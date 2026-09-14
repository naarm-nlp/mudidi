"""Direct OpenAI Codex subscription routing.

The adapter intentionally does not import MUDIDI's existing API-key
completion path or any provider/agent credential store.  OAuth tokens are
accepted only from the shared OAuth helper and are retained in
:class:`SubscriptionCredential` objects while inside this backend or the
encrypted subscription store.

The Codex endpoint is a subscription product surface, not the public OpenAI
API.  Its protocol is provider-owned and may change; this module therefore
keeps the researched endpoint/client/scope values private and exposes only the
provider-neutral backend contract.
"""

from __future__ import annotations

from contextlib import nullcontext
from collections.abc import Callable, Mapping, Sequence
import base64
from datetime import UTC, datetime, timedelta
import hmac
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

from mudidi.llm.reasoning import choose_supported_effort, resolve_reasoning_profile
from mudidi.llm.subscriptions.oauth import (
    LoopbackOAuthReceiver,
    OAuthCallback,
    OAuthHttpClient,
    _build_no_redirect_opener,
    begin_login_transaction,
    complete_login_transaction,
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
    SubscriptionModel,
    SubscriptionStatus,
    SubscriptionTokenExpired,
    SubscriptionTransportError,
    SubscriptionUnsupportedRequest,
    resolve_subscription_model,
)

# These are researched Codex subscription values, not public API-key values.
# Keep them module-private so provider selection cannot accidentally become a
# general API-key configuration surface.
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_AUTHORIZATION_ENDPOINT = "https://auth.openai.com/oauth/authorize"
_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
_RESPONSES_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
_MODELS_ENDPOINT = "https://chatgpt.com/backend-api/codex/models?client_version=0.0.0"
_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
_REDIRECT_PATH = "/auth/callback"
_REDIRECT_URI = "http://localhost:1455/auth/callback"
_ORIGINATOR = "codex_cli_rs"
_JWT_AUTH_CLAIM = "https://api.openai.com/auth"
_JWT_PROFILE_CLAIM = "https://api.openai.com/profile"
_DEFAULT_ACCOUNT_LABEL = "OpenAI Codex"
_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 300.0
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1_048_576
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_PDF_MIME_TYPE = "application/pdf"

# Deliberately narrow allowlists.  They are used for both configured constants
# and final response URLs when an injected seam reports one.
_OAUTH_HOSTS = frozenset({"auth.openai.com"})
_CODEX_HOSTS = frozenset({"chatgpt.com"})


_NO_REDIRECT_OPENER = _build_no_redirect_opener()


def _error(
    error_type: type[SubscriptionError],
    message: str,
    *,
    category: str | None = None,
    status: int | None = None,
    reason: str | None = None,
) -> SubscriptionError:
    metadata: dict[str, Any] = {}
    if reason is not None:
        metadata["reason"] = reason
    return error_type(
        message,
        provider=SubscriptionProvider.OPENAI,
        category=category,
        status=status,
        metadata=metadata,
    )


def _bounded_timeout(value: float | int | None) -> float:
    selected = _DEFAULT_TIMEOUT if value is None else float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise ValueError("timeout must be a finite positive number")
    return min(selected, _MAX_TIMEOUT)


def _as_plain_json(value: Any) -> Any:
    """Copy frozen contract containers into ordinary JSON containers."""

    if isinstance(value, Mapping):
        return {str(key): _as_plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain_json(item) for item in value]
    return value


def _safe_string(value: Any, *, maximum: int = 256) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        return None
    return value


def _safe_email(value: Any) -> str | None:
    selected = _safe_string(value)
    if selected is None:
        return None
    normalized = selected.strip().lower()
    return normalized or None


def _decode_jwt_claims(token: str) -> Mapping[str, Any] | None:
    """Decode a JWT payload without retaining or returning the token itself."""

    try:
        pieces = token.split(".")
        if len(pieces) != 3:
            return None
        encoded = pieces[1]
        if not encoded:
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(payload.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    except Exception:
        # Base64 implementations can raise binascii.Error (a ValueError
        # subclass on supported Python versions) and platform-specific errors.
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _jwt_metadata(token: str) -> tuple[str | None, dict[str, str], float | None]:
    """Return a whitelist of non-secret identity claims and an optional expiry."""

    claims = _decode_jwt_claims(token)
    if claims is None:
        return None, {}, None

    nested = claims.get(_JWT_AUTH_CLAIM)
    auth = nested if isinstance(nested, Mapping) else {}
    profile_value = claims.get(_JWT_PROFILE_CLAIM)
    profile = profile_value if isinstance(profile_value, Mapping) else {}

    account_id = (
        _safe_string(auth.get("chatgpt_account_id"))
        or _safe_string(auth.get("account_id"))
        or _safe_string(claims.get("chatgpt_account_id"))
        or _safe_string(claims.get("account_id"))
    )
    workspace_id = _safe_string(auth.get("chatgpt_workspace_id")) or _safe_string(
        auth.get("workspace_id")
    )
    email = _safe_email(profile.get("email")) or _safe_email(claims.get("email"))
    name = _safe_string(profile.get("name")) or _safe_string(claims.get("name"))

    metadata: dict[str, str] = {}
    if account_id is not None:
        metadata["account_id"] = account_id
    if workspace_id is not None:
        metadata["workspace_id"] = workspace_id
    if email is not None:
        metadata["email"] = email
    if name is not None:
        metadata["name"] = name

    expiry: float | None = None
    raw_expiry = claims.get("exp")
    if isinstance(raw_expiry, (int, float)) and not isinstance(raw_expiry, bool):
        try:
            if math.isfinite(float(raw_expiry)) and float(raw_expiry) >= 0:
                expiry = float(raw_expiry)
        except (OverflowError, ValueError):
            pass

    return account_id, metadata, expiry


def _token_expiry(
    payload: Mapping[str, Any],
    *,
    jwt_expiry: float | None,
    previous: SubscriptionCredential | None,
) -> datetime | None:
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                reason="invalid_expiry",
            )
        try:
            seconds = float(expires_in)
        except (OverflowError, ValueError):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                reason="invalid_expiry",
            ) from None
        if not math.isfinite(seconds) or seconds < 0 or seconds > 1_000_000_000:
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                reason="invalid_expiry",
            )
        return datetime.now(UTC) + timedelta(seconds=seconds)

    if jwt_expiry is not None:
        try:
            return datetime.fromtimestamp(jwt_expiry, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return previous.expires_at if previous is not None else None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if isinstance(value, int) or numeric.is_integer():
        return int(numeric)
    return numeric


def _extract_text(value: Any) -> str | None:
    """Extract visible output text from a Responses object."""

    if not isinstance(value, Mapping):
        return None
    direct = value.get("output_text")
    if isinstance(direct, str):
        return direct
    direct_text = value.get("text")
    if isinstance(direct_text, str):
        return direct_text

    output = value.get("output")
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes, bytearray)):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in (None, "message", "output_text"):
            continue
        if item.get("type") == "output_text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type not in (None, "output_text", "text"):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts) if parts else None


