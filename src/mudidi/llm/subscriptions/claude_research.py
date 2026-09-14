"""Direct Claude.ai subscription routing.

This adapter is separate from MUDIDI's API-key/LiteLLM path. It uses the
provider-owned Claude.ai OAuth flow and Messages-compatible subscription
surface. Credentials remain in the encrypted subscription store.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import platform
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request
from uuid import uuid4
import zlib

from mudidi.llm.reasoning import choose_supported_effort, resolve_reasoning_profile
from mudidi.llm.subscriptions.oauth import (
    LoopbackOAuthReceiver,
    OAuthCallback,
    OAuthHttpClient,
    _build_no_redirect_opener,
    begin_login_transaction,
    validate_login_transaction,
    validate_loopback_redirect_uri,
    validate_provider_url,
)
from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.storage import SubscriptionStore, json_values_equal
from mudidi.llm.subscriptions.types import (
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    SubscriptionAuthError,
    SubscriptionCredential,
    SubscriptionError,
    SubscriptionLoginTransaction,
    SubscriptionPolicyError,
    SubscriptionProvider,
    SubscriptionStatus,
    SubscriptionTokenExpired,
    SubscriptionTransportError,
    SubscriptionModel,
    SubscriptionUnsupportedRequest,
    resolve_subscription_model,
)

# These values track Oh My Pi's Claude Code-compatible OAuth transport. They
# are provider-owned implementation details rather than public API-key config.
_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_AUTHORIZATION_ENDPOINT = "https://claude.ai/oauth/authorize"
_TOKEN_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
_BOOTSTRAP_ENDPOINT = "https://api.anthropic.com/api/claude_cli/bootstrap"
_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages?beta=true"
_MODELS_ENDPOINT = "https://api.anthropic.com/v1/models?limit=1000&beta=true"
_CLAUDE_CODE_VERSION = "2.1.257"
_CLAUDE_CODE_SDK_VERSION = "0.112.1"
_OAUTH_USER_AGENT = (
    f"anthropic-sdk-typescript/{_CLAUDE_CODE_SDK_VERSION} userOAuthProvider"
)
_MESSAGES_USER_AGENT = f"claude-cli/{_CLAUDE_CODE_VERSION} (external, cli)"
_SCOPE = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)
_REDIRECT_PATH = "/callback"
_REDIRECT_URI = "http://localhost:54545/callback"
_DEFAULT_ACCOUNT_LABEL = "Claude.ai subscription"
_IDENTITY_SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."
_DEFAULT_MAX_TOKENS = 16_384
_DEFAULT_TIMEOUT = 120.0
_MAX_TIMEOUT = 300.0
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1_048_576
_MAX_TEXT_LENGTH = 1_048_576
_MAX_IDENTIFIER_LENGTH = 512
_MAX_EXPIRES_IN_SECONDS = 1_000_000_000
_TOKEN_EXPIRY_SKEW_SECONDS = 300
_MAX_MODEL_PAGES = 20
_CCH_SEED = 0x4D659218E32A3268
_CCH_PLACEHOLDER = "cch=00000"
_CCH_MARKER = b'"system":[{"type":"text","text":"x-anthropic-billing-header:'
_CCH_SEARCH_WINDOW = 150

_OAUTH_HOSTS = frozenset({"claude.ai", "api.anthropic.com"})
_MESSAGES_HOSTS = frozenset({"api.anthropic.com"})

_UTILITY_BETAS = (
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "thinking-token-count-2026-05-13",
    "context-management-2025-06-27",
    "prompt-caching-scope-2026-01-05",
    "structured-outputs-2025-12-15",
)
_AGENT_BETAS = (
    "claude-code-20250219",
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "thinking-token-count-2026-05-13",
    "context-management-2025-06-27",
    "prompt-caching-scope-2026-01-05",
    "mid-conversation-system-2026-04-07",
    "effort-2025-11-24",
    "fallback-credit-2026-06-01",
)

_PLATFORM_SYSTEM = platform.system().lower()
_PLATFORM_MACHINE = platform.machine().lower()
_STAINLESS_OS = {
    "darwin": "MacOS",
    "windows": "Windows",
    "linux": "Linux",
    "freebsd": "FreeBSD",
}.get(_PLATFORM_SYSTEM, f"Other::{_PLATFORM_SYSTEM}")
_STAINLESS_ARCH = {
    "amd64": "x64",
    "x86_64": "x64",
    "x64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "386": "x86",
    "x86": "x86",
    "i386": "x86",
}.get(_PLATFORM_MACHINE, f"other::{_PLATFORM_MACHINE}")

# OAuth access tokens always use bearer auth; an X-Api-Key is never emitted.
_MESSAGES_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "User-Agent": _MESSAGES_USER_AGENT,
    "X-Stainless-Arch": _STAINLESS_ARCH,
    "X-Stainless-Lang": "js",
    "X-Stainless-OS": _STAINLESS_OS,
    "X-Stainless-Package-Version": _CLAUDE_CODE_SDK_VERSION,
    "X-Stainless-Retry-Count": "0",
    "X-Stainless-Runtime": "node",
    "X-Stainless-Runtime-Version": "v26.3.0",
    "X-Stainless-Timeout": "600",
    "anthropic-version": "2023-06-01",
    "anthropic-dangerous-direct-browser-access": "true",
    "x-app": "cli",
}

_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}
_TEXT_BLOCK_TYPES = frozenset({None, "text", "input_text", "output_text"})
_IMAGE_BLOCK_TYPES = frozenset({"image", "image_url", "input_image"})
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_PDF_MIME_TYPE = "application/pdf"
_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "title",
        "description",
        "default",
    }
)
_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


_NO_REDIRECT_OPENER = _build_no_redirect_opener()


_MASK_64 = (1 << 64) - 1
_XXH64_PRIME_1 = 11_400_714_785_074_694_791
_XXH64_PRIME_2 = 14_029_467_366_897_019_727
_XXH64_PRIME_3 = 1_609_587_929_392_839_161
_XXH64_PRIME_4 = 9_650_029_242_287_828_579
_XXH64_PRIME_5 = 2_870_177_450_012_600_261


def _rotate_left_64(value: int, count: int) -> int:
    return ((value << count) | (value >> (64 - count))) & _MASK_64


def _xxhash64_round(accumulator: int, lane: int) -> int:
    accumulator = (accumulator + lane * _XXH64_PRIME_2) & _MASK_64
    accumulator = _rotate_left_64(accumulator, 31)
    return (accumulator * _XXH64_PRIME_1) & _MASK_64


def _xxhash64(data: bytes, seed: int = 0) -> int:
    """Return the XXH64 digest used by Claude Code's billing attestation."""

    length = len(data)
    offset = 0
    if length >= 32:
        accumulator_1 = (seed + _XXH64_PRIME_1 + _XXH64_PRIME_2) & _MASK_64
        accumulator_2 = (seed + _XXH64_PRIME_2) & _MASK_64
        accumulator_3 = seed & _MASK_64
        accumulator_4 = (seed - _XXH64_PRIME_1) & _MASK_64
        while offset <= length - 32:
            accumulator_1 = _xxhash64_round(
                accumulator_1,
                int.from_bytes(data[offset : offset + 8], "little"),
            )
            accumulator_2 = _xxhash64_round(
                accumulator_2,
                int.from_bytes(data[offset + 8 : offset + 16], "little"),
            )
            accumulator_3 = _xxhash64_round(
                accumulator_3,
                int.from_bytes(data[offset + 16 : offset + 24], "little"),
            )
            accumulator_4 = _xxhash64_round(
                accumulator_4,
                int.from_bytes(data[offset + 24 : offset + 32], "little"),
            )
            offset += 32
        digest = (
            _rotate_left_64(accumulator_1, 1)
            + _rotate_left_64(accumulator_2, 7)
            + _rotate_left_64(accumulator_3, 12)
            + _rotate_left_64(accumulator_4, 18)
        ) & _MASK_64
        for accumulator in (
            accumulator_1,
            accumulator_2,
            accumulator_3,
            accumulator_4,
        ):
            digest ^= _xxhash64_round(0, accumulator)
            digest = (digest * _XXH64_PRIME_1 + _XXH64_PRIME_4) & _MASK_64
    else:
        digest = (seed + _XXH64_PRIME_5) & _MASK_64

    digest = (digest + length) & _MASK_64
    while offset <= length - 8:
        lane = int.from_bytes(data[offset : offset + 8], "little")
        digest ^= _xxhash64_round(0, lane)
        digest = (
            _rotate_left_64(digest, 27) * _XXH64_PRIME_1 + _XXH64_PRIME_4
        ) & _MASK_64
        offset += 8
    if offset <= length - 4:
        lane = int.from_bytes(data[offset : offset + 4], "little")
        digest ^= (lane * _XXH64_PRIME_1) & _MASK_64
        digest = (
            _rotate_left_64(digest, 23) * _XXH64_PRIME_2 + _XXH64_PRIME_3
        ) & _MASK_64
        offset += 4
    while offset < length:
        digest ^= (data[offset] * _XXH64_PRIME_5) & _MASK_64
        digest = (_rotate_left_64(digest, 11) * _XXH64_PRIME_1) & _MASK_64
        offset += 1

    digest ^= digest >> 33
    digest = (digest * _XXH64_PRIME_2) & _MASK_64
    digest ^= digest >> 29
    digest = (digest * _XXH64_PRIME_3) & _MASK_64
    digest ^= digest >> 32
    return digest & _MASK_64


def _create_billing_header(first_user_text: str) -> str:
    utf16 = first_user_text.encode("utf-16-le", errors="surrogatepass")
    fingerprint_chars = "".join(
        (
            utf16[index * 2 : index * 2 + 2].decode(
                "utf-16-le",
                errors="replace",
            )
            if index * 2 + 2 <= len(utf16)
            else "0"
        )
        for index in (4, 7, 20)
    )
    fingerprint = hashlib.sha256(
        f"59cf53e54c78{fingerprint_chars}{_CLAUDE_CODE_VERSION}".encode()
    ).hexdigest()[:3]
    return (
        "x-anthropic-billing-header: "
        f"cc_version={_CLAUDE_CODE_VERSION}.{fingerprint}; "
        f"cc_entrypoint=cli; {_CCH_PLACEHOLDER};"
    )


def _patch_billing_attestation(data: bytes) -> bytes:
    marker_index = data.find(_CCH_MARKER)
    if marker_index < 0:
        return data
    search_from = marker_index + len(_CCH_MARKER)
    placeholder = _CCH_PLACEHOLDER.encode()
    placeholder_index = data.find(
        placeholder,
        search_from,
        search_from + _CCH_SEARCH_WINDOW,
    )
    if placeholder_index < 0:
        return data
    attestation = f"{_xxhash64(data, _CCH_SEED) & 0xFFFFF:05x}".encode()
    patched = bytearray(data)
    patched[placeholder_index + 4 : placeholder_index + 9] = attestation
    return bytes(patched)


def _messages_beta_header(body: Mapping[str, Any]) -> str:
    betas = list(_AGENT_BETAS if "thinking" in body else _UTILITY_BETAS)
    if "output_config" in body and "structured-outputs-2025-12-15" not in betas:
        betas.append("structured-outputs-2025-12-15")
    return ",".join(betas)


def _decode_sse_message(body: bytes) -> Mapping[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _error(
            SubscriptionTransportError,
            "Claude response is not valid UTF-8",
            reason="malformed_response",
        ) from None

    events: list[Mapping[str, Any]] = []
    data_lines: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line:
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            continue
        if not data_lines:
            continue
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        if raw_data == "[DONE]":
            continue
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            raise _error(
                SubscriptionTransportError,
                "Claude streaming response is malformed",
                reason="malformed_response",
            ) from None
        if not isinstance(event, Mapping):
            raise _error(
                SubscriptionTransportError,
                "Claude streaming response is malformed",
                reason="malformed_response",
            )
        events.append(event)

    message: dict[str, Any] | None = None
    content: dict[int, dict[str, Any]] = {}
    saw_message_stop = False
    for event in events:
        event_type = event.get("type")
        if event_type == "error":
            return event
        if event_type == "message_stop":
            saw_message_stop = True
            continue
        if event_type == "message_start":
            raw_message = event.get("message")
            if isinstance(raw_message, Mapping):
                message = dict(raw_message)
                initial_content = raw_message.get("content")
                if isinstance(initial_content, Sequence) and not isinstance(
                    initial_content,
                    (str, bytes, bytearray),
                ):
                    content = {
                        index: dict(block)
                        for index, block in enumerate(initial_content)
                        if isinstance(block, Mapping)
                    }
            continue
        if message is None:
            continue
        raw_index = event.get("index")
        index = (
            raw_index
            if isinstance(raw_index, int) and not isinstance(raw_index, bool)
            else None
        )
        if event_type == "content_block_start" and index is not None:
            raw_block = event.get("content_block")
            if isinstance(raw_block, Mapping):
                content[index] = dict(raw_block)
            continue
        if event_type == "content_block_delta" and index is not None:
            delta = event.get("delta")
            if not isinstance(delta, Mapping):
                continue
            block = content.setdefault(index, {"type": "text", "text": ""})
            delta_type = delta.get("type")
            if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                block["text"] = f"{block.get('text', '')}{delta['text']}"
            elif delta_type == "thinking_delta" and isinstance(
                delta.get("thinking"), str
            ):
                block["thinking"] = f"{block.get('thinking', '')}{delta['thinking']}"
            elif delta_type == "signature_delta" and isinstance(
                delta.get("signature"), str
            ):
                block["signature"] = f"{block.get('signature', '')}{delta['signature']}"
            continue
        if event_type == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping):
                for key in ("stop_reason", "stop_sequence"):
                    if key in delta:
                        message[key] = delta[key]
            usage = event.get("usage")
            if isinstance(usage, Mapping):
                existing_usage = message.get("usage")
                merged_usage = (
                    dict(existing_usage) if isinstance(existing_usage, Mapping) else {}
                )
                merged_usage.update(usage)
                message["usage"] = merged_usage

    if message is None or not saw_message_stop:
        raise _error(
            SubscriptionTransportError,
            "Claude streaming response did not contain a complete message",
            reason="malformed_response",
        )
    message["content"] = [content[index] for index in sorted(content)]
    return message


def _decode_response_message(body: bytes) -> Mapping[str, Any]:
    stripped = body.lstrip()
    if stripped.startswith((b"event:", b"data:")):
        return _decode_sse_message(body)
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise _error(
            SubscriptionTransportError,
            "Claude response is not valid JSON",
            reason="malformed_response",
        ) from None
    if not isinstance(decoded, Mapping):
        raise _error(
            SubscriptionTransportError,
            "Claude response is malformed",
            reason="malformed_response",
        )
    return decoded


def _error(
    error_type: type[SubscriptionError],
    message: str,
    *,
    category: str | None = None,
    status: int | None = None,
    reason: str | None = None,
    credential: SubscriptionCredential | None = None,
    secret_values: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> SubscriptionError:
    error_metadata: dict[str, Any] = dict(metadata or {})
    error_metadata["research_only"] = True
    if reason is not None:
        error_metadata.setdefault("reason", reason)
    secrets = [secret for secret in secret_values if isinstance(secret, str) and secret]
    if credential is not None:
        secrets.append(credential.access_token.get_secret_value())
        if credential.refresh_token is not None:
            secrets.append(credential.refresh_token.get_secret_value())
    return error_type(
        message,
        provider=SubscriptionProvider.CLAUDE,
        category=category,
        status=status,
        metadata=error_metadata,
        secret_values=secrets,
    )


def _normalize_shared_error(
    error: SubscriptionError,
    *,
    reason: str | None = None,
    credential: SubscriptionCredential | None = None,
) -> SubscriptionError:
    """Re-home errors crossing a shared helper boundary under Claude policy."""

    if (
        error.provider is SubscriptionProvider.CLAUDE
        and error.metadata.get("research_only") is True
    ):
        return error
    if isinstance(error, SubscriptionPolicyError) or error.status == 403:
        error_type: type[SubscriptionError] = SubscriptionPolicyError
        message = "Claude subscription request was rejected by provider policy"
    elif isinstance(error, SubscriptionTokenExpired):
        error_type = SubscriptionTokenExpired
        message = "Claude subscription authentication has expired"
    elif isinstance(error, SubscriptionAuthError):
        error_type = SubscriptionAuthError
        message = "Claude subscription authentication failed"
    elif isinstance(error, SubscriptionUnsupportedRequest):
        error_type = SubscriptionUnsupportedRequest
        message = "Claude subscription request is unsupported"
    else:
        error_type = SubscriptionTransportError
        message = "Claude subscription transport failed"
    selected_reason = reason
    if selected_reason is None:
        candidate = error.metadata.get("reason")
        selected_reason = candidate if isinstance(candidate, str) else "shared_error"
    return _error(
        error_type,
        message,
        status=error.status,
        reason=selected_reason,
        credential=credential,
    )


def _bounded_timeout(value: float | int | None) -> float:
    selected = _DEFAULT_TIMEOUT if value is None else float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise ValueError("timeout must be a finite positive number")
    return min(selected, _MAX_TIMEOUT)


def _as_plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _as_plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain_json(item) for item in value]
    return value


def _safe_string(value: Any, *, maximum: int = _MAX_IDENTIFIER_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    selected = value.strip()
    if not selected or len(selected) > maximum:
        return None
    if any(character in selected for character in "\r\n\x00"):
        return None
    return selected


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return value if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value)
        except ValueError:
            return None
        if not math.isfinite(parsed) or parsed < 0:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _token_secrets(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    values: list[str] = []
    for key in ("access_token", "refresh_token", "id_token"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return values


def _schema_is_supported(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if any(
        not isinstance(key, str) or key not in _SUPPORTED_SCHEMA_KEYWORDS
        for key in schema
    ):
        return False

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        if schema_type not in _SCHEMA_TYPES:
            return False
    elif isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes, bytearray)
    ):
        if not schema_type or any(item not in _SCHEMA_TYPES for item in schema_type):
            return False
    elif schema_type is not None:
        return False

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            return False
        if any(
            not isinstance(key, str) or not _schema_is_supported(child)
            for key, child in properties.items()
        ):
            return False

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, Sequence) or isinstance(
            required, (str, bytes, bytearray)
        ):
            return False
        if any(not isinstance(key, str) for key in required):
            return False

    items = schema.get("items")
    if items is not None and not _schema_is_supported(items):
        return False

    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        return False
    object_declared = (
        schema_type == "object"
        or (
            isinstance(schema_type, Sequence)
            and not isinstance(schema_type, (str, bytes, bytearray))
            and "object" in schema_type
        )
        or properties is not None
    )
    if object_declared and additional is not False:
        return False

    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray))
    ):
        return False
    return True