def _extract_usage(value: Mapping[str, Any]) -> dict[str, int | float | None]:
    raw_usage = value.get("usage")
    if not isinstance(raw_usage, Mapping):
        return {}

    usage: dict[str, int | float | None] = {}
    input_tokens = _number(raw_usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _number(raw_usage.get("prompt_tokens"))
    output_tokens = _number(raw_usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _number(raw_usage.get("completion_tokens"))
    total_tokens = _number(raw_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens

    input_details = raw_usage.get("input_tokens_details")
    if isinstance(input_details, Mapping):
        cached = _number(input_details.get("cached_tokens"))
        if cached is not None:
            usage["cached_tokens"] = cached
    output_details = raw_usage.get("output_tokens_details")
    if isinstance(output_details, Mapping):
        reasoning = _number(output_details.get("reasoning_tokens"))
        if reasoning is not None:
            usage["reasoning_tokens"] = reasoning
    return usage


def _finish_reason(value: Mapping[str, Any]) -> str | None:
    direct = value.get("finish_reason")
    if isinstance(direct, str) and direct:
        return direct
    status = value.get("status")
    if not isinstance(status, str):
        return None
    if status == "completed":
        return "stop"
    if status in {"incomplete", "cancelled", "failed", "queued", "in_progress"}:
        details = value.get("incomplete_details")
        if isinstance(details, Mapping):
            reason = details.get("reason")
            if isinstance(reason, str) and reason:
                if reason in {"max_output_tokens", "length"}:
                    return "length"
                if reason in {"content_filter", "content_filtered"}:
                    return "content_filter"
                return reason
        return {
            "incomplete": "length",
            "cancelled": "cancelled",
            "failed": "error",
            "queued": "queued",
            "in_progress": "in_progress",
        }[status]
    return status


def _parse_json_documents(body: bytes) -> list[Mapping[str, Any]]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _error(
            SubscriptionTransportError,
            "Codex response is not valid UTF-8",
            reason="malformed_response",
        ) from None

    stripped = text.strip()
    if not stripped:
        raise _error(
            SubscriptionTransportError,
            "Codex response is empty",
            reason="malformed_response",
        )

    if not any(line.startswith("data:") for line in stripped.splitlines()):
        try:
            decoded = json.loads(stripped)
        except (TypeError, json.JSONDecodeError):
            raise _error(
                SubscriptionTransportError,
                "Codex response is not valid JSON",
                reason="malformed_response",
            ) from None
        if not isinstance(decoded, Mapping):
            raise _error(
                SubscriptionTransportError,
                "Codex response is malformed",
                reason="malformed_response",
            )
        return [decoded]

    documents: list[Mapping[str, Any]] = []
    for line in stripped.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].lstrip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            decoded = json.loads(chunk)
        except (TypeError, json.JSONDecodeError):
            raise _error(
                SubscriptionTransportError,
                "Codex response stream is malformed",
                reason="malformed_response",
            ) from None
        if isinstance(decoded, Mapping):
            documents.append(decoded)
    if not documents:
        raise _error(
            SubscriptionTransportError,
            "Codex response stream is empty",
            reason="malformed_response",
        )
    return documents


def _response_payload(
    documents: list[Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]]:
    text_parts: list[str] = []
    saw_delta = False
    response: Mapping[str, Any] | None = None
    for document in documents:
        event_type = document.get("type")
        if event_type == "response.output_text.delta":
            delta = document.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
                saw_delta = True
            continue
        if event_type == "response.output_text.done":
            if not saw_delta:
                text = document.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            continue
        if event_type == "response.output_item.done":
            if not saw_delta:
                text = _extract_text({"output": [document.get("item")]})
                if text is not None:
                    text_parts.append(text)
            continue
        nested = document.get("response")
        if isinstance(nested, Mapping):
            response = nested
        elif event_type in {
            "response.completed",
            "response.incomplete",
            "response.failed",
            "response.cancelled",
        }:
            response = document

        if response is None and (
            "output_text" in document or "output" in document or "usage" in document
        ):
            response = document

    if response is None:
        response = documents[-1]
    if not text_parts:
        extracted = _extract_text(response)
        if extracted is not None:
            text_parts.append(extracted)
    return "".join(text_parts), response


def _schema_matches(value: Any, schema: Mapping[str, Any]) -> bool:
    """Small fail-closed JSON Schema subset for provider structured output."""

    if not isinstance(schema, Mapping):
        return False
    if "const" in schema and not json_values_equal(value, schema["const"]):
        return False
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray)):
        if not any(json_values_equal(value, candidate) for candidate in enum):
            return False

    schema_type = schema.get("type")
    if isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes, bytearray)
    ):
        if not any(_schema_matches(value, {"type": item}) for item in schema_type):
            return False
    elif isinstance(schema_type, str):
        type_matches = {
            "object": isinstance(value, Mapping),
            "array": isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray)),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        if not type_matches.get(schema_type, False):
            return False

    if isinstance(value, Mapping):
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(
            required, (str, bytes, bytearray)
        ):
            if any(not isinstance(key, str) or key not in value for key in required):
                return False
        properties = schema.get("properties")
        known_properties = properties if isinstance(properties, Mapping) else {}
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    if not _schema_matches(value[key], child_schema):
                        return False
        additional = schema.get("additionalProperties")
        if additional is False:
            if any(key not in known_properties for key in value):
                return False
        elif isinstance(additional, Mapping):
            if any(
                key not in known_properties
                and not _schema_matches(value[key], additional)
                for key in value
            ):
                return False

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = schema.get("items")
        if isinstance(items, Mapping) and any(
            not _schema_matches(item, items) for item in value
        ):
            return False

    return True


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
    }
)
_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