_json_values_equal = json_values_equal


def _schema_matches(value: Any, schema: Mapping[str, Any]) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if "const" in schema and not _json_values_equal(value, schema["const"]):
        return False

    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray)):
        if not any(_json_values_equal(value, candidate) for candidate in enum):
            return False

    schema_type = schema.get("type")
    if isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes, bytearray)
    ):
        if not any(_schema_matches(value, {"type": item}) for item in schema_type):
            return False
    elif isinstance(schema_type, str):
        matches = {
            "object": isinstance(value, Mapping),
            "array": isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray)),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        if not matches.get(schema_type, False):
            return False

    if isinstance(value, Mapping):
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(
            required, (str, bytes, bytearray)
        ):
            if any(not isinstance(key, str) or key not in value for key in required):
                return False
        properties = schema.get("properties")
        known = properties if isinstance(properties, Mapping) else {}
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                if (
                    key in value
                    and isinstance(child, Mapping)
                    and not _schema_matches(value[key], child)
                ):
                    return False
        if schema.get("additionalProperties") is False and any(
            key not in known for key in value
        ):
            return False

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = schema.get("items")
        if isinstance(items, Mapping) and any(
            not _schema_matches(item, items) for item in value
        ):
            return False
    return True


def _require_supported_schema(schema: Any) -> None:
    if not _schema_is_supported(schema):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude structured completion schema uses unsupported keywords",
            reason="unsupported_schema_keyword",
        )


def _coerce_request(
    request: CompletionRequest | Mapping[str, Any],
) -> CompletionRequest:
    if isinstance(request, CompletionRequest):
        return request
    try:
        return CompletionRequest.model_validate(request)
    except Exception:
        raise _error(
            SubscriptionUnsupportedRequest,
            "completion request shape is unsupported",
            reason="invalid_request",
        ) from None


def _is_https_image_url(value: Any) -> str | None:
    selected = _safe_string(value, maximum=_MAX_TEXT_LENGTH)
    if selected is None:
        return None
    try:
        parsed = urlsplit(selected)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    return selected


def _base64_image_source(
    data: Any,
    mime_type: Any,
) -> dict[str, str] | None:
    encoded = _safe_string(data, maximum=_MAX_TEXT_LENGTH)
    selected_mime = _safe_string(mime_type, maximum=128)
    if (
        encoded is None
        or selected_mime is None
        or selected_mime.lower() not in _IMAGE_MIME_TYPES
    ):
        return None
    selected_mime = selected_mime.lower()
    try:
        base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        return None
    return {"type": "base64", "media_type": selected_mime, "data": encoded}


def _image_block(part: Mapping[str, Any]) -> dict[str, Any]:
    source = part.get("source")
    if isinstance(source, Mapping):
        source_type = source.get("type")
        if source_type == "base64":
            normalized = _base64_image_source(
                source.get("data"), source.get("media_type", source.get("mime_type"))
            )
            if normalized is None:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude image input is malformed",
                    reason="image_input_unsupported",
                )
            return {"type": "image", "source": normalized}
        if source_type == "url":
            url = _is_https_image_url(source.get("url"))
            if url is None:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude image URL is malformed",
                    reason="image_input_unsupported",
                )
            return {"type": "image", "source": {"type": "url", "url": url}}
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude image source is unsupported",
            reason="image_input_unsupported",
        )

    image_url = part.get("image_url")
    if isinstance(image_url, Mapping):
        url = image_url.get("url")
        mime_type = image_url.get("mime_type", image_url.get("media_type"))
    else:
        url = image_url
        mime_type = part.get("mime_type", part.get("media_type"))
    selected_url = _safe_string(url, maximum=_MAX_TEXT_LENGTH)
    if selected_url is None:
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude image input is malformed",
            reason="image_input_unsupported",
        )
    if selected_url.startswith("data:"):
        header, separator, encoded = selected_url.partition(",")
        parameters = [part.strip().lower() for part in header[5:].split(";")]
        if (
            not separator
            or len(parameters) < 2
            or "base64" not in parameters[1:]
            or not parameters[0]
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude image data URI is malformed",
                reason="image_input_unsupported",
            )
        parsed_mime = _safe_string(header[5:].split(";", 1)[0], maximum=128)
        explicit_mime = _safe_string(mime_type, maximum=128)
        if parsed_mime is None or (
            mime_type is not None
            and (explicit_mime is None or explicit_mime.lower() != parsed_mime.lower())
        ):
            normalized = None
        else:
            normalized = _base64_image_source(encoded, parsed_mime)
        if normalized is None:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude image data URI is malformed",
                reason="image_input_unsupported",
            )
        return {"type": "image", "source": normalized}

    remote = _is_https_image_url(selected_url)
    if remote is None:
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude image URL is malformed",
            reason="image_input_unsupported",
        )
    return {"type": "image", "source": {"type": "url", "url": remote}}


def _document_block(part: Mapping[str, Any]) -> dict[str, Any]:
    file_value = part.get("file")
    if not isinstance(file_value, Mapping):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude file input is malformed",
            reason="file_input_unsupported",
        )

    mime_values = [
        value
        for mapping in (part, file_value)
        for key in ("format", "mime_type", "mimeType", "media_type")
        if (value := mapping.get(key)) is not None
    ]
    if not mime_values or any(
        not isinstance(value, str)
        or value.strip().lower() != _PDF_MIME_TYPE
        for value in mime_values
    ):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude file input must be an application/pdf document",
            reason="file_input_unsupported",
        )

    sources = [
        (key, file_value[key])
        for key in ("file_data", "file_id", "file_url")
        if key in file_value
    ]
    if len(sources) != 1:
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude file input must contain exactly one data source",
            reason="file_input_unsupported",
        )
    source_kind, source_value = sources[0]

    if source_kind == "file_data":
        if not isinstance(source_value, str):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude PDF data URI is malformed",
                reason="file_input_unsupported",
            )
        header, separator, encoded = source_value.partition(",")
        parameters = header[5:].split(";") if header.lower().startswith("data:") else []
        if (
            not separator
            or len(parameters) != 2
            or parameters[0].lower() != _PDF_MIME_TYPE
            or parameters[1].lower() != "base64"
            or not encoded
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude PDF data URI is malformed",
                reason="file_input_unsupported",
            )
        try:
            base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeError):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude PDF data URI is malformed",
                reason="file_input_unsupported",
            ) from None
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": _PDF_MIME_TYPE,
                "data": encoded,
            },
        }

    remote = _is_https_image_url(source_value)
    if remote is not None:
        return {
            "type": "document",
            "source": {"type": "url", "url": remote},
        }
    selected_reference = _safe_string(source_value, maximum=_MAX_IDENTIFIER_LENGTH)
    if (
        source_kind == "file_id"
        and selected_reference is not None
        and selected_reference.startswith(("file-", "file_"))
        and all(
            character.isalnum() or character in "-_"
            for character in selected_reference
        )
    ):
        return {
            "type": "document",
            "source": {"type": "file", "file_id": selected_reference},
        }
    raise _error(
        SubscriptionUnsupportedRequest,
        "Claude file reference is unsupported",
        reason="file_input_unsupported",
    )


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        if not content:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude message content is empty",
                reason="unsupported_message_content",
            )
        return [{"type": "text", "text": content}]
    if isinstance(content, Mapping):
        content = [content]
    if not isinstance(content, Sequence) or isinstance(
        content, (str, bytes, bytearray)
    ):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude message content is unsupported",
            reason="unsupported_message_content",
        )

    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude message content is unsupported",
                reason="unsupported_message_content",
            )
        kind = part.get("type")
        if kind == "file":
            blocks.append(_document_block(part))
            continue
        if kind in _IMAGE_BLOCK_TYPES or "image_url" in part or "source" in part:
            if kind not in _IMAGE_BLOCK_TYPES and "source" not in part:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude message content is unsupported",
                    reason="unsupported_message_content",
                )
            blocks.append(_image_block(part))
            continue
        if kind not in _TEXT_BLOCK_TYPES:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude message content is unsupported",
                reason="unsupported_message_content",
            )
        text = part.get("text")
        if not isinstance(text, str) or not text:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude message content is unsupported",
                reason="unsupported_message_content",
            )
        blocks.append({"type": "text", "text": text})
    if not blocks:
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude message content is empty",
            reason="unsupported_message_content",
        )
    return blocks


def _text_blocks(content: Any) -> str:
    blocks = _content_blocks(content)
    if any(block.get("type") != "text" for block in blocks):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Claude system messages support text content only",
            reason="unsupported_message_content",
        )
    return "".join(str(block["text"]) for block in blocks)