def _schema_is_supported(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if any(key not in _SUPPORTED_SCHEMA_KEYWORDS for key in schema):
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
    if properties is not None and not isinstance(properties, Mapping):
        return False
    if isinstance(properties, Mapping):
        if any(
            not isinstance(key, str) or not _schema_is_supported(child)
            for key, child in properties.items()
        ):
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
    required_is_present = "required" in schema
    required = schema.get("required")
    if object_declared:
        required_values = required if required_is_present else []
        if (
            not isinstance(required_values, Sequence)
            or isinstance(required_values, (str, bytes, bytearray))
            or any(not isinstance(item, str) for item in required_values)
            or len(required_values) != len(set(required_values))
            or set(required_values) != set(properties or {})
        ):
            return False
    elif required_is_present:
        if not isinstance(required, Sequence) or isinstance(
            required, (str, bytes, bytearray)
        ):
            return False
        if any(not isinstance(item, str) for item in required):
            return False
    items = schema.get("items")
    if items is not None and not _schema_is_supported(items):
        return False
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping):
        return False
    if additional is not None and not isinstance(additional, bool):
        return False
    if object_declared and additional is not False:
        return False
    return True


def _require_supported_schema(schema: Any) -> None:
    if not _schema_is_supported(schema):
        raise _error(
            SubscriptionUnsupportedRequest,
            "Codex structured completion schema uses unsupported keywords",
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


class OpenAICodexBackend:
    """Provider-neutral backend for the ChatGPT/Codex subscription surface."""

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

        if fetch is not None and request is not None:
            raise TypeError("pass only one of fetch or request")
        self._fetch = fetch if fetch is not None else request
        self._transport = transport if transport is not None else http_client
        if oauth_client is None:
            self._oauth = OAuthHttpClient(
                fetch=fetch,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
                allowed_hosts=set(_OAUTH_HOSTS),
            )
        else:
            self._oauth = oauth_client

    @property
    def provider(self) -> SubscriptionProvider:
        return SubscriptionProvider.OPENAI

    @property
    def capabilities(self) -> BackendCapabilities:
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
        return "OpenAICodexBackend(provider='openai', subscription=True)"

    def build_authorization_url(
        self,
        challenge: PkceChallenge,
        redirect_uri: str,
        *,
        originator: str = _ORIGINATOR,
    ) -> str:
        if not isinstance(challenge, PkceChallenge):
            raise TypeError("challenge must be a PkceChallenge")
        try:
            validate_loopback_redirect_uri(redirect_uri)
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex OAuth callback URI is invalid",
                reason="invalid_redirect_uri",
            ) from None
        parameters = {
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": _SCOPE,
            **challenge.authorization_parameters(),
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": originator,
        }
        builder = getattr(self._oauth, "build_authorization_url", None)
        if callable(builder):
            try:
                return str(builder(_AUTHORIZATION_ENDPOINT, parameters))
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionTransportError,
                    "Codex authorization endpoint is unavailable",
                    reason="invalid_authorization_endpoint",
                ) from None
        validate_provider_url(_AUTHORIZATION_ENDPOINT, allowed_hosts=set(_OAUTH_HOSTS))
        return f"{_AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"

    authorization_url = build_authorization_url

    def _make_receiver(self, challenge: PkceChallenge) -> Any:
        factory = self._receiver_factory
        if factory is None and self._receiver is not None and callable(self._receiver):
            has_receive = any(
                hasattr(self._receiver, name) for name in ("receive", "listen", "run")
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
            port=1455,
            path=_REDIRECT_PATH,
            redirect_host="localhost",
            allow_fixed_port=True,
            timeout=self._callback_timeout,
            listener=self._listener,
            opener=self._opener,
        )

    def _receive_callback(self, receiver: Any, url: str) -> Any:
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
                "Codex OAuth callback receiver is unavailable",
                reason="callback_receiver_unavailable",
            )
        kwargs = {
            "opener": self._opener,
            "listener": self._listener,
            "timeout": self._callback_timeout,
        }
        try:
            return method(url, **kwargs)
        except TypeError:
            try:
                return method(url)
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Codex OAuth callback could not be validated",
                    reason="invalid_callback",
                ) from None
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex OAuth callback could not be validated",
                reason="invalid_callback",
            ) from None

    def _validated_callback(
        self, value: Any, challenge: PkceChallenge, receiver: Any
    ) -> OAuthCallback:
        callback = value
        if not isinstance(callback, OAuthCallback):
            handler = getattr(receiver, "handle_callback", None)
            if callable(handler):
                try:
                    callback = handler(value)
                except SubscriptionError:
                    raise
                except Exception:
                    raise _error(
                        SubscriptionAuthError,
                        "Codex OAuth callback could not be validated",
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
                query = (
                    parsed.query
                    if (parsed.scheme or parsed.netloc or parsed.path.startswith("/"))
                    else raw_callback.lstrip("?")
                )
                values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
                if (
                    len(values.get("code", [])) == 1
                    and len(values.get("state", [])) == 1
                ):
                    callback = OAuthCallback(
                        code=values["code"][0], state=values["state"][0]
                    )
            except (UnicodeDecodeError, ValueError):
                pass
        if not isinstance(callback, OAuthCallback):
            code = getattr(callback, "code", None)
            state = getattr(callback, "state", None)
            if isinstance(callback, Mapping):
                code = callback.get("code")
                state = callback.get("state")
            if (
                not isinstance(code, str)
                or not code.strip()
                or not isinstance(state, str)
            ):
                raise _error(
                    SubscriptionAuthError,
                    "Codex OAuth callback is invalid",
                    reason="invalid_callback",
                )
            callback = OAuthCallback(code=code, state=state)
        if not hmac.compare_digest(callback.state, challenge.state):
            raise _error(
                SubscriptionAuthError,
                "Codex OAuth callback state is invalid",
                reason="state_mismatch",
            )
        if not callback.code.strip():
            raise _error(
                SubscriptionAuthError,
                "Codex OAuth callback did not contain an authorization code",
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
        """Exchange a callback using the server-held PKCE verifier."""

        exchange = getattr(self._oauth, "exchange_code", None)
        if not callable(exchange):
            exchange = getattr(self._oauth, "exchange_authorization_code", None)
        with self._provider_operation():
            return complete_login_transaction(
                transaction,
                code=code,
                state=state,
                exchange=exchange,
                token_endpoint=_TOKEN_ENDPOINT,
                client_id=_CLIENT_ID,
                provider=self.provider,
                credential_from_token_response=self._credential_from_token_response,
                save=self._save,
            )

    def login(self) -> SubscriptionCredential:
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
                    "Codex OAuth callback listener is unavailable",
                    reason="callback_listener_failed",
                ) from None
            redirect_uri = getattr(receiver, "redirect_uri", None)
            if not isinstance(redirect_uri, str) or not redirect_uri:
                raise _error(
                    SubscriptionAuthError,
                    "Codex OAuth callback URI is unavailable",
                    reason="invalid_redirect_uri",
                )
            authorization_url = self.build_authorization_url(challenge, redirect_uri)
            callback = self._validated_callback(
                self._receive_callback(receiver, authorization_url), challenge, receiver
            )
            exchange = getattr(self._oauth, "exchange_code", None)
            if not callable(exchange):
                exchange = getattr(self._oauth, "exchange_authorization_code", None)
            if not callable(exchange):
                raise _error(
                    SubscriptionAuthError,
                    "Codex OAuth token exchange is unavailable",
                    reason="token_exchange_unavailable",
                )
            with self._provider_operation():
                try:
                    payload = exchange(
                        _TOKEN_ENDPOINT,
                        code=callback.code,
                        redirect_uri=redirect_uri,
                        client_id=_CLIENT_ID,
                        code_verifier=challenge.verifier,
                        provider=self.provider,
                    )
                except TypeError:
                    payload = exchange(
                        _TOKEN_ENDPOINT,
                        code=callback.code,
                        redirect_uri=redirect_uri,
                        client_id=_CLIENT_ID,
                        code_verifier=challenge.verifier,
                    )
                credential = self._credential_from_token_response(payload)
                self._save(credential)
                return credential
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex OAuth token exchange failed",
                reason="token_exchange_failed",
            ) from None
        finally:
            close = getattr(receiver, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _credential_from_token_response(
        self,
        payload: Any,
        *,
        previous: SubscriptionCredential | None = None,
    ) -> SubscriptionCredential:
        if not isinstance(payload, Mapping):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                reason="malformed_token_response",
            )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is missing a required token value",
                reason="missing_access_token",
            )
        token_type = payload.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str) or token_type.strip().lower() != "bearer"
        ):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                reason="invalid_token_type",
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
                "OAuth token response is invalid",
                reason="invalid_refresh_token",
            )

        account_id, metadata, jwt_expiry = _jwt_metadata(access_token)
        raw_id_token = payload.get("id_token")
        if isinstance(raw_id_token, str) and raw_id_token.strip():
            id_account_id, id_metadata, _ = _jwt_metadata(raw_id_token)
            account_id = account_id or id_account_id
            for key, value in id_metadata.items():
                metadata.setdefault(key, value)
        if account_id is None:
            response_account_id = _safe_string(payload.get("account_id"))
            if response_account_id is not None:
                account_id = response_account_id
                metadata["account_id"] = response_account_id
        if previous is not None:
            account_id = account_id or previous.account_id
            for key in ("account_id", "workspace_id", "email", "name"):
                if key not in metadata and key in previous.metadata:
                    prior_value = _safe_string(previous.metadata[key])
                    if prior_value is not None:
                        metadata[key] = prior_value

        for key, value in tuple(metadata.items()):
            if value == access_token or (
                isinstance(refresh_token, str) and value == refresh_token
            ):
                del metadata[key]
        if account_id in {access_token, refresh_token}:
            account_id = None
        label = metadata.get("email") or metadata.get("name") or account_id
        if label in {access_token, refresh_token}:
            label = None
        if label is None and previous is not None:
            label = previous.account_label
            if label in {
                access_token,
                refresh_token,
                previous.access_token.get_secret_value(),
                previous.refresh_token.get_secret_value()
                if previous.refresh_token is not None
                else None,
            }:
                label = None
        if label is None:
            label = _DEFAULT_ACCOUNT_LABEL
        expires_at = _token_expiry(payload, jwt_expiry=jwt_expiry, previous=previous)
        return SubscriptionCredential(
            provider=self.provider,
            account_label=label,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
            metadata=metadata,
        )

    def _load_optional(self) -> SubscriptionCredential | None:
        loader = getattr(self._store, "load", None)
        if not callable(loader):
            loader = getattr(self._store, "get", None)
        if not callable(loader):
            raise _error(
                SubscriptionAuthError,
                "Codex subscription store is unavailable",
                reason="store_unavailable",
            )
        try:
            credential = loader(self.provider)
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex subscription credential could not be loaded",
                reason="store_unavailable",
            ) from None
        if credential is None:
            return None
        if isinstance(credential, SubscriptionCredential):
            return credential
        try:
            return SubscriptionCredential.model_validate(credential)
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex subscription credential is invalid",
                reason="corrupt_record",
            ) from None

    def _load_required(self) -> SubscriptionCredential:
        credential = self._load_optional()
        if credential is None:
            raise _error(
                SubscriptionTokenExpired,
                "Codex subscription authentication is required",
                reason="missing_credential",
            )
        return credential

    def _save(self, credential: SubscriptionCredential) -> None:
        saver = getattr(self._store, "save", None)
        if not callable(saver):
            raise _error(
                SubscriptionAuthError,
                "Codex subscription store is unavailable",
                reason="store_unavailable",
            )
        try:
            try:
                saver(self.provider, credential)
            except TypeError:
                saver(credential)
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex subscription credential could not be saved",
                reason="store_unavailable",
            ) from None

    def _provider_operation(self) -> Any:
        provider_lock = getattr(self._store, "provider_lock", None)
        return (
            provider_lock(self.provider) if callable(provider_lock) else nullcontext()
        )

    def refresh(self) -> SubscriptionCredential:
        with self._provider_operation():
            return self._refresh_locked()

    def _refresh_locked(self) -> SubscriptionCredential:
        current = self._load_required()
        refresh_token = current.refresh_token
        if refresh_token is None:
            raise _error(
                SubscriptionTokenExpired,
                "Codex subscription authentication has expired",
                status=401,
                reason="missing_refresh_token",
            )
        exchanger = getattr(self._oauth, "refresh", None)
        if not callable(exchanger):
            exchanger = getattr(self._oauth, "refresh_token", None)
        if not callable(exchanger):
            raise _error(
                SubscriptionTokenExpired,
                "Codex subscription refresh is unavailable",
                status=401,
                reason="refresh_unavailable",
            )
        try:
            try:
                payload = exchanger(
                    _TOKEN_ENDPOINT,
                    refresh_token=refresh_token.get_secret_value(),
                    client_id=_CLIENT_ID,
                    provider=self.provider,
                )
            except TypeError:
                payload = exchanger(
                    _TOKEN_ENDPOINT,
                    refresh_token=refresh_token.get_secret_value(),
                    client_id=_CLIENT_ID,
                )
        except SubscriptionError as exc:
            raise _error(
                SubscriptionTokenExpired,
                "Codex subscription refresh failed",
                status=exc.status,
                reason="refresh_failed",
            ) from None
        except Exception:
            raise _error(
                SubscriptionTokenExpired,
                "Codex subscription refresh failed",
                status=401,
                reason="refresh_failed",
            ) from None
        try:
            replacement = self._credential_from_token_response(
                payload, previous=current
            )
            self._save(replacement)
            return replacement
        except SubscriptionTokenExpired:
            raise
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Codex subscription refresh response is invalid",
                reason="malformed_token_response",
            ) from None

    def logout(self) -> None:
        with self._provider_operation():
            deleter = getattr(self._store, "delete", None)
            if not callable(deleter):
                deleter = getattr(self._store, "remove", None)
            if not callable(deleter):
                raise _error(
                    SubscriptionAuthError,
                    "Codex subscription store is unavailable",
                    reason="store_unavailable",
                )
            try:
                deleter(self.provider)
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Codex subscription credential could not be removed",
                    reason="store_unavailable",
                ) from None

    def status(self) -> SubscriptionStatus:
        credential = self._load_optional()
        if credential is None:
            return SubscriptionStatus(provider=self.provider, authenticated=False)
        now = datetime.now(UTC)
        expires_at = credential.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        authenticated = expires_at is None or expires_at > now
        _, current_metadata, _ = _jwt_metadata(
            credential.access_token.get_secret_value()
        )
        account_label = (
            current_metadata.get("email")
            or current_metadata.get("name")
            or credential.account_label
        )
        secret_values = [credential.access_token.get_secret_value()]
        if credential.refresh_token is not None:
            secret_values.append(credential.refresh_token.get_secret_value())
        if any(secret and secret in account_label for secret in secret_values):
            account_label = _DEFAULT_ACCOUNT_LABEL
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=authenticated,
            account_label=account_label,
            expires_at=expires_at,
            metadata=credential.redacted_metadata,
        )

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        """Return structured models authorized for the stored Codex account."""

        credential = self._load_required()
        status, payload = self._get_models(credential)
        if status == 401:
            try:
                credential = self.refresh()
            except SubscriptionTokenExpired as exc:
                if exc.status is None:
                    raise _error(
                        SubscriptionTokenExpired,
                        "Codex subscription authentication has expired",
                        status=401,
                        reason="refresh_failed",
                    ) from None
                raise
            status, payload = self._get_models(credential)
            if status == 401:
                raise _error(
                    SubscriptionTokenExpired,
                    "Codex subscription authentication has expired",
                    status=401,
                    reason="refresh_rejected",
                )
        if status < 200 or status >= 300:
            raise self._map_http_error(status)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                SubscriptionTransportError,
                "Codex model catalog response is invalid",
                reason="malformed_model_catalog",
            ) from None
        if not isinstance(decoded, Mapping):
            raise _error(
                SubscriptionTransportError,
                "Codex model catalog response is invalid",
                reason="malformed_model_catalog",
            )
        raw_models = decoded.get("models")
        if not isinstance(raw_models, Sequence) or isinstance(
            raw_models, (str, bytes, bytearray)
        ):
            raise _error(
                SubscriptionTransportError,
                "Codex model catalog response is invalid",
                reason="malformed_model_catalog",
            )
        models: list[SubscriptionModel] = []
        seen: set[str] = set()
        for index, raw_model in enumerate(raw_models):
            if not isinstance(raw_model, Mapping):
                continue
            if _safe_string(raw_model.get("visibility")) != "list":
                continue
            slug = _safe_string(raw_model.get("slug"))
            if slug is None or "/" in slug or slug in seen:
                continue
            display_name = _safe_string(raw_model.get("display_name")) or slug
            raw_priority = raw_model.get("priority")
            priority = (
                raw_priority
                if isinstance(raw_priority, int)
                and not isinstance(raw_priority, bool)
                and raw_priority >= 0
                else index
            )
            seen.add(slug)
            models.append(
                SubscriptionModel(
                    model_id=slug,
                    display_name=display_name,
                    reasoning=resolve_reasoning_profile(
                        "openai-codex",
                        slug,
                        advertised_efforts=raw_model.get("supported_reasoning_levels"),
                        advertised_default=raw_model.get("default_reasoning_level"),
                    ),
                    provider_order=priority,
                )
            )
        return tuple(models)

    def _build_request_body(
        self, request: CompletionRequest, *, structured: bool
    ) -> dict[str, Any]:
        native_model = resolve_subscription_model(self.provider, request.model)
        if native_model != request.model:
            request = request.model_copy(update={"model": native_model})
        inputs: list[dict[str, Any]] = []
        instructions: list[str] = []
        for message in request.messages:
            if not isinstance(message, Mapping):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex does not support this message shape",
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
                    "Codex tool messages are unsupported by this backend",
                    reason="tool_input_unsupported",
                )
            role = message.get("role")
            if not isinstance(role, str):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message role is unsupported",
                    reason="unsupported_message",
                )
            if role in {"tool", "function"}:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex tool messages are unsupported by this backend",
                    reason="tool_input_unsupported",
                )
            content = message.get("content")
            content_parts = self._message_parts(content)
            if role in {"system", "developer"}:
                if any(part["type"] != "input_text" for part in content_parts):
                    raise _error(
                        SubscriptionUnsupportedRequest,
                        "Codex system messages support text content only",
                        reason="unsupported_message_content",
                    )
                instructions.extend(part["text"] for part in content_parts)
            elif role in {"user", "assistant"}:
                inputs.append(
                    {
                        "role": role,
                        "content": content_parts,
                    }
                )
            else:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message role is unsupported",
                    reason="unsupported_message",
                )

        body: dict[str, Any] = {
            "model": request.model,
            "store": False,
            "stream": True,
            "instructions": "\n".join(instructions)
            if instructions
            else "You are a helpful assistant.",
            "input": inputs,
        }
        # Codex Responses rejects provider-neutral token-limit parameters.
        if request.reasoning is not None:
            profile = resolve_reasoning_profile("openai-codex", request.model)
            effort = choose_supported_effort(profile, request.reasoning)
            if effort is not None:
                body["reasoning"] = {"effort": effort}
        if request.cache_key:
            body["prompt_cache_key"] = request.cache_key
        if structured:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "mudidi_response",
                    "schema": _as_plain_json(request.schema),
                    "strict": True,
                }
            }
        else:
            body["text"] = {"verbosity": "low"}
        return body

    def _validated_data_uri(
        self,
        value: str,
        *,
        mime_type: str | None = None,
        encoded_data: bool = False,
    ) -> str:
        if not isinstance(value, str):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image data URI is malformed",
                reason="image_input_unsupported",
            )
        if encoded_data:
            if (
                not isinstance(mime_type, str)
                or mime_type.lower() not in _IMAGE_MIME_TYPES
            ):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex image input has an unsupported media type",
                    reason="image_input_unsupported",
                )
            value = f"data:{mime_type.lower()};base64,{value}"
        header, separator, encoded = value.partition(",")
        if not header.lower().startswith("data:") or not separator:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image data URI is malformed",
                reason="image_input_unsupported",
            )
        parameters = header[5:].split(";")
        parsed_mime = parameters[0].lower()
        parameter_tokens = [token.lower() for token in parameters[1:]]
        if (
            parsed_mime not in _IMAGE_MIME_TYPES
            or (
                mime_type is not None
                and (not isinstance(mime_type, str) or parsed_mime != mime_type.lower())
            )
            or parameter_tokens.count("base64") != 1
            or len(parameter_tokens) != 1
            or not encoded
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image data URI is malformed",
                reason="image_input_unsupported",
            )
        try:
            base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeError):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image data URI is malformed",
                reason="image_input_unsupported",
            ) from None
        return value

    def _image_url(self, part: Mapping[str, Any]) -> str:
        mime_type: Any = None
        mime_type_supplied = False
        for key in ("mime_type", "mimeType", "media_type"):
            if key in part:
                mime_type = part[key]
                mime_type_supplied = True
                break
        nested = part.get("image_url")
        if nested is None:
            nested = part.get("imageUrl")
        source = part.get("source")
        if isinstance(source, Mapping):
            if not mime_type_supplied:
                for key in ("media_type", "mime_type", "mimeType"):
                    if key in source:
                        mime_type = source[key]
                        mime_type_supplied = True
                        break
            data = source.get("data")
            if data is None:
                data = source.get("base64")
            if not isinstance(data, str):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex image input is malformed",
                    reason="image_input_unsupported",
                )
            return self._validated_data_uri(
                data,
                mime_type=mime_type,
                encoded_data=True,
            )
        if isinstance(nested, Mapping):
            url = nested.get("url") or nested.get("uri")
            if not mime_type_supplied:
                for key in ("mime_type", "mimeType", "media_type"):
                    if key in nested:
                        mime_type = nested[key]
                        mime_type_supplied = True
                        break
        elif isinstance(nested, str):
            url = nested
        else:
            url = part.get("url") or part.get("uri")
        if mime_type_supplied and (
            not isinstance(mime_type, str)
            or not mime_type
            or mime_type.lower() not in _IMAGE_MIME_TYPES
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image input has an unsupported media type",
                reason="image_input_unsupported",
            )
        if not isinstance(url, str) or not url.strip():
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image input is malformed",
                reason="image_input_unsupported",
            )
        url = url.strip()
        if url.lower().startswith("data:"):
            return self._validated_data_uri(url, mime_type=mime_type)
        try:
            parsed = urlsplit(url)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex image URL must be an HTTPS URL or image data URI",
                reason="image_input_unsupported",
            )
        return url

    def _file_part(self, part: Mapping[str, Any]) -> dict[str, Any]:
        file_value = part.get("file")
        if not isinstance(file_value, Mapping):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex file input is malformed",
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
                "Codex file input must be an application/pdf document",
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
                "Codex file input must contain exactly one data source",
                reason="file_input_unsupported",
            )
        source_kind, source_value = sources[0]

        if source_kind == "file_data":
            if not isinstance(source_value, str):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex PDF data URI is malformed",
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
                    "Codex PDF data URI is malformed",
                    reason="file_input_unsupported",
                )
            try:
                base64.b64decode(encoded.encode("ascii"), validate=True)
            except (ValueError, UnicodeError):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex PDF data URI is malformed",
                    reason="file_input_unsupported",
                ) from None

            filename = file_value.get("filename", part.get("filename", "document.pdf"))
            selected_filename = _safe_string(filename)
            if (
                selected_filename is None
                or selected_filename != selected_filename.strip()
                or "/" in selected_filename
                or "\\" in selected_filename
                or not selected_filename.lower().endswith(".pdf")
                or any(ord(character) < 32 for character in selected_filename)
            ):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex PDF filename is malformed",
                    reason="file_input_unsupported",
                )
            return {
                "type": "input_file",
                "filename": selected_filename,
                "file_data": source_value,
            }

        if not isinstance(source_value, str) or not source_value.strip():
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex file reference is malformed",
                reason="file_input_unsupported",
            )
        selected_reference = source_value.strip()
        try:
            parsed = urlsplit(selected_reference)
        except ValueError:
            parsed = None
        if (
            parsed is not None
            and parsed.scheme.lower() == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not any(character in selected_reference for character in "\r\n\x00")
        ):
            return {"type": "input_file", "file_url": selected_reference}
        if (
            source_kind == "file_id"
            and selected_reference.startswith("file-")
            and len(selected_reference) <= 512
            and all(
                character.isalnum() or character in "-_"
                for character in selected_reference
            )
        ):
            return {"type": "input_file", "file_id": selected_reference}
        raise _error(
            SubscriptionUnsupportedRequest,
            "Codex file reference is unsupported",
            reason="file_input_unsupported",
        )

    def _message_parts(self, content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            if not content:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message content is empty",
                    reason="unsupported_message_content",
                )
            return [{"type": "input_text", "text": content}]
        if isinstance(content, Mapping):
            content = [content]
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex message content is unsupported",
                reason="unsupported_message_content",
            )
        values: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, Mapping):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message content is unsupported",
                    reason="unsupported_message_content",
                )
            kind = part.get("type")
            if kind == "file":
                values.append(self._file_part(part))
                continue
            if kind in {"image", "image_url", "input_image"} or "image_url" in part:
                values.append(
                    {"type": "input_image", "image_url": self._image_url(part)}
                )
                continue
            if kind in {"input_audio", "audio"}:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex audio input is unsupported by this backend",
                    reason="unsupported_message_content",
                )
            if kind not in {None, "text", "input_text", "output_text"}:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message content is unsupported",
                    reason="unsupported_message_content",
                )
            text = part.get("text")
            if not isinstance(text, str) or not text:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Codex message content is unsupported",
                    reason="unsupported_message_content",
                )
            values.append({"type": "input_text", "text": text})
        if not values:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex message content is empty",
                reason="unsupported_message_content",
            )
        return values

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

    def _read_response(self, response: Any) -> tuple[int, bytes]:
        if isinstance(response, Mapping):
            return 200, json.dumps(response, ensure_ascii=True).encode("utf-8")
        if isinstance(response, str):
            return 200, response.encode("utf-8")
        if isinstance(response, (bytes, bytearray, memoryview)):
            return 200, bytes(response)
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "status_code"):
            status = response.status_code
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is None:
            status = 200
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray, memoryview)):
            raw = bytes(content)
            close = getattr(response, "close", None)
            if callable(close):
                close()
            return int(status), raw
        reader = getattr(response, "read", None)
        if not callable(reader):
            raise TypeError("Codex response body is not readable")
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
            raise TypeError("Codex response body is not bytes")
        return int(status), bytes(raw)

    def _validate_response_target(self, response: Any) -> None:
        target = getattr(response, "geturl", None)
        if callable(target):
            target = target()
        if target is None:
            target = getattr(response, "url", None)
        if target is None:
            return
        try:
            validate_provider_url(target, allowed_hosts=set(_CODEX_HOSTS))
        except SubscriptionError:
            raise _error(
                SubscriptionTransportError,
                "Codex response endpoint redirect is not allowed",
                reason="endpoint_redirect",
            ) from None

    def _post_json(
        self, credential: SubscriptionCredential, body: Mapping[str, Any]
    ) -> tuple[int, bytes]:
        validate_provider_url(_RESPONSES_ENDPOINT, allowed_hosts=set(_CODEX_HOSTS))
        data = json.dumps(
            _as_plain_json(body), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        headers = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "originator": _ORIGINATOR,
            "Authorization": f"Bearer {credential.access_token.get_secret_value()}",
        }
        if credential.account_id:
            headers["chatgpt-account-id"] = credential.account_id
        request = Request(
            _RESPONSES_ENDPOINT,
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
                "Codex request could not be delivered",
                reason="transport_error",
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Codex response could not be read",
                reason="response_error",
            ) from None
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Codex response is too large",
                status=status,
                reason="response_too_large",
            )
        return status, payload

    def _map_http_error(self, status: int) -> SubscriptionError:
        if status == 401:
            return _error(
                SubscriptionAuthError,
                "Codex authentication was rejected",
                status=status,
                reason="authentication_rejected",
            )
        if status == 403:
            return _error(
                SubscriptionPolicyError,
                "Codex subscription policy rejected the request",
                status=status,
                reason="policy_rejected",
            )
        if status == 429:
            return _error(
                SubscriptionTransportError,
                "Codex subscription rate limit reached",
                category="rate_limited",
                status=status,
                reason="rate_limited",
            )
        if 500 <= status <= 599:
            return _error(
                SubscriptionTransportError,
                "Codex subscription service is unavailable",
                category="provider_unavailable",
                status=status,
                reason="provider_unavailable",
            )
        if 400 <= status <= 499:
            return _error(
                SubscriptionAuthError,
                "Codex request was rejected",
                status=status,
                reason="provider_client_error",
            )
        return _error(
            SubscriptionTransportError,
            "Codex request failed",
            status=status,
            reason="provider_error",
        )

    def _get_models(self, credential: SubscriptionCredential) -> tuple[int, bytes]:
        validate_provider_url(_MODELS_ENDPOINT, allowed_hosts=set(_CODEX_HOSTS))
        headers = {
            "Accept": "application/json",
            "originator": _ORIGINATOR,
            "Authorization": f"Bearer {credential.access_token.get_secret_value()}",
        }
        if credential.account_id:
            headers["chatgpt-account-id"] = credential.account_id
        request = Request(_MODELS_ENDPOINT, headers=headers, method="GET")
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
                "Codex model catalog could not be loaded",
                reason="transport_error",
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Codex model catalog response could not be read",
                reason="response_error",
            ) from None
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Codex model catalog response is too large",
                status=status,
                reason="response_too_large",
            )
        return status, payload

    def _normalize_response(
        self,
        request: CompletionRequest,
        body: bytes,
        *,
        structured: bool,
    ) -> CompletionResult:
        documents = _parse_json_documents(body)
        text, response = _response_payload(documents)
        if not text and "output_text" not in response and "output" not in response:
            raise _error(
                SubscriptionTransportError,
                "Codex response did not contain visible text",
                reason="missing_visible_text",
            )
        structured_json: Any | None = None
        if structured:
            candidate = response.get("structured_json")
            if candidate is None:
                candidate = response.get("structured_output")
            if candidate is None:
                candidate = response.get("output_parsed")
            if isinstance(candidate, str):
                try:
                    candidate = json.loads(candidate)
                except (TypeError, json.JSONDecodeError):
                    candidate = None
            if candidate is None:
                try:
                    candidate = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    raise _error(
                        SubscriptionTransportError,
                        "Codex structured output is invalid JSON",
                        reason="invalid_structured_output",
                    ) from None
            if not _schema_matches(candidate, request.schema or {}):
                raise _error(
                    SubscriptionTransportError,
                    "Codex structured output does not match the requested schema",
                    reason="structured_schema_mismatch",
                )
            structured_json = candidate
        model = response.get("model")
        selected_model = model if isinstance(model, str) and model else request.model
        return CompletionResult(
            text=text,
            structured_json=structured_json,
            usage=_extract_usage(response),
            finish_reason=_finish_reason(response),
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
                        "Codex subscription authentication has expired",
                        status=401,
                        reason="refresh_failed",
                    ) from None
                raise
            status, payload = self._post_json(credential, body)
            if status == 401:
                raise _error(
                    SubscriptionTokenExpired,
                    "Codex subscription authentication has expired",
                    status=401,
                    reason="refresh_rejected",
                )
        if status < 200 or status >= 300:
            raise self._map_http_error(status)
        return self._normalize_response(request, payload, structured=structured)

    def complete(
        self, request: CompletionRequest | Mapping[str, Any]
    ) -> CompletionResult:
        normalized = _coerce_request(request)
        structured = normalized.schema is not None
        if structured:
            _require_supported_schema(normalized.schema)
        return self._complete(normalized, structured=structured)

    def complete_structured(
        self, request: CompletionRequest | Mapping[str, Any]
    ) -> CompletionResult:
        normalized = _coerce_request(request)
        if normalized.schema is None:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Codex structured completion requires a JSON schema",
                reason="missing_schema",
            )
        _require_supported_schema(normalized.schema)
        return self._complete(normalized, structured=True)


__all__ = ["OpenAICodexBackend"]