def _extract_usage(response: Mapping[str, Any]) -> dict[str, int | float | None]:
    raw_usage = response.get("usage")
    if raw_usage is None:
        return {}
    if not isinstance(raw_usage, Mapping):
        raise _error(
            SubscriptionTransportError,
            "Claude response usage is malformed",
            reason="malformed_response",
        )

    usage: dict[str, int | float | None] = {}
    input_tokens = _number(raw_usage.get("input_tokens"))
    output_tokens = _number(raw_usage.get("output_tokens"))
    total_tokens = _number(raw_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens

    cached = _number(raw_usage.get("cached_tokens"))
    if cached is None:
        cached = _number(raw_usage.get("cache_read_input_tokens"))
    if cached is not None:
        usage["cached_tokens"] = cached

    reasoning: int | float | None = None
    details = raw_usage.get("output_tokens_details")
    if isinstance(details, Mapping):
        reasoning = _number(details.get("thinking_tokens"))
        if reasoning is None:
            reasoning = _number(details.get("reasoning_tokens"))
    if reasoning is None:
        reasoning = _number(raw_usage.get("reasoning_tokens"))
    if reasoning is None:
        reasoning = _number(raw_usage.get("thinking_tokens"))
    if reasoning is not None:
        usage["reasoning_tokens"] = reasoning
    return usage


def _response_error_kind(response: Mapping[str, Any]) -> str | None:
    error = response.get("error")
    if not isinstance(error, Mapping):
        return None
    kind = error.get("type")
    return kind if isinstance(kind, str) else None


class ClaudeResearchBackend:
    """Provider-neutral backend for the Claude.ai subscription surface."""

    login_redirect_uri = _REDIRECT_URI

    def __init__(
        self,
        store: SubscriptionStore | str | os.PathLike[str] | Any | None = None,
        *,
        store_path: str | os.PathLike[str] | None = None,
        database_path: str | os.PathLike[str] | None = None,
        oauth_client: Any | None = None,
        http_client: Any | None = None,
        transport: Any | None = None,
        fetch: Callable[..., Any] | None = None,
        request: Callable[..., Any] | None = None,
        opener: Callable[[str], Any] | Any | None = None,
        listener: Callable[..., Any] | Any | None = None,
        receiver: Any | None = None,
        receiver_factory: Callable[..., Any] | None = None,
        timeout: float | int | None = None,
        callback_timeout: float | int | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if store_path is not None and database_path is not None:
            raise TypeError("pass only one of store_path or database_path")
        selected_path = store_path if store_path is not None else database_path
        if store is not None and selected_path is not None:
            raise TypeError("pass a store or store_path, not both")
        if store is None and selected_path is not None:
            store = SubscriptionStore(selected_path)
        elif isinstance(store, (str, os.PathLike, Path)):
            store = SubscriptionStore(store)
        if store is None:
            raise ValueError(
                "an encrypted subscription store or test store is required"
            )

        if fetch is not None and request is not None:
            raise TypeError("pass only one of fetch or request")
        selected_max = int(max_response_bytes)
        if selected_max <= 0:
            raise ValueError("max_response_bytes must be positive")

        self._store = store
        self._timeout = _bounded_timeout(timeout)
        self._callback_timeout = callback_timeout
        self._max_response_bytes = min(selected_max, _DEFAULT_MAX_RESPONSE_BYTES)
        self._opener = opener
        self._listener = listener
        self._receiver = receiver
        self._receiver_factory = receiver_factory
        self._fetch = fetch if fetch is not None else request
        self._transport = transport if transport is not None else http_client
        self._session_id = str(uuid4()).lower()
        self._oauth = (
            OAuthHttpClient(
                fetch=self._fetch,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
                allowed_hosts=set(_OAUTH_HOSTS),
                user_agent=_OAUTH_USER_AGENT,
            )
            if oauth_client is None
            else oauth_client
        )

    @property
    def provider(self) -> SubscriptionProvider:
        """Return the Claude provider identifier."""

        return SubscriptionProvider.CLAUDE

    @property
    def capabilities(self) -> BackendCapabilities:
        """Return provider-neutral Claude capability flags."""

        return BackendCapabilities(
            image_input=True,
            structured_output=True,
            streaming=False,
            cancellation=False,
            usage=True,
            reasoning=True,
            model_discovery=False,
        )

    def __repr__(self) -> str:
        return "ClaudeResearchBackend(provider='claude', subscription=True)"

    def _status_metadata(
        self,
        credential: SubscriptionCredential | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if credential is not None:
            metadata.update(credential.redacted_metadata)
            metadata.pop("policy_warning", None)
            secrets = [credential.access_token.get_secret_value()]
            if credential.refresh_token is not None:
                secrets.append(credential.refresh_token.get_secret_value())
            account_label = metadata.get("account_label")
            if isinstance(account_label, str) and any(
                secret and secret in account_label for secret in secrets
            ):
                metadata["account_label"] = _DEFAULT_ACCOUNT_LABEL
            account_id = metadata.get("account_id")
            if isinstance(account_id, str) and any(
                secret and secret in account_id for secret in secrets
            ):
                metadata["account_id"] = None
        metadata["subscription"] = True
        return metadata

    def build_authorization_url(
        self,
        challenge: PkceChallenge,
        redirect_uri: str,
    ) -> str:
        """Build a non-secret Claude.ai OAuth authorization URL."""

        if not isinstance(challenge, PkceChallenge):
            raise TypeError("challenge must be a PkceChallenge")
        try:
            parsed_redirect = validate_loopback_redirect_uri(redirect_uri)
            if parsed_redirect.path not in {
                _REDIRECT_PATH,
                "/subscriptions/claude/callback",
            }:
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth callback URI is invalid",
                    reason="invalid_redirect_uri",
                )
        except SubscriptionError as exc:
            raise _normalize_shared_error(exc, reason="invalid_redirect_uri") from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth callback URI is invalid",
                reason="invalid_redirect_uri",
            ) from None

        parameters = {
            "code": "true",
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": _SCOPE,
            **challenge.authorization_parameters(),
        }
        builder = getattr(self._oauth, "build_authorization_url", None)
        try:
            if callable(builder):
                result = str(builder(_AUTHORIZATION_ENDPOINT, parameters))
            else:
                validate_provider_url(
                    _AUTHORIZATION_ENDPOINT, allowed_hosts=set(_OAUTH_HOSTS)
                )
                result = f"{_AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"
            validate_provider_url(result, allowed_hosts=set(_OAUTH_HOSTS))
            return result
        except SubscriptionError as exc:
            raise _normalize_shared_error(
                exc, reason="authorization_url_error"
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Claude authorization endpoint is unavailable",
                reason="invalid_authorization_endpoint",
            ) from None

    authorization_url = build_authorization_url

    def _make_receiver(self, challenge: PkceChallenge) -> Any:
        factory = self._receiver_factory
        if factory is None and self._receiver is not None and callable(self._receiver):
            has_receive = any(
                hasattr(self._receiver, name)
                for name in ("receive", "listen", "run", "wait")
            )
            if not has_receive:
                factory = self._receiver
        if factory is not None:
            kwargs = {
                "expected_state": challenge.state,
                "pkce": challenge,
                "path": _REDIRECT_PATH,
                "timeout": self._callback_timeout,
                "listener": self._listener,
                "opener": self._opener,
            }
            try:
                return factory(**kwargs)
            except TypeError:
                try:
                    return factory(expected_state=challenge.state, pkce=challenge)
                except TypeError:
                    return factory(challenge.state)
        if self._receiver is not None:
            return self._receiver
        return LoopbackOAuthReceiver(
            expected_state=challenge.state,
            pkce=challenge,
            host="127.0.0.1",
            port=54545,
            path=_REDIRECT_PATH,
            redirect_host="localhost",
            allow_fixed_port=True,
            timeout=self._callback_timeout,
            listener=self._listener,
            opener=self._opener,
        )

    def _receive_callback(self, receiver: Any, authorization_url: str) -> Any:
        method = next(
            (
                getattr(receiver, name, None)
                for name in ("receive", "listen", "run", "wait")
                if hasattr(receiver, name)
            ),
            None,
        )
        if not callable(method):
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth callback receiver is unavailable",
                reason="callback_receiver_unavailable",
            )
        kwargs = {
            "opener": self._opener,
            "listener": self._listener,
            "timeout": self._callback_timeout,
        }
        try:
            return method(authorization_url, **kwargs)
        except TypeError:
            try:
                return method(authorization_url)
            except SubscriptionError as exc:
                raise _normalize_shared_error(exc, reason="callback_error") from None
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth callback could not be validated",
                    reason="invalid_callback",
                ) from None
        except SubscriptionError as exc:
            raise _normalize_shared_error(exc, reason="callback_error") from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth callback could not be validated",
                reason="invalid_callback",
            ) from None

    def _validated_callback(
        self,
        value: Any,
        challenge: PkceChallenge,
        receiver: Any,
    ) -> OAuthCallback:
        callback = value
        if not isinstance(callback, OAuthCallback):
            handler = getattr(receiver, "handle_callback", None)
            if callable(handler):
                try:
                    callback = handler(value)
                except SubscriptionError as exc:
                    raise _normalize_shared_error(
                        exc, reason="callback_error"
                    ) from None
                except Exception:
                    raise _error(
                        SubscriptionAuthError,
                        "Claude OAuth callback could not be validated",
                        reason="invalid_callback",
                    ) from None

        if not isinstance(callback, OAuthCallback) and isinstance(
            callback, (str, bytes)
        ):
            try:
                raw_callback = (
                    callback.decode("ascii")
                    if isinstance(callback, bytes)
                    else callback
                )
                parsed = urlsplit(raw_callback)
                if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
                    if (
                        parsed.scheme.lower() != "http"
                        or parsed.hostname != "127.0.0.1"
                    ):
                        raise ValueError("callback authority is not loopback")
                    if parsed.path != _REDIRECT_PATH or parsed.fragment:
                        raise ValueError("callback path is invalid")
                    query = parsed.query
                else:
                    query = raw_callback.lstrip("?")
                values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
                if (
                    len(values.get("code", [])) == 1
                    and len(values.get("state", [])) == 1
                ):
                    callback = OAuthCallback(
                        code=values["code"][0], state=values["state"][0]
                    )
            except (UnicodeDecodeError, ValueError):
                callback = value

        if not isinstance(callback, OAuthCallback):
            code = getattr(callback, "code", None)
            state = getattr(callback, "state", None)
            if isinstance(callback, Mapping):
                code = callback.get("code")
                state = callback.get("state")
            if (
                not isinstance(code, str)
                or not code.strip()
                or len(code) > 4096
                or not isinstance(state, str)
                or not state
                or len(state) > 512
            ):
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth callback is invalid",
                    reason="invalid_callback",
                )
            try:
                callback = OAuthCallback(code=code, state=state)
            except (TypeError, ValueError):
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth callback is invalid",
                    reason="invalid_callback",
                ) from None

        try:
            matches = hmac.compare_digest(callback.state, challenge.state)
        except TypeError:
            matches = False
        if not matches:
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth callback state is invalid",
                reason="state_mismatch",
            )
        if not callback.code.strip():
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth callback did not contain an authorization code",
                reason="missing_authorization_code",
            )
        return callback

    def begin_login(self, redirect_uri: str) -> SubscriptionLoginTransaction:
        """Create a loopback PKCE transaction without waiting for a callback."""

        return begin_login_transaction(self.build_authorization_url, redirect_uri)

    def complete_login(
        self,
        code: str,
        state: str,
        transaction: SubscriptionLoginTransaction,
    ) -> SubscriptionCredential:
        """Exchange a callback through Claude's JSON OAuth contract."""

        validate_login_transaction(
            transaction,
            code=code,
            state=state,
            provider=self.provider,
        )
        json_exchange = getattr(self._oauth, "request_json_token", None)
        if not callable(json_exchange):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude OAuth JSON token exchange is unavailable",
                reason="token_exchange_unavailable",
            )
        token_parameters = {
            "grant_type": "authorization_code",
            "client_id": _CLIENT_ID,
            "code": code,
            "state": state,
            "redirect_uri": transaction.redirect_uri,
            "code_verifier": transaction.verifier.get_secret_value(),
        }
        try:
            with self._provider_operation():
                payload = json_exchange(
                    _TOKEN_ENDPOINT,
                    token_parameters,
                    provider=self.provider,
                )
                credential = self._credential_from_token_response(payload)
                self._save(credential)
                return credential
        except SubscriptionError as exc:
            raise _normalize_shared_error(exc, reason="oauth_error") from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token exchange failed",
                reason="token_exchange_failed",
            ) from None

    def login(self) -> SubscriptionCredential:
        """Run the opt-in Claude.ai OAuth authorization-code flow."""

        challenge = PkceChallenge.generate()
        receiver: Any | None = None
        try:
            try:
                receiver = self._make_receiver(challenge)
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionTransportError,
                    "Claude OAuth callback listener is unavailable",
                    reason="callback_listener_failed",
                ) from None
            redirect_uri = getattr(receiver, "redirect_uri", None)
            if not isinstance(redirect_uri, str) or not redirect_uri:
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth callback URI is unavailable",
                    reason="invalid_redirect_uri",
                )
            authorization_url = self.build_authorization_url(challenge, redirect_uri)
            callback = self._validated_callback(
                self._receive_callback(receiver, authorization_url), challenge, receiver
            )
            token_parameters = {
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": callback.code,
                "state": callback.state,
                "redirect_uri": redirect_uri,
                "code_verifier": challenge.verifier,
            }
            json_exchange = getattr(self._oauth, "request_json_token", None)
            if not callable(json_exchange):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude OAuth JSON token exchange is unavailable",
                    reason="token_exchange_unavailable",
                )
            with self._provider_operation():
                payload = json_exchange(
                    _TOKEN_ENDPOINT,
                    token_parameters,
                    provider=self.provider,
                )
                credential = self._credential_from_token_response(payload)
                self._save(credential)
                return credential
        except SubscriptionError as exc:
            raise _normalize_shared_error(exc, reason="oauth_error") from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token exchange failed",
                reason="token_exchange_failed",
            ) from None
        finally:
            close = getattr(receiver, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _bootstrap_identity(self, access_token: str) -> dict[str, Any]:
        endpoint = f"{_BOOTSTRAP_ENDPOINT}?entrypoint=cli&model=claude-opus-5"
        request = Request(
            endpoint,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": f"claude-code/{_CLAUDE_CODE_VERSION}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            method="GET",
        )
        try:
            response = self._open_request(request)
            self._validate_response_target(response)
            status, response_body = self._read_response(response)
            if status < 200 or status >= 300:
                return {}
            decoded = json.loads(response_body.decode("utf-8"))
        except Exception:
            return {}
        if not isinstance(decoded, Mapping):
            return {}
        raw_account = decoded.get("oauth_account")
        if not isinstance(raw_account, Mapping):
            return {}
        account_id = _safe_string(raw_account.get("account_uuid"))
        email = _safe_string(raw_account.get("account_email"))
        organization_id = _safe_string(raw_account.get("organization_uuid"))
        organization_name = _safe_string(raw_account.get("organization_name"))
        result: dict[str, Any] = {}
        if account_id is not None or email is not None:
            result["account"] = {
                **({"uuid": account_id} if account_id is not None else {}),
                **({"email_address": email} if email is not None else {}),
            }
        if organization_id is not None or organization_name is not None:
            result["organization"] = {
                **({"uuid": organization_id} if organization_id is not None else {}),
                **(
                    {"name": organization_name} if organization_name is not None else {}
                ),
            }
        return result

    def _credential_from_token_response(
        self,
        payload: Any,
        *,
        previous: SubscriptionCredential | None = None,
    ) -> SubscriptionCredential:
        secrets = _token_secrets(payload)
        if not isinstance(payload, Mapping):
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token response is invalid",
                reason="malformed_token_response",
                secret_values=secrets,
            )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token response is missing a required token value",
                reason="missing_access_token",
                secret_values=secrets,
            )
        token_type = payload.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str) or token_type.strip().lower() != "bearer"
        ):
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token response is invalid",
                reason="invalid_token_type",
                secret_values=secrets,
            )

        refresh_token = payload.get("refresh_token")
        if (
            refresh_token is None
            and previous is not None
            and previous.refresh_token is not None
        ):
            refresh_token = previous.refresh_token.get_secret_value()
        if refresh_token is not None and (
            not isinstance(refresh_token, str) or not refresh_token.strip()
        ):
            raise _error(
                SubscriptionAuthError,
                "Claude OAuth token response is invalid",
                reason="invalid_refresh_token",
                secret_values=secrets,
            )

        expires_at = self._token_expiry(payload, previous=previous)
        identity_payload = dict(payload)
        account = identity_payload.get("account")
        account_mapping = account if isinstance(account, Mapping) else {}
        organization = identity_payload.get("organization")
        organization_mapping = organization if isinstance(organization, Mapping) else {}
        account_id = (
            _safe_string(identity_payload.get("account_id"))
            or _safe_string(account_mapping.get("uuid"))
            or _safe_string(account_mapping.get("id"))
        )
        account_email = _safe_string(identity_payload.get("email")) or _safe_string(
            account_mapping.get("email_address")
        )
        if previous is None and not (
            account_id
            and account_email
            and _safe_string(organization_mapping.get("uuid"))
            and _safe_string(organization_mapping.get("name"))
        ):
            bootstrap = self._bootstrap_identity(access_token)
            for section in ("account", "organization"):
                incoming = bootstrap.get(section)
                if not isinstance(incoming, Mapping):
                    continue
                current = identity_payload.get(section)
                merged = dict(current) if isinstance(current, Mapping) else {}
                for key, value in incoming.items():
                    if _safe_string(merged.get(key)) is None:
                        merged[key] = value
                identity_payload[section] = merged
        account_id, metadata, account_label = self._account_metadata(
            identity_payload,
            access_token=access_token,
            refresh_token=refresh_token,
            previous=previous,
        )
        metadata.pop("policy_warning", None)
        metadata["research_only"] = True
        return SubscriptionCredential(
            provider=self.provider,
            account_label=account_label,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
            metadata=metadata,
        )

    @staticmethod
    def _token_expiry(
        payload: Mapping[str, Any],
        *,
        previous: SubscriptionCredential | None,
    ) -> datetime | None:
        if "expires_in" in payload and payload.get("expires_in") is not None:
            raw = payload.get("expires_in")
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth token response is invalid",
                    reason="invalid_expiry",
                )
            try:
                seconds = float(raw)
            except (OverflowError, ValueError):
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth token response is invalid",
                    reason="invalid_expiry",
                ) from None
            if (
                not math.isfinite(seconds)
                or seconds < 0
                or seconds > _MAX_EXPIRES_IN_SECONDS
            ):
                raise _error(
                    SubscriptionAuthError,
                    "Claude OAuth token response is invalid",
                    reason="invalid_expiry",
                )
            usable_seconds = max(0.0, seconds - _TOKEN_EXPIRY_SKEW_SECONDS)
            return datetime.now(UTC) + timedelta(seconds=usable_seconds)
        return previous.expires_at if previous is not None else None

    @staticmethod
    def _account_metadata(
        payload: Mapping[str, Any],
        *,
        access_token: str,
        refresh_token: str | None,
        previous: SubscriptionCredential | None,
    ) -> tuple[str | None, dict[str, Any], str]:
        account = payload.get("account")
        account_mapping = account if isinstance(account, Mapping) else {}
        organization = payload.get("organization")
        organization_mapping = organization if isinstance(organization, Mapping) else {}
        account_id = (
            _safe_string(payload.get("account_id"))
            or _safe_string(account_mapping.get("uuid"))
            or _safe_string(account_mapping.get("id"))
        )
        email = _safe_string(payload.get("email")) or _safe_string(
            account_mapping.get("email_address")
        )
        name = _safe_string(payload.get("name")) or _safe_string(
            account_mapping.get("name")
        )
        organization_id = _safe_string(organization_mapping.get("uuid"))
        organization_name = _safe_string(organization_mapping.get("name"))
        scope = _safe_string(payload.get("scope"), maximum=_MAX_TEXT_LENGTH)

        secret_values = {access_token}
        if refresh_token is not None:
            secret_values.add(refresh_token)
        if previous is not None:
            secret_values.add(previous.access_token.get_secret_value())
            if previous.refresh_token is not None:
                secret_values.add(previous.refresh_token.get_secret_value())

        def safe_identity(value: str | None) -> str | None:
            if (
                value is None
                or value == "[redacted]"
                or any(secret in value for secret in secret_values)
            ):
                return None
            return value

        account_id = safe_identity(account_id)
        email = safe_identity(email)
        name = safe_identity(name)
        organization_id = safe_identity(organization_id)
        organization_name = safe_identity(organization_name)
        if previous is not None:
            account_id = account_id or safe_identity(previous.account_id)
            if email is None:
                email = safe_identity(_safe_string(previous.metadata.get("email")))
            if name is None:
                name = safe_identity(_safe_string(previous.metadata.get("name")))
            if organization_id is None:
                organization_id = safe_identity(
                    _safe_string(previous.metadata.get("organization_id"))
                )
            if organization_name is None:
                organization_name = safe_identity(
                    _safe_string(previous.metadata.get("organization_name"))
                )

        metadata: dict[str, str] = {}
        if email is not None:
            metadata["email"] = email
        if name is not None:
            metadata["name"] = name
        if scope is not None and not any(secret in scope for secret in secret_values):
            metadata["scope"] = scope
        if organization_id is not None:
            metadata["organization_id"] = organization_id
        if organization_name is not None:
            metadata["organization_name"] = organization_name

        candidates = [name, email, account_id]
        selected_label = next(
            (
                candidate
                for candidate in candidates
                if candidate is not None and candidate not in secret_values
            ),
            None,
        )
        if selected_label is None and previous is not None:
            prior = previous.account_label
            if prior and not any(secret in prior for secret in secret_values):
                selected_label = prior
        return account_id, metadata, selected_label or _DEFAULT_ACCOUNT_LABEL

    def _load_optional(self) -> SubscriptionCredential | None:
        loader = getattr(self._store, "load", None)
        if not callable(loader):
            loader = getattr(self._store, "get", None)
        if not callable(loader):
            raise _error(
                SubscriptionAuthError,
                "Claude subscription store is unavailable",
                reason="store_unavailable",
            )
        try:
            credential = loader(self.provider)
        except SubscriptionError as exc:
            raise _normalize_shared_error(exc, reason="store_unavailable") from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude subscription credential could not be loaded",
                reason="store_unavailable",
            ) from None
        if credential is None:
            return None
        try:
            normalized = (
                credential
                if isinstance(credential, SubscriptionCredential)
                else SubscriptionCredential.model_validate(credential)
            )
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude subscription credential is invalid",
                reason="corrupt_record",
            ) from None
        if normalized.provider is not self.provider:
            raise _error(
                SubscriptionAuthError,
                "Claude subscription credential is invalid",
                reason="provider_mismatch",
            )
        return normalized

    def _load_required(self) -> SubscriptionCredential:
        credential = self._load_optional()
        if credential is None:
            raise _error(
                SubscriptionTokenExpired,
                "Claude subscription authentication is required",
                reason="missing_credential",
            )
        return credential

    def _save(self, credential: SubscriptionCredential) -> None:
        saver = getattr(self._store, "save", None)
        if not callable(saver):
            raise _error(
                SubscriptionAuthError,
                "Claude subscription store is unavailable",
                reason="store_unavailable",
            )
        try:
            try:
                saver(self.provider, credential)
            except TypeError:
                saver(credential)
        except SubscriptionError as exc:
            raise _normalize_shared_error(
                exc, reason="store_unavailable", credential=credential
            ) from None
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Claude subscription credential could not be saved",
                reason="store_unavailable",
            ) from None

    def _provider_operation(self) -> Any:
        provider_lock = getattr(self._store, "provider_lock", None)
        return (
            provider_lock(self.provider) if callable(provider_lock) else nullcontext()
        )

    def refresh(self) -> SubscriptionCredential:
        """Refresh the stored Claude OAuth credential once."""

        with self._provider_operation():
            return self._refresh_locked()

    def _refresh_locked(self) -> SubscriptionCredential:
        current = self._load_required()
        if current.refresh_token is None:
            raise _error(
                SubscriptionTokenExpired,
                "Claude subscription authentication has expired",
                status=401,
                reason="missing_refresh_token",
                credential=current,
            )
        try:
            token_parameters = {
                "grant_type": "refresh_token",
                "client_id": _CLIENT_ID,
                "refresh_token": current.refresh_token.get_secret_value(),
            }
            json_refresh = getattr(self._oauth, "request_json_token", None)
            if not callable(json_refresh):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude OAuth JSON token refresh is unavailable",
                    status=401,
                    reason="token_exchange_unavailable",
                    credential=current,
                )
            payload = json_refresh(
                _TOKEN_ENDPOINT,
                token_parameters,
                provider=self.provider,
            )
        except SubscriptionError as exc:
            normalized = _normalize_shared_error(exc, credential=current)
            if isinstance(normalized, SubscriptionPolicyError):
                raise normalized from None
            if isinstance(normalized, SubscriptionAuthError):
                raise _error(
                    SubscriptionTokenExpired,
                    "Claude subscription refresh failed",
                    status=normalized.status,
                    reason=normalized.metadata.get("reason", "refresh_failed"),
                    credential=current,
                ) from None
            raise normalized from None
        except Exception:
            raise _error(
                SubscriptionTokenExpired,
                "Claude subscription refresh failed",
                status=401,
                reason="refresh_failed",
                credential=current,
            ) from None
        replacement = self._credential_from_token_response(payload, previous=current)
        self._save(replacement)
        return replacement

    def logout(self) -> None:
        """Delete the Claude credential from the separate encrypted store."""

        with self._provider_operation():
            deleter = getattr(self._store, "delete", None)
            if not callable(deleter):
                deleter = getattr(self._store, "remove", None)
            if not callable(deleter):
                raise _error(
                    SubscriptionAuthError,
                    "Claude subscription store is unavailable",
                    reason="store_unavailable",
                )
            try:
                deleter(self.provider)
            except SubscriptionError as exc:
                raise _normalize_shared_error(exc, reason="store_unavailable") from None
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Claude subscription credential could not be removed",
                    reason="store_unavailable",
                ) from None

    def status(self) -> SubscriptionStatus:
        """Return safe Claude subscription authentication status."""

        credential = self._load_optional()
        if credential is None:
            return SubscriptionStatus(
                provider=self.provider,
                authenticated=False,
                metadata=self._status_metadata(),
            )
        now = datetime.now(UTC)
        expires_at = credential.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        authenticated = expires_at is None or expires_at > now
        metadata = self._status_metadata(credential)
        account_label = metadata.get("account_label")
        if not isinstance(account_label, str) or not account_label:
            account_label = _DEFAULT_ACCOUNT_LABEL
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=authenticated,
            account_label=account_label,
            expires_at=expires_at,
            metadata=metadata,
        )

    def _get_model_page(
        self,
        credential: SubscriptionCredential,
        url: str,
    ) -> tuple[int, bytes]:
        validate_provider_url(url, allowed_hosts=set(_MESSAGES_HOSTS))
        headers = dict(_MESSAGES_HEADERS)
        headers["anthropic-beta"] = ",".join(_UTILITY_BETAS)
        headers["Authorization"] = (
            f"Bearer {credential.access_token.get_secret_value()}"
        )
        request = Request(url, headers=headers, method="GET")
        try:
            response = self._open_request(request)
            self._validate_response_target(response)
            status, payload = self._read_response(response)
        except HTTPError as exc:
            return int(exc.code), b""
        except SubscriptionError:
            raise
        except (OSError, URLError, TimeoutError):
            raise _error(
                SubscriptionTransportError,
                "Claude model catalog request could not be delivered",
                reason="model_discovery_failed",
                credential=credential,
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Claude model catalog response could not be read",
                reason="model_discovery_failed",
                credential=credential,
            ) from None
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Claude model catalog response is too large",
                status=status,
                reason="response_too_large",
                credential=credential,
            )
        return status, payload

    def _discover_models(
        self,
        credential: SubscriptionCredential,
    ) -> tuple[int, tuple[SubscriptionModel, ...]]:
        url = _MODELS_ENDPOINT
        seen_urls: set[str] = set()
        seen_models: set[str] = set()
        models: list[SubscriptionModel] = []
        for provider_order in range(_MAX_MODEL_PAGES):
            if url in seen_urls:
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog pagination is invalid",
                    reason="model_discovery_failed",
                    credential=credential,
                )
            seen_urls.add(url)
            status, payload = self._get_model_page(credential, url)
            if status == 401:
                return status, ()
            if status < 200 or status >= 300:
                raise self._map_http_error(status, credential=credential)
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog response is invalid",
                    reason="malformed_model_catalog",
                    credential=credential,
                ) from None
            if not isinstance(decoded, Mapping):
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog response is invalid",
                    reason="malformed_model_catalog",
                    credential=credential,
                )
            raw_models = decoded.get("data")
            if not isinstance(raw_models, Sequence) or isinstance(
                raw_models, (str, bytes, bytearray)
            ):
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog response is invalid",
                    reason="malformed_model_catalog",
                    credential=credential,
                )
            for raw_model in raw_models:
                if not isinstance(raw_model, Mapping):
                    continue
                model_id = raw_model.get("id")
                if (
                    not isinstance(model_id, str)
                    or not model_id.startswith("claude-")
                    or "/" in model_id
                    or model_id in seen_models
                ):
                    continue
                display_name = raw_model.get("display_name")
                if not isinstance(display_name, str) or not display_name:
                    display_name = model_id
                release_at: datetime | None = None
                created_at = raw_model.get("created_at")
                if isinstance(created_at, str):
                    try:
                        release_at = datetime.fromisoformat(
                            created_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        release_at = None
                seen_models.add(model_id)
                models.append(
                    SubscriptionModel(
                        model_id=model_id,
                        display_name=display_name,
                        reasoning=resolve_reasoning_profile("claude", model_id),
                        release_at=release_at,
                        provider_order=len(models),
                    )
                )
            has_more = decoded.get("has_more", False)
            if not isinstance(has_more, bool):
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog pagination is invalid",
                    reason="model_discovery_failed",
                    credential=credential,
                )
            if not has_more:
                return status, tuple(models)
            last_id = decoded.get("last_id")
            if not isinstance(last_id, str) or not last_id:
                raise _error(
                    SubscriptionTransportError,
                    "Claude model catalog pagination is invalid",
                    reason="model_discovery_failed",
                    credential=credential,
                )
            url = f"{_MODELS_ENDPOINT}&{urlencode({'after_id': last_id})}"
        raise _error(
            SubscriptionTransportError,
            "Claude model catalog pagination exceeded its page limit",
            reason="model_discovery_failed",
            credential=credential,
        )

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        """Return every model visible to the authenticated Claude account."""

        credential = self._load_required()
        status, models = self._discover_models(credential)
        if status == 401:
            credential = self.refresh()
            status, models = self._discover_models(credential)
        if status == 401:
            raise _error(
                SubscriptionTokenExpired,
                "Claude subscription authentication has expired",
                status=401,
                reason="refresh_rejected",
                credential=credential,
            )
        return models

    def _build_request_body(
        self,
        request: CompletionRequest,
        *,
        structured: bool,
    ) -> dict[str, Any]:
        native_model = resolve_subscription_model(self.provider, request.model)
        if native_model != request.model:
            request = request.model_copy(update={"model": native_model})
        if not isinstance(request.model, str) or not request.model.strip():
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude model is unsupported",
                reason="invalid_model",
            )
        if request.cache_key is not None:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude prompt cache keys are unsupported by this backend",
                reason="cache_key_unsupported",
            )
        if request.max_tokens is not None and request.max_tokens <= 0:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude max tokens must be positive",
                reason="invalid_max_tokens",
            )

        messages: list[dict[str, Any]] = []
        system_parts: list[str] = []
        first_user_text: str | None = None
        for message in request.messages:
            if not isinstance(message, Mapping):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude message shape is unsupported",
                    reason="unsupported_message",
                )
            if any(
                key in message
                for key in (
                    "tool_calls",
                    "tool_call",
                    "tool_call_id",
                    "function_call",
                    "function",
                )
            ):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude tool messages are unsupported by this backend",
                    reason="tool_input_unsupported",
                )
            role = message.get("role")
            if role in {"system", "developer"}:
                system_parts.append(_text_blocks(message.get("content")))
                continue
            if role not in {"user", "assistant"}:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude message role is unsupported",
                    reason="unsupported_message",
                )
            blocks = _content_blocks(message.get("content"))
            if role == "user" and first_user_text is None:
                first_user_text = next(
                    (
                        str(block["text"])
                        for block in blocks
                        if block.get("type") == "text"
                    ),
                    "",
                )
            if role == "assistant" and any(
                block.get("type") != "text" for block in blocks
            ):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Claude assistant history supports text content only",
                    reason="unsupported_message_content",
                )
            messages.append(
                {
                    "role": role,
                    "content": blocks,
                }
            )
        if not messages:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude completion requires at least one user or assistant message",
                reason="unsupported_message",
            )

        selected_max_tokens = min(
            request.max_tokens or _DEFAULT_MAX_TOKENS,
            64_000,
        )
        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "system": [
                {
                    "type": "text",
                    "text": _create_billing_header(first_user_text or ""),
                },
                {
                    "type": "text",
                    "text": _IDENTITY_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                },
            ],
            "tools": [],
            "max_tokens": selected_max_tokens,
        }
        if system_parts:
            body["system"].append({"type": "text", "text": "\n".join(system_parts)})
        if request.reasoning is not None:
            profile = resolve_reasoning_profile("claude", request.model)
            effort = choose_supported_effort(profile, request.reasoning)
            if effort is not None and profile.mode == "anthropic-adaptive":
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {"effort": effort}
            elif effort is not None:
                configured_budget = _THINKING_BUDGETS.get(effort)
                if configured_budget is None:
                    raise _error(
                        SubscriptionUnsupportedRequest,
                        "Claude reasoning control is unsupported",
                        reason="unsupported_reasoning",
                    )
                if configured_budget >= selected_max_tokens:
                    raise _error(
                        SubscriptionUnsupportedRequest,
                        "Claude thinking budget leaves no visible output room",
                        reason="invalid_reasoning_budget",
                    )
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": configured_budget,
                }

        if structured:
            output_config = body.setdefault("output_config", {})
            output_config["format"] = {
                "type": "json_schema",
                "schema": _as_plain_json(request.schema),
            }
        body["stream"] = True
        return body

    def _open_request(self, request: Request) -> Any:
        if self._fetch is not None:
            fetch = self._fetch
            try:
                return fetch(request, timeout=self._timeout)
            except TypeError:
                try:
                    return fetch(request, self._timeout)
                except TypeError:
                    try:
                        return fetch(
                            request.full_url,
                            request.data,
                            dict(request.headers),
                            self._timeout,
                        )
                    except TypeError:
                        return fetch(
                            request.full_url, request.data, dict(request.headers)
                        )
        transport = self._transport
        if transport is not None:
            if hasattr(transport, "open"):
                return transport.open(request, timeout=self._timeout)
            if hasattr(transport, "request"):
                method = transport.request
                try:
                    return method(request, timeout=self._timeout)
                except TypeError:
                    return method(
                        request.full_url,
                        request.data,
                        dict(request.headers),
                        self._timeout,
                    )
            if hasattr(transport, "post"):
                method = transport.post
                try:
                    return method(
                        request.full_url,
                        data=request.data,
                        headers=dict(request.headers),
                        timeout=self._timeout,
                    )
                except TypeError:
                    return method(
                        request.full_url,
                        request.data,
                        dict(request.headers),
                        self._timeout,
                    )
            if callable(transport):
                return transport(request, timeout=self._timeout)
        return _NO_REDIRECT_OPENER.open(request, timeout=self._timeout)

    @staticmethod
    def _response_header(response: Any, name: str) -> str | None:
        getter = getattr(response, "getheader", None)
        if callable(getter):
            value = getter(name)
            return value if isinstance(value, str) else None
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if str(key).lower() == name.lower() and isinstance(value, str):
                    return value
        return None

    def _decode_transport_body(self, response: Any, raw: bytes) -> bytes:
        encoding = self._response_header(response, "Content-Encoding")
        if encoding is None:
            return raw
        normalized = encoding.strip().lower()
        if normalized == "gzip":
            window_bits = zlib.MAX_WBITS | 16
        elif normalized == "deflate":
            window_bits = zlib.MAX_WBITS
        else:
            raise _error(
                SubscriptionTransportError,
                "Claude response compression is unsupported",
                reason="unsupported_content_encoding",
            )

        limit = self._max_response_bytes + 1
        try:
            decompressor = zlib.decompressobj(window_bits)
            decoded = decompressor.decompress(raw, limit)
            if len(decoded) > self._max_response_bytes or decompressor.unconsumed_tail:
                raise _error(
                    SubscriptionTransportError,
                    "Claude response is too large",
                    reason="response_too_large",
                )
            decoded += decompressor.flush(limit - len(decoded))
        except zlib.error:
            raise _error(
                SubscriptionTransportError,
                "Claude compressed response is malformed",
                reason="malformed_response",
            ) from None
        if len(decoded) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Claude response is too large",
                reason="response_too_large",
            )
        if not decompressor.eof or decompressor.unused_data:
            raise _error(
                SubscriptionTransportError,
                "Claude compressed response is malformed",
                reason="malformed_response",
            )
        return decoded

    def _read_response(self, response: Any) -> tuple[int, bytes]:
        if isinstance(response, Mapping):
            return 200, json.dumps(response, ensure_ascii=True).encode("utf-8")
        if isinstance(response, str):
            return 200, response.encode("utf-8")
        if isinstance(response, (bytes, bytearray, memoryview)):
            return 200, bytes(response)
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "status_code", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is None:
            status = 200
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray, memoryview)):
            raw = self._decode_transport_body(response, bytes(content))
            close = getattr(response, "close", None)
            if callable(close):
                close()
            return int(status), raw
        reader = getattr(response, "read", None)
        if not callable(reader):
            raise TypeError("Claude response body is not readable")
        try:
            try:
                raw = reader(self._max_response_bytes + 1)
            except TypeError:
                raw = reader()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError("Claude response body is not bytes")
        return int(status), self._decode_transport_body(response, bytes(raw))

    @staticmethod
    def _validate_response_target(response: Any) -> None:
        target = getattr(response, "geturl", None)
        if callable(target):
            target = target()
        if target is None:
            target = getattr(response, "url", None)
        if target is None:
            return
        try:
            validate_provider_url(target, allowed_hosts=set(_MESSAGES_HOSTS))
        except SubscriptionError:
            raise _error(
                SubscriptionTransportError,
                "Claude response endpoint redirect is not allowed",
                reason="endpoint_redirect",
            ) from None

    def _post_json(
        self,
        credential: SubscriptionCredential,
        body: Mapping[str, Any],
    ) -> tuple[int, bytes]:
        validate_provider_url(_MESSAGES_ENDPOINT, allowed_hosts=set(_MESSAGES_HOSTS))
        try:
            data = json.dumps(
                _as_plain_json(body), ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
            data = _patch_billing_attestation(data)
        except (TypeError, ValueError):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude request body is not JSON serializable",
                reason="invalid_request",
                credential=credential,
            ) from None
        headers = dict(_MESSAGES_HEADERS)
        headers["anthropic-beta"] = _messages_beta_header(body)
        headers["X-Claude-Code-Session-Id"] = self._session_id
        headers["Authorization"] = (
            f"Bearer {credential.access_token.get_secret_value()}"
        )
        request = Request(
            _MESSAGES_ENDPOINT,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            response = self._open_request(request)
            self._validate_response_target(response)
            status, payload = self._read_response(response)
        except HTTPError as exc:
            return int(exc.code), b""
        except SubscriptionError:
            raise
        except (OSError, URLError, TimeoutError):
            raise _error(
                SubscriptionTransportError,
                "Claude request could not be delivered",
                reason="transport_error",
                credential=credential,
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Claude response could not be read",
                reason="response_error",
                credential=credential,
            ) from None
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Claude response is too large",
                status=status,
                reason="response_too_large",
                credential=credential,
            )
        return status, payload

    def _map_http_error(
        self,
        status: int,
        *,
        credential: SubscriptionCredential | None = None,
    ) -> SubscriptionError:
        if status == 401:
            return _error(
                SubscriptionAuthError,
                "Claude authentication was rejected",
                status=status,
                reason="authentication_rejected",
                credential=credential,
            )
        if status == 403:
            return _error(
                SubscriptionPolicyError,
                "Claude subscription request was rejected by provider policy",
                status=status,
                reason="policy_rejected",
                credential=credential,
            )
        if status == 429:
            return _error(
                SubscriptionTransportError,
                "Claude subscription rate limit reached",
                category="rate_limited",
                status=status,
                reason="rate_limited",
                credential=credential,
            )
        if 500 <= status <= 599:
            return _error(
                SubscriptionTransportError,
                "Claude subscription service is unavailable",
                category="provider_unavailable",
                status=status,
                reason="provider_unavailable",
                credential=credential,
            )
        if 400 <= status <= 499:
            return _error(
                SubscriptionAuthError,
                "Claude request was rejected",
                status=status,
                reason="provider_client_error",
                credential=credential,
            )
        return _error(
            SubscriptionTransportError,
            "Claude request failed",
            status=status,
            reason="provider_error",
            credential=credential,
        )

    def _normalize_response(
        self,
        request: CompletionRequest,
        body: bytes,
        *,
        structured: bool,
    ) -> CompletionResult:
        decoded = _decode_response_message(body)
        error_kind = _response_error_kind(decoded)
        if error_kind is not None:
            lowered = error_kind.lower()
            if lowered in {"permission_error", "forbidden", "policy_error"}:
                raise _error(
                    SubscriptionPolicyError,
                    "Claude subscription request was rejected by provider policy",
                    status=200,
                    reason="policy_rejected",
                )
            if lowered in {"authentication_error", "invalid_request_error"}:
                raise _error(
                    SubscriptionAuthError,
                    "Claude request was rejected",
                    status=200,
                    reason="authentication_rejected"
                    if lowered == "authentication_error"
                    else "provider_client_error",
                )
            raise _error(
                SubscriptionTransportError,
                "Claude response contains an error",
                status=200,
                reason="provider_error",
            )

        selected_model = _safe_string(decoded.get("model"))
        stop_reason_value = decoded.get("stop_reason")
        if (
            decoded.get("type") != "message"
            or decoded.get("role") != "assistant"
            or selected_model is None
            or "stop_reason" not in decoded
            or (
                stop_reason_value is not None and not isinstance(stop_reason_value, str)
            )
        ):
            raise _error(
                SubscriptionTransportError,
                "Claude response envelope is malformed",
                reason="malformed_response",
            )

        content = decoded.get("content")
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            raise _error(
                SubscriptionTransportError,
                "Claude response content is malformed",
                reason="malformed_response",
            )
        visible_parts: list[str] = []
        for block in content:
            if not isinstance(block, Mapping):
                raise _error(
                    SubscriptionTransportError,
                    "Claude response content is malformed",
                    reason="malformed_response",
                )
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise _error(
                        SubscriptionTransportError,
                        "Claude response text is malformed",
                        reason="malformed_response",
                    )
                visible_parts.append(text)
            elif block_type == "thinking":
                if not isinstance(block.get("thinking"), str):
                    raise _error(
                        SubscriptionTransportError,
                        "Claude reasoning response is malformed",
                        reason="malformed_response",
                    )
            elif block_type == "redacted_thinking":
                continue
            else:
                raise _error(
                    SubscriptionTransportError,
                    "Claude response content is unsupported",
                    reason="unsupported_response_content",
                )
        text = "".join(visible_parts)
        if not text:
            raise _error(
                SubscriptionTransportError,
                "Claude response did not contain visible text",
                reason="missing_visible_text",
            )

        structured_json: Any | None = None
        if structured:
            try:
                structured_json = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                raise _error(
                    SubscriptionTransportError,
                    "Claude structured output is invalid JSON",
                    reason="invalid_structured_output",
                ) from None
            if not _schema_matches(structured_json, request.schema or {}):
                raise _error(
                    SubscriptionTransportError,
                    "Claude structured output does not match the requested schema",
                    reason="structured_schema_mismatch",
                )

        stop_reason = decoded.get("stop_reason")
        finish_reason = (
            stop_reason.lower()
            if isinstance(stop_reason, str) and stop_reason
            else None
        )
        return CompletionResult(
            text=text,
            structured_json=structured_json,
            usage=_extract_usage(decoded),
            finish_reason=finish_reason,
            provider=self.provider,
            model=selected_model,
        )

    def _complete(
        self, request: CompletionRequest, *, structured: bool
    ) -> CompletionResult:
        native_model = resolve_subscription_model(self.provider, request.model)
        if native_model != request.model:
            request = request.model_copy(update={"model": native_model})
        credential = self._load_required()
        body = self._build_request_body(request, structured=structured)
        status, payload = self._post_json(credential, body)
        if status == 401:
            try:
                credential = self.refresh()
            except SubscriptionTokenExpired as exc:
                if exc.status is None:
                    raise _error(
                        SubscriptionTokenExpired,
                        "Claude subscription authentication has expired",
                        status=401,
                        reason="refresh_failed",
                    ) from None
                raise
            status, payload = self._post_json(credential, body)
            if status == 401:
                raise _error(
                    SubscriptionTokenExpired,
                    "Claude subscription authentication has expired",
                    status=401,
                    reason="refresh_rejected",
                    credential=credential,
                )
        if status < 200 or status >= 300:
            raise self._map_http_error(status, credential=credential)
        return self._normalize_response(request, payload, structured=structured)

    def complete(
        self, request: CompletionRequest | Mapping[str, Any]
    ) -> CompletionResult:
        """Complete a normalized text or structured request through Messages."""

        normalized = _coerce_request(request)
        structured = normalized.schema is not None
        if structured:
            _require_supported_schema(normalized.schema)
        return self._complete(normalized, structured=structured)

    def complete_structured(
        self,
        request: CompletionRequest | Mapping[str, Any],
    ) -> CompletionResult:
        """Complete a request whose output must satisfy its JSON schema."""

        normalized = _coerce_request(request)
        if normalized.schema is None:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Claude structured completion requires a JSON schema",
                reason="missing_schema",
            )
        _require_supported_schema(normalized.schema)
        return self._complete(normalized, structured=True)


__all__ = ["ClaudeResearchBackend"]
