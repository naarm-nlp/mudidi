"""Direct Google Antigravity subscription routing.

MUDIDI owns a browser OAuth session in its encrypted subscription store and
calls Antigravity's Cloud Code Assist transport directly. It never reads OMP,
Antigravity CLI, gcloud, or browser credential stores and never falls back to
an API key or another provider.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import replace
import base64
from datetime import UTC, datetime, timedelta
import json
import math
import re
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

from mudidi.llm.reasoning import (
    ReasoningEffort,
    choose_supported_effort,
    resolve_reasoning_profile,
)
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

# Google OAuth registration is deployment configuration. Client secrets never
# enter source control or subscription worker environments.
GOOGLE_OAUTH_CLIENT_ID_ENV = "MUDIDI_GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_OAUTH_CLIENT_SECRET_ENV = "MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET"
GOOGLE_CLOUD_PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"

# Google OAuth endpoints are provider-owned and deliberately private to this
# adapter.
_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
_SCOPE = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/experimentsandconfigs"
)
_REDIRECT_PATH = "/oauth-callback"
_LOGIN_REDIRECT_URI = "http://127.0.0.1:51121/oauth-callback"


# Antigravity uses the daily Cloud Code Assist deployment. The service gates
# models by its captured desktop-client user agent.
_CLOUD_CODE_PRODUCTION_BASE = "https://daily-cloudcode-pa.googleapis.com"
_CLOUD_CODE_SANDBOX_BASE = "https://daily-cloudcode-pa.sandbox.googleapis.com"
_CLOUD_CODE_BASE_ENDPOINT = f"{_CLOUD_CODE_PRODUCTION_BASE}/v1internal"
_CLOUD_CODE_PRODUCTION_ENDPOINT = (
    f"{_CLOUD_CODE_BASE_ENDPOINT}:streamGenerateContent?alt=sse"
)
_CLOUD_CODE_SANDBOX_ENDPOINT = (
    f"{_CLOUD_CODE_SANDBOX_BASE}/v1internal:streamGenerateContent?alt=sse"
)
_CLOUD_CODE_ENDPOINTS = {
    "production": _CLOUD_CODE_PRODUCTION_ENDPOINT,
    "sandbox": _CLOUD_CODE_SANDBOX_ENDPOINT,
}
_LOAD_CODE_ASSIST_ENDPOINT = f"{_CLOUD_CODE_BASE_ENDPOINT}:loadCodeAssist"
_ONBOARD_USER_ENDPOINT = f"{_CLOUD_CODE_BASE_ENDPOINT}:onboardUser"
_FETCH_AVAILABLE_MODELS_ENDPOINT = f"{_CLOUD_CODE_BASE_ENDPOINT}:fetchAvailableModels"
_OAUTH_HOSTS = frozenset(
    {"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"}
)
_CLOUD_CODE_HOSTS = frozenset(
    {
        "daily-cloudcode-pa.googleapis.com",
        "daily-cloudcode-pa.sandbox.googleapis.com",
    }
)

_ANTIGRAVITY_USER_AGENT = (
    "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)"
)
_CLIENT_METADATA = {"ideType": "ANTIGRAVITY"}
_GEMINI_25_REASONING_BUDGETS = {
    "minimal": 1024,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}
_MAX_OPERATION_POLL_ATTEMPTS = 5
_MAX_OPERATION_POLL_DELAY = 5.0

_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 300.0
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1_048_576
_MAX_IDENTIFIER_LENGTH = 512
_MAX_SSE_LINE_LENGTH = 1_048_576
_MAX_TRANSIENT_RETRIES = 1
_TRANSIENT_STATUSES = frozenset({408, 425, 429, 499, 500, 502, 503, 504})

_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "additionalProperties",
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
_GOOGLE_INLINE_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "text/csv",
        "text/html",
        "text/plain",
        "audio/aac",
        "audio/aiff",
        "audio/flac",
        "audio/mp3",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "video/3gpp",
        "video/avi",
        "video/mpeg",
        "video/mp4",
        "video/mov",
        "video/mpeg",
        "video/webm",
        "video/wmv",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_GOOGLE_FILE_SCHEMES = frozenset({"gs", "https"})
_GOOGLE_FILE_HOSTS = frozenset(
    {
        "cloudcode-pa.googleapis.com",
        "generativelanguage.googleapis.com",
        "storage.googleapis.com",
        "www.googleapis.com",
    }
)
_IMAGE_TYPES = frozenset(
    {
        "file",
        "image",
        "image_url",
        "input_image",
        "inline_data",
        "inlineData",
        "file_data",
        "fileData",
    }
)
_TEXT_TYPES = frozenset({None, "text", "input_text", "output_text"})
_MISSING = object()


_NO_REDIRECT_OPENER = _build_no_redirect_opener()


def _error(
    error_type: type[SubscriptionError],
    message: str,
    *,
    category: str | None = None,
    status: int | None = None,
    reason: str | None = None,
    credential: SubscriptionCredential | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SubscriptionError:
    error_metadata: dict[str, Any] = dict(metadata or {})
    if reason is not None:
        error_metadata.setdefault("reason", reason)
    secret_values: list[str] = []
    if credential is not None:
        secret_values.append(credential.access_token.get_secret_value())
        if credential.refresh_token is not None:
            secret_values.append(credential.refresh_token.get_secret_value())
    return error_type(
        message,
        provider=SubscriptionProvider.GOOGLE,
        category=category,
        status=status,
        metadata=error_metadata,
        secret_values=secret_values,
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


def _parse_structured_text(text: str) -> Any:
    """Decode JSON, accepting the single fenced block emitted by Antigravity."""

    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_newline = candidate.find("\n")
        if first_newline >= 0:
            fence_language = candidate[3:first_newline].strip().lower()
            if fence_language in {"", "json", "application/json"}:
                candidate = candidate[first_newline + 1 : -3].strip()
    return json.loads(candidate)


def _safe_string(value: Any, *, maximum: int = _MAX_IDENTIFIER_LENGTH) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return None
    if any(character in value for character in "\r\n\x00"):
        return None
    return value.strip()


def _logical_model_id(model: str) -> str:
    """Collapse an effort-qualified Gemini model to its dashboard identity."""
    if model.endswith("-tiered"):
        base = model.removesuffix("-tiered")
        if base.startswith("gemini-3.") and "-flash" in base:
            return base

    for suffix in ("-extra-low", "-low", "-medium", "-high"):
        if not model.endswith(suffix):
            continue
        base = model[: -len(suffix)]
        if base.startswith("gemini-3.") and ("-flash" in base or base.endswith("-pro")):
            return base
    return model


def _gemini_display_name(model_id: str) -> str:
    """Return a stable display name for a logical Gemini model ID."""

    return f"Gemini {model_id.removeprefix('gemini-').replace('-', ' ').title()}"


def _canonical_model_id(
    model_id: str,
    metadata: object,
    available_model_ids: frozenset[str],
) -> str:
    """Collapse provider-declared aliases onto an available canonical tag."""

    tag = (
        _safe_string(metadata.get("tagDescription"))
        if isinstance(metadata, Mapping)
        else None
    )
    if (
        tag is not None
        and tag.startswith("gemini-")
        and "/" not in tag
        and tag in available_model_ids
    ):
        return _logical_model_id(tag)
    return _logical_model_id(model_id)


def _wire_model_id(model: str, reasoning: str | None) -> str:
    """Route a portable effort to Antigravity's effort-qualified wire ID."""

    logical = _logical_model_id(model)
    if reasoning is None:
        return model
    profile = resolve_reasoning_profile("google-antigravity", logical)
    level = choose_supported_effort(profile, reasoning)
    if level is None:
        return model
    if re.fullmatch(r"gemini-3\.\d+-flash", logical):
        wire_level = "low" if level == "minimal" else level
        return f"{logical}-{wire_level}"
    if re.fullmatch(r"gemini-3\.\d+-pro", logical):
        return f"{logical}-{'high' if level == 'high' else 'low'}"
    return model


def google_oauth_client_id_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the deployment's configured Google OAuth client ID."""

    source = os.environ if environ is None else environ
    return _safe_string(source.get(GOOGLE_OAUTH_CLIENT_ID_ENV))


def google_oauth_client_secret_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the deployment's optional Google OAuth client secret."""

    source = os.environ if environ is None else environ
    return _safe_string(source.get(GOOGLE_OAUTH_CLIENT_SECRET_ENV))


def google_cloud_project_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the optional Google Cloud project used by Code Assist."""

    source = os.environ if environ is None else environ
    return _safe_project_id(source.get(GOOGLE_CLOUD_PROJECT_ENV))


def _safe_project_id(value: Any) -> str | None:
    project = _safe_string(value)
    if project is None:
        return None
    if project.startswith("projects/"):
        project = _safe_string(project.split("/", 1)[1])
    if (
        project is None
        or project.startswith(("http://", "https://"))
        or "/" in project
        or any(character.isspace() for character in project)
        or project.isdigit()
    ):
        return None
    return project


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


def _schema_is_supported(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if any(key not in _SUPPORTED_SCHEMA_KEYWORDS for key in schema):
        return False
    schema_type = schema.get("type")
    if isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes, bytearray)
    ):
        if not schema_type or any(item not in _SCHEMA_TYPES for item in schema_type):
            return False
    elif schema_type is not None and (
        not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES
    ):
        return False
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes, bytearray))
        or any(not isinstance(item, str) for item in required)
    ):
        return False
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            return False
        if any(not isinstance(key, str) for key in properties):
            return False
        if any(not _schema_is_supported(child) for child in properties.values()):
            return False
    items = schema.get("items")
    if items is not None and not _schema_is_supported(items):
        return False
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        return False
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray))
    ):
        return False
    return True


def _schema_matches(value: Any, schema: Mapping[str, Any]) -> bool:
    schema_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray)),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes, bytearray)
    ):
        if not any(type_matches.get(item, False) for item in schema_type):
            return False
    elif isinstance(schema_type, str) and not type_matches.get(schema_type, False):
        return False
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray)):
        if not any(json_values_equal(value, candidate) for candidate in enum):
            return False
    if "const" in schema and not json_values_equal(value, schema["const"]):
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
        if additional is False and any(key not in known_properties for key in value):
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
            "Google structured completion schema uses unsupported keywords",
            reason="unsupported_schema_keyword",
        )


def _decode_jwt_claims(token: str) -> Mapping[str, Any] | None:
    """Decode a JWT payload in memory for whitelisted identity claims only."""

    if not isinstance(token, str):
        return None
    pieces = token.split(".")
    if len(pieces) != 3:
        return None
    try:
        encoded = pieces[1] + "=" * (-len(pieces[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _token_expiry(
    payload: Mapping[str, Any],
    *,
    previous: SubscriptionCredential | None = None,
) -> datetime | None:
    raw_expires_at = payload.get("expires_at")
    if raw_expires_at is not None:
        if isinstance(raw_expires_at, datetime):
            result = raw_expires_at
        elif isinstance(raw_expires_at, str):
            try:
                result = datetime.fromisoformat(raw_expires_at.replace("Z", "+00:00"))
            except ValueError:
                raise _error(
                    SubscriptionAuthError,
                    "Google OAuth token response is invalid",
                    reason="invalid_expiry",
                ) from None
        else:
            raise _error(
                SubscriptionAuthError,
                "Google OAuth token response is invalid",
                reason="invalid_expiry",
            )
        return result if result.tzinfo is not None else result.replace(tzinfo=UTC)

    expires_in = payload.get("expires_in")
    if expires_in is None:
        return previous.expires_at if previous is not None else None
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
        raise _error(
            SubscriptionAuthError,
            "Google OAuth token response is invalid",
            reason="invalid_expiry",
        )
    try:
        if not math.isfinite(float(expires_in)) or expires_in < 0 or expires_in > 10**9:
            raise ValueError
    except (OverflowError, TypeError, ValueError):
        raise _error(
            SubscriptionAuthError,
            "Google OAuth token response is invalid",
            reason="invalid_expiry",
        ) from None
    return datetime.now(UTC) + timedelta(seconds=float(expires_in))


def _media_error(message: str) -> SubscriptionUnsupportedRequest:
    return _error(
        SubscriptionUnsupportedRequest,
        message,
        reason="image_input_unsupported",
    )


def _validated_mime_type(value: Any) -> str:
    if not isinstance(value, str):
        raise _media_error("Google media input has an unsupported media type")
    selected = value.strip().lower()
    if selected not in _GOOGLE_INLINE_MIME_TYPES:
        raise _media_error("Google media input has an unsupported media type")
    return selected


def _validated_inline_data(
    data: Any,
    declared_mime: Any = _MISSING,
) -> dict[str, str]:
    if not isinstance(data, str) or not data:
        raise _media_error("Google inline media data is malformed")
    selected_mime = (
        None if declared_mime is _MISSING else _validated_mime_type(declared_mime)
    )
    if data.lower().startswith("data:"):
        header, separator, encoded = data.partition(",")
        parameters = header[5:].split(";") if header.lower().startswith("data:") else []
        if (
            not separator
            or len(parameters) != 2
            or parameters[1].strip().lower() != "base64"
        ):
            raise _media_error("Google media data URI is malformed")
        parsed_mime = _validated_mime_type(parameters[0])
        if selected_mime is not None and selected_mime != parsed_mime:
            raise _media_error("Google media MIME type disagrees with its data URI")
        selected_mime = parsed_mime
    else:
        encoded = data
    if selected_mime is None or not encoded:
        raise _media_error("Google inline media data is missing a media type")
    try:
        base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise _media_error("Google inline media data is malformed") from None
    return {"mimeType": selected_mime, "data": encoded}


def _validated_remote_uri(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _media_error("Google remote file reference is malformed")
    selected = value.strip()
    try:
        parsed = urlsplit(selected)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower() if parsed.hostname else None
        port = parsed.port
    except ValueError:
        raise _media_error("Google remote file reference is malformed") from None
    if (
        scheme not in _GOOGLE_FILE_SCHEMES
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or not parsed.path
        or port is not None
        or any(character in selected for character in "\r\n\x00")
    ):
        raise _media_error("Google remote file reference is unsupported")
    if scheme == "https" and hostname not in _GOOGLE_FILE_HOSTS:
        raise _media_error("Google remote file reference is unsupported")
    return selected


def _merged_mime_type(current: Any, candidate: Any) -> Any:
    if candidate is _MISSING:
        return current
    if current is _MISSING:
        return candidate
    if (
        not isinstance(current, str)
        or not isinstance(candidate, str)
        or current.strip().lower() != candidate.strip().lower()
    ):
        raise _media_error("Google media MIME types disagree")
    return current


def _mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return _MISSING


def _image_part(part: Mapping[str, Any]) -> dict[str, Any]:
    kind = part.get("type")
    mime_type: Any = _mapping_value(
        part,
        "mime_type",
        "mimeType",
        "media_type",
        "mediaType",
    )

    file_value = part.get("file")
    if kind == "file" or isinstance(file_value, Mapping):
        if not isinstance(file_value, Mapping):
            raise _media_error("Google file input is malformed")
        mime_type = _merged_mime_type(
            mime_type,
            _mapping_value(file_value, "format", "mime_type", "mimeType", "media_type"),
        )
        file_data = _mapping_value(
            file_value, "file_data", "fileData", "data", "base64"
        )
        file_id = _mapping_value(file_value, "file_id", "fileId", "uri", "url")
        if file_data is not _MISSING and file_id is not _MISSING:
            raise _media_error("Google file input has conflicting data references")
        if file_data is not _MISSING:
            return {"inlineData": _validated_inline_data(file_data, mime_type)}
        if file_id is not _MISSING:
            return {
                "fileData": {
                    "mimeType": _validated_mime_type(mime_type),
                    "fileUri": _validated_remote_uri(file_id),
                }
            }
        raise _media_error("Google file input is missing file data")

    nested_inline = _mapping_value(part, "inlineData", "inline_data")
    if nested_inline is not _MISSING:
        if not isinstance(nested_inline, Mapping):
            raise _media_error("Google inline media input is malformed")
        mime_type = _merged_mime_type(
            mime_type,
            _mapping_value(
                nested_inline, "mimeType", "mime_type", "mediaType", "media_type"
            ),
        )
        data = _mapping_value(nested_inline, "data", "base64")
        return {"inlineData": _validated_inline_data(data, mime_type)}

    nested_file = _mapping_value(part, "fileData", "file_data")
    if nested_file is not _MISSING:
        if not isinstance(nested_file, Mapping):
            raise _media_error("Google remote file input is malformed")
        mime_type = _merged_mime_type(
            mime_type,
            _mapping_value(
                nested_file, "mimeType", "mime_type", "mediaType", "media_type"
            ),
        )
        file_uri = _mapping_value(nested_file, "fileUri", "file_uri", "uri", "url")
        return {
            "fileData": {
                "mimeType": _validated_mime_type(mime_type),
                "fileUri": _validated_remote_uri(file_uri),
            }
        }

    source = part.get("source")
    if isinstance(source, Mapping):
        mime_type = _merged_mime_type(
            mime_type,
            _mapping_value(source, "media_type", "mime_type", "mimeType", "mediaType"),
        )
        data = _mapping_value(source, "data", "base64")
        if data is not _MISSING:
            return {"inlineData": _validated_inline_data(data, mime_type)}

    nested = part.get("image_url")
    if nested is None:
        nested = part.get("imageUrl")
    if isinstance(nested, Mapping):
        mime_type = _merged_mime_type(
            mime_type,
            _mapping_value(nested, "mime_type", "mimeType", "media_type", "mediaType"),
        )
        url = _mapping_value(nested, "url", "uri")
    elif isinstance(nested, str):
        url = nested
    else:
        url = _mapping_value(part, "url", "uri")

    direct_data = _mapping_value(part, "data", "base64")
    if direct_data is not _MISSING:
        return {"inlineData": _validated_inline_data(direct_data, mime_type)}
    if not isinstance(url, str) or not url.strip():
        raise _media_error("Google media input is malformed")
    url = url.strip()
    if url.lower().startswith("data:"):
        return {"inlineData": _validated_inline_data(url, mime_type)}
    return {
        "fileData": {
            "mimeType": _validated_mime_type(mime_type),
            "fileUri": _validated_remote_uri(url),
        }
    }


class GoogleAntigravityBackend:
    """Google Antigravity subscription backend with browser OAuth."""

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
        client_id: str | None = None,
        client_secret: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        project_id: str | None = None,
        account_id: str | None = None,
        account_label: str | None = None,
        environment: str = "production",
        endpoint: str | None = None,
        cloud_code_endpoint: str | None = None,
        prompt_id_factory: Callable[[], str] | None = None,
        operation_poll_attempts: int = _MAX_OPERATION_POLL_ATTEMPTS,
        operation_poll_delay: float | int = 0.1,
        sleep: Callable[[float], Any] | None = None,
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

        if (
            client_id is not None
            and oauth_client_id is not None
            and client_id != oauth_client_id
        ):
            raise TypeError("client_id and oauth_client_id must match")
        if (
            client_secret is not None
            and oauth_client_secret is not None
            and client_secret != oauth_client_secret
        ):
            raise TypeError("client_secret and oauth_client_secret must match")
        selected_client_id = (
            client_id if client_id is not None else oauth_client_id
        ) or google_oauth_client_id_from_environment()
        selected_client_secret = (
            client_secret if client_secret is not None else oauth_client_secret
        ) or google_oauth_client_secret_from_environment()
        if selected_client_id is not None and _safe_string(selected_client_id) is None:
            raise ValueError("client_id must be a bounded non-empty string")
        if (
            selected_client_secret is not None
            and _safe_string(selected_client_secret) is None
        ):
            raise ValueError("client_secret must be a bounded non-empty string")
        if environment not in _CLOUD_CODE_ENDPOINTS:
            raise ValueError("environment must be production or sandbox")
        if (
            endpoint is not None
            and cloud_code_endpoint is not None
            and endpoint != cloud_code_endpoint
        ):
            raise TypeError("endpoint and cloud_code_endpoint must match")
        selected_endpoint = endpoint if endpoint is not None else cloud_code_endpoint
        if selected_endpoint is None:
            selected_endpoint = _CLOUD_CODE_ENDPOINTS[environment]
        if selected_endpoint not in _CLOUD_CODE_ENDPOINTS.values():
            raise ValueError(
                "Cloud Code Assist endpoint is not an allowlisted deployment endpoint"
            )
        validate_provider_url(selected_endpoint, allowed_hosts=set(_CLOUD_CODE_HOSTS))
        parsed_endpoint = urlsplit(selected_endpoint)
        if (
            parsed_endpoint.path != "/v1internal:streamGenerateContent"
            or parsed_endpoint.query != "alt=sse"
        ):
            raise ValueError(
                "Cloud Code Assist endpoint must use streamGenerateContent with alt=sse"
            )

        selected_project = _safe_project_id(project_id)
        selected_account_id = _safe_string(account_id)
        selected_account_label = _safe_string(account_label)
        if project_id is not None and selected_project is None:
            raise ValueError("project_id must be a bounded valid Google project id")
        if account_id is not None and selected_account_id is None:
            raise ValueError("account_id must be a bounded non-empty string")
        if account_label is not None and selected_account_label is None:
            raise ValueError("account_label must be a bounded non-empty string")
        selected_max = int(max_response_bytes)
        if selected_max <= 0:
            raise ValueError("max_response_bytes must be positive")
        selected_poll_attempts = int(operation_poll_attempts)
        if selected_poll_attempts <= 0:
            raise ValueError("operation_poll_attempts must be positive")
        selected_poll_attempts = min(
            selected_poll_attempts, _MAX_OPERATION_POLL_ATTEMPTS
        )
        selected_poll_delay = float(operation_poll_delay)
        if not math.isfinite(selected_poll_delay) or selected_poll_delay < 0:
            raise ValueError("operation_poll_delay must be finite and non-negative")
        selected_poll_delay = min(selected_poll_delay, _MAX_OPERATION_POLL_DELAY)

        self._store = store
        self._client_id = selected_client_id
        self._client_secret = selected_client_secret
        self._project_id = selected_project
        self._account_id = selected_account_id
        self._account_label = selected_account_label
        self._environment = environment
        self._endpoint = selected_endpoint
        self._timeout = _bounded_timeout(timeout)
        self._callback_timeout = callback_timeout
        self._max_response_bytes = min(selected_max, _DEFAULT_MAX_RESPONSE_BYTES)
        self._opener = opener
        self._listener = listener
        self._receiver = receiver
        self._receiver_factory = receiver_factory
        self._prompt_id_factory = prompt_id_factory or (lambda: secrets.token_hex(16))
        self._operation_poll_attempts = selected_poll_attempts
        self._operation_poll_delay = selected_poll_delay
        self._sleep = sleep or time.sleep

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
        return SubscriptionProvider.GOOGLE

    @property
    def login_redirect_uri(self) -> str:
        """Return Antigravity's registered fixed loopback callback."""

        return _LOGIN_REDIRECT_URI

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            image_input=True,
            structured_output=True,
            streaming=True,
            cancellation=False,
            usage=True,
            reasoning=True,
            model_discovery=True,
        )

    def __repr__(self) -> str:
        return "GoogleAntigravityBackend(provider='google', subscription=True)"

    def build_authorization_url(
        self,
        challenge: PkceChallenge,
        redirect_uri: str,
        *,
        client_id: str | None = None,
    ) -> str:
        if not isinstance(challenge, PkceChallenge):
            raise TypeError("challenge must be a PkceChallenge")
        try:
            parsed_redirect = validate_loopback_redirect_uri(redirect_uri)
            if parsed_redirect.path != _REDIRECT_PATH:
                raise _error(
                    SubscriptionAuthError,
                    "Google OAuth callback URI is invalid",
                    reason="invalid_redirect_uri",
                )
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Google OAuth callback URI is invalid",
                reason="invalid_redirect_uri",
            ) from None
        selected_client_id = client_id if client_id is not None else self._client_id
        if _safe_string(selected_client_id) is None:
            raise _error(
                SubscriptionAuthError,
                "Google OAuth is not configured; set "
                "MUDIDI_GOOGLE_OAUTH_CLIENT_ID and restart MUDIDI",
                reason="oauth_client_configuration_missing",
            )
        parameters: dict[str, Any] = {
            "response_type": "code",
            "client_id": selected_client_id,
            "redirect_uri": redirect_uri,
            "scope": _SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
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
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Google authorization endpoint is unavailable",
                reason="invalid_authorization_endpoint",
            ) from None

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
            port=51121,
            path=_REDIRECT_PATH,
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
                "Google OAuth callback receiver is unavailable",
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
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Google OAuth callback could not be validated",
                    reason="invalid_callback",
                ) from None
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Google OAuth callback could not be validated",
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
                except SubscriptionError:
                    raise
                except Exception:
                    raise _error(
                        SubscriptionAuthError,
                        "Google OAuth callback could not be validated",
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
                    "Google OAuth callback is invalid",
                    reason="invalid_callback",
                )
            callback = OAuthCallback(code=code, state=state)
        if not secrets.compare_digest(callback.state, challenge.state):
            raise _error(
                SubscriptionAuthError,
                "Google OAuth callback state is invalid",
                reason="state_mismatch",
            )
        if not callback.code.strip():
            raise _error(
                SubscriptionAuthError,
                "Google OAuth callback did not contain an authorization code",
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
        """Exchange and persist a Google OAuth credential."""

        exchange = getattr(self._oauth, "exchange_code", None)
        if not callable(exchange):
            exchange = getattr(self._oauth, "exchange_authorization_code", None)
        parameters: dict[str, str] = {}
        if self._client_secret is not None:
            parameters["client_secret"] = self._client_secret
        with self._provider_operation():
            return complete_login_transaction(
                transaction,
                code=code,
                state=state,
                exchange=exchange,
                token_endpoint=_TOKEN_ENDPOINT,
                client_id=self._client_id or "",
                provider=self.provider,
                exchange_parameters=parameters,
                credential_from_token_response=lambda payload: (
                    self._credential_from_token_response(
                        payload,
                        require_project=False,
                        require_account=False,
                    )
                ),
                save=self._save,
            )

    def login(self) -> SubscriptionCredential:
        challenge = PkceChallenge.generate()
        receiver: Any | None = None
        try:
            receiver = self._make_receiver(challenge)
            redirect_uri = getattr(receiver, "redirect_uri", None)
            if not isinstance(redirect_uri, str) or not redirect_uri:
                raise _error(
                    SubscriptionAuthError,
                    "Google OAuth callback URI is unavailable",
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
                    "Google OAuth token exchange is unavailable",
                    reason="token_exchange_unavailable",
                )
            kwargs: dict[str, Any] = {
                "code": callback.code,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "code_verifier": challenge.verifier,
            }
            if self._client_secret is not None:
                kwargs["client_secret"] = self._client_secret
            with self._provider_operation():
                try:
                    payload = exchange(
                        _TOKEN_ENDPOINT, provider=self.provider, **kwargs
                    )
                except TypeError:
                    payload = exchange(_TOKEN_ENDPOINT, **kwargs)
                credential = self._credential_from_token_response(
                    payload,
                    require_project=False,
                    require_account=False,
                )
                credential = self._ensure_account_and_project(credential)
                self._save(credential)
                return credential
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Google OAuth login failed",
                reason="token_exchange_failed",
            ) from None
        finally:
            close = getattr(receiver, "close", None) if receiver is not None else None
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
        require_project: bool = True,
        require_account: bool = True,
    ) -> SubscriptionCredential:

        if not isinstance(payload, Mapping):
            raise _error(
                SubscriptionAuthError,
                "Google OAuth token response is invalid",
                reason="malformed_token_response",
            )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise _error(
                SubscriptionAuthError,
                "Google OAuth token response is missing a required token value",
                reason="missing_access_token",
            )
        token_type = payload.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str) or token_type.strip().lower() != "bearer"
        ):
            raise _error(
                SubscriptionAuthError,
                "Google OAuth token response is invalid",
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
                "Google OAuth token response is invalid",
                reason="invalid_refresh_token",
            )

        claims: dict[str, Any] = {}
        id_token = payload.get("id_token")
        if isinstance(id_token, str):
            decoded_claims = _decode_jwt_claims(id_token)
            if decoded_claims is not None:
                claims.update(decoded_claims)
        for key in (
            "email",
            "name",
            "sub",
            "account_id",
            "accountId",
            "project_id",
            "project",
            "quota_project_id",
            "cloudaicompanionProject",
        ):
            if key in payload:
                claims[key] = payload[key]

        account_id = None
        for key in (
            "account_id",
            "accountId",
            "sub",
            "subject",
            "user_id",
            "userId",
            "email",
        ):
            candidate = _safe_string(claims.get(key))
            if candidate is not None:
                account_id = candidate
                break
        account_id = account_id or self._account_id
        if account_id is None and previous is not None:
            account_id = previous.account_id
        email = _safe_string(claims.get("email"))
        name = _safe_string(claims.get("name"))
        account_label = self._account_label or email or name
        if account_label is None and previous is not None:
            account_label = previous.account_label
        if account_label is None and account_id is not None:
            account_label = account_id
        project_id = self._project_from_payload(claims)
        project_id = project_id or self._project_id
        if project_id is None and previous is not None:
            project_id = self._project_from_payload({"project_id": previous.project_id})
        if require_project and project_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account project metadata is required",
                reason="missing_project_metadata",
            )
        if account_label is None:
            account_label = "Google account"
        if require_account and account_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account metadata is required",
                reason="missing_account_metadata",
            )
        if access_token in {account_id, account_label, project_id} or (
            isinstance(refresh_token, str)
            and refresh_token in {account_id, account_label, project_id}
        ):
            raise _error(
                SubscriptionAuthError,
                "Google account metadata is invalid",
                reason="invalid_account_metadata",
            )

        metadata: dict[str, Any] = {}
        if email is not None:
            metadata["email"] = email
        if name is not None:
            metadata["name"] = name
        if previous is not None:
            for key, value in previous.metadata.items():
                if key not in metadata and isinstance(
                    value, (str, int, float, bool, type(None))
                ):
                    metadata[key] = value
        return SubscriptionCredential(
            provider=self.provider,
            account_label=account_label,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=_token_expiry(payload, previous=previous),
            project_id=project_id,
            account_id=account_id,
            metadata=metadata,
        )

    def _credential_with_metadata(
        self,
        credential: SubscriptionCredential,
        *,
        account_id: str | None = None,
        account_label: str | None = None,
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SubscriptionCredential:
        return SubscriptionCredential(
            provider=self.provider,
            account_label=account_label or credential.account_label,
            access_token=credential.access_token,
            refresh_token=(
                credential.refresh_token.get_secret_value()
                if credential.refresh_token is not None
                else None
            ),
            expires_at=credential.expires_at,
            project_id=project_id if project_id is not None else credential.project_id,
            account_id=account_id if account_id is not None else credential.account_id,
            metadata=dict(metadata if metadata is not None else credential.metadata),
        )

    def _request_json(
        self,
        credential: SubscriptionCredential,
        endpoint: str,
        *,
        method: str,
        body: Mapping[str, Any] | None = None,
        allowed_hosts: set[str] | frozenset[str],
        response_reason: str,
    ) -> dict[str, Any]:
        try:
            validate_provider_url(endpoint, allowed_hosts=set(allowed_hosts))
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Google setup endpoint is invalid",
                reason="invalid_setup_endpoint",
                credential=credential,
            ) from None
        data = (
            json.dumps(
                _as_plain_json(body), ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
            if body is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {credential.access_token.get_secret_value()}",
            "User-Agent": _ANTIGRAVITY_USER_AGENT,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(endpoint, data=data, headers=headers, method=method)
        try:
            response = self._open_request(request)
            target = getattr(response, "geturl", None)
            if callable(target):
                target = target()
            if target is None:
                target = getattr(response, "url", None)
            if target is not None:
                validate_provider_url(str(target), allowed_hosts=set(allowed_hosts))
            status, payload = self._read_response(response)
        except HTTPError as exc:
            status, payload = int(exc.code), b""
        except SubscriptionError:
            raise
        except (OSError, URLError, TimeoutError):
            raise _error(
                SubscriptionTransportError,
                "Google setup request could not be delivered",
                reason="setup_transport_error",
                credential=credential,
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Google setup response could not be read",
                reason="setup_response_error",
                credential=credential,
            ) from None
        if status < 200 or status >= 300:
            raise self._map_http_error(status, credential=credential)
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Google setup response is too large",
                status=status,
                reason="response_too_large",
                credential=credential,
            )
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                SubscriptionTransportError,
                "Google setup response is not valid JSON",
                status=status,
                reason=response_reason,
                credential=credential,
            ) from None
        if not isinstance(decoded, Mapping):
            raise _error(
                SubscriptionTransportError,
                "Google setup response is not a JSON object",
                status=status,
                reason=response_reason,
                credential=credential,
            )
        return dict(decoded)

    def _fetch_userinfo(self, credential: SubscriptionCredential) -> Mapping[str, Any]:
        payload = self._request_json(
            credential,
            _USERINFO_ENDPOINT,
            method="GET",
            allowed_hosts=_OAUTH_HOSTS,
            response_reason="malformed_account_response",
        )
        account_id = None
        for key in (
            "sub",
            "account_id",
            "accountId",
            "id",
            "user_id",
            "userId",
            "email",
        ):
            account_id = _safe_string(payload.get(key))
            if account_id is not None:
                break
        if account_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account metadata is required",
                reason="missing_account_metadata",
                credential=credential,
            )
        return payload

    def _merge_userinfo(
        self,
        credential: SubscriptionCredential,
        payload: Mapping[str, Any],
    ) -> SubscriptionCredential:
        account_id = None
        for key in (
            "sub",
            "account_id",
            "accountId",
            "id",
            "user_id",
            "userId",
            "email",
        ):
            account_id = _safe_string(payload.get(key))
            if account_id is not None:
                break
        account_id = account_id or credential.account_id or self._account_id
        email = _safe_string(payload.get("email"))
        name = _safe_string(payload.get("name"))
        account_label = credential.account_label
        if email is not None and (
            account_label == "Google account" or self._account_label is None
        ):
            account_label = email
        elif name is not None and account_label == "Google account":
            account_label = name
        metadata = dict(credential.metadata)
        if email is not None:
            metadata["email"] = email
        if name is not None:
            metadata["name"] = name
        secret_values = [credential.access_token.get_secret_value()]
        if credential.refresh_token is not None:
            secret_values.append(credential.refresh_token.get_secret_value())
        if account_id in secret_values or account_label in secret_values:
            raise _error(
                SubscriptionAuthError,
                "Google account metadata is invalid",
                reason="invalid_account_metadata",
                credential=credential,
            )
        return self._credential_with_metadata(
            credential,
            account_id=account_id,
            account_label=account_label,
            metadata=metadata,
        )

    @staticmethod
    def _project_from_payload(payload: Mapping[str, Any]) -> str | None:
        for key in (
            "cloudaicompanionProject",
            "cloudaicompanion_project",
            "project_id",
            "projectId",
            "project",
        ):
            value = payload.get(key)
            if isinstance(value, Mapping):
                value = value.get("id") or value.get("projectId") or value.get("name")
            project = _safe_project_id(value)
            if project is None:
                continue
            return project
        nested = payload.get("response")
        if isinstance(nested, Mapping):
            return GoogleAntigravityBackend._project_from_payload(nested)
        return None

    @staticmethod
    def _tier_id(payload: Mapping[str, Any]) -> str | None:
        tier = payload.get("currentTier") or payload.get("current_tier")
        if isinstance(tier, Mapping):
            return _safe_string(tier.get("id") or tier.get("tierId"))
        if isinstance(tier, str):
            return _safe_string(tier)
        direct = _safe_string(payload.get("tierId") or payload.get("tier_id"))
        if direct is not None:
            return direct
        allowed = payload.get("allowedTiers")
        if isinstance(allowed, Sequence) and not isinstance(
            allowed, (str, bytes, bytearray)
        ):
            default_tier: str | None = None
            for candidate in allowed:
                if not isinstance(candidate, Mapping):
                    continue
                candidate_id = _safe_string(
                    candidate.get("id") or candidate.get("tierId")
                )
                if candidate_id is not None and candidate.get("isDefault") is True:
                    default_tier = candidate_id
                    break
            return default_tier or "LEGACY"
        return None

    @staticmethod
    def _operation_name(payload: Mapping[str, Any]) -> str | None:
        raw_name = payload.get("name")
        if isinstance(raw_name, Mapping):
            raw_name = raw_name.get("name")
        if raw_name is None and isinstance(payload.get("operation"), Mapping):
            raw_name = payload["operation"].get("name")
        name = _safe_string(raw_name, maximum=256)
        if name is None or name.startswith("http") or "?" in name or "#" in name:
            return None
        parts = name.lstrip("/").split("/")
        if len(parts) != 2 or parts[0] != "operations":
            return None
        if (
            _safe_string(parts[1], maximum=128) is None
            or parts[1] in {".", ".."}
            or any(character.isspace() for character in parts[1])
        ):
            return None
        return "/".join(parts)

    def _operation_endpoint(
        self, name: str, *, credential: SubscriptionCredential
    ) -> str:
        if not name or name.startswith("http") or "?" in name or "#" in name:
            raise _error(
                SubscriptionTransportError,
                "Google project provisioning operation is invalid",
                reason="malformed_provisioning_response",
                credential=credential,
            )
        endpoint = f"{_CLOUD_CODE_BASE_ENDPOINT}/{name.lstrip('/')}"
        try:
            validate_provider_url(endpoint, allowed_hosts=set(_CLOUD_CODE_HOSTS))
        except SubscriptionError:
            raise _error(
                SubscriptionTransportError,
                "Google project provisioning operation is invalid",
                reason="malformed_provisioning_response",
                credential=credential,
            ) from None
        return endpoint

    def _poll_project_operation(
        self,
        credential: SubscriptionCredential,
        name: str,
    ) -> str:
        endpoint = self._operation_endpoint(name, credential=credential)
        for attempt in range(self._operation_poll_attempts):
            if attempt:
                try:
                    self._sleep(self._operation_poll_delay)
                except Exception:
                    raise _error(
                        SubscriptionTransportError,
                        "Google project provisioning wait failed",
                        reason="project_provisioning_wait_failed",
                        credential=credential,
                    ) from None
            payload = self._request_json(
                credential,
                endpoint,
                method="GET",
                allowed_hosts=_CLOUD_CODE_HOSTS,
                response_reason="malformed_provisioning_response",
            )
            project = self._project_from_payload(payload)
            if project is not None:
                return project
            if payload.get("done") is True:
                raise _error(
                    SubscriptionTransportError,
                    "Google project provisioning did not return a project",
                    reason="missing_project_metadata",
                    credential=credential,
                )
        raise _error(
            SubscriptionTransportError,
            "Google project provisioning did not complete",
            reason="project_provisioning_timeout",
            credential=credential,
        )

    def _provision_project(
        self, credential: SubscriptionCredential
    ) -> SubscriptionCredential:
        metadata = dict(_CLIENT_METADATA)
        configured_project = self._project_id
        if configured_project is not None:
            metadata["duetProject"] = configured_project
        load_body: dict[str, Any] = {"metadata": metadata}
        if configured_project is not None:
            load_body["cloudaicompanionProject"] = configured_project
        load_payload = self._request_json(
            credential,
            _LOAD_CODE_ASSIST_ENDPOINT,
            method="POST",
            body=load_body,
            allowed_hosts=_CLOUD_CODE_HOSTS,
            response_reason="malformed_provisioning_response",
        )
        project = self._project_from_payload(load_payload)
        if project is None:
            current_tier = load_payload.get("currentTier")
            if current_tier is None:
                current_tier = load_payload.get("current_tier")
            if current_tier is not None:
                if configured_project is not None:
                    return self._credential_with_metadata(
                        credential,
                        project_id=configured_project,
                    )
                raise _error(
                    SubscriptionAuthError,
                    "Google Cloud project configuration is required",
                    reason="project_configuration_required",
                    credential=credential,
                )
            tier_id = self._tier_id(load_payload)
            if tier_id is None:
                raise _error(
                    SubscriptionTransportError,
                    "Google project provisioning response is incomplete",
                    reason="missing_provisioning_tier",
                    credential=credential,
                )
            allowed_tiers = load_payload.get("allowedTiers")
            selected_tier = None
            if isinstance(allowed_tiers, Sequence) and not isinstance(
                allowed_tiers, (str, bytes, bytearray)
            ):
                selected_tier = next(
                    (
                        candidate
                        for candidate in allowed_tiers
                        if isinstance(candidate, Mapping)
                        and _safe_string(candidate.get("id") or candidate.get("tierId"))
                        == tier_id
                    ),
                    None,
                )
            requires_project = (
                isinstance(selected_tier, Mapping)
                and selected_tier.get("userDefinedCloudaicompanionProject") is True
            )
            if requires_project and configured_project is None:
                ineligible_tiers = load_payload.get("ineligibleTiers")
                individual_client_unsupported = (
                    isinstance(ineligible_tiers, Sequence)
                    and not isinstance(ineligible_tiers, (str, bytes, bytearray))
                    and any(
                        isinstance(candidate, Mapping)
                        and _safe_string(candidate.get("tierId")) == "free-tier"
                        and _safe_string(candidate.get("reasonCode"))
                        == "UNSUPPORTED_CLIENT"
                        for candidate in ineligible_tiers
                    )
                )
                if individual_client_unsupported:
                    raise _error(
                        SubscriptionPolicyError,
                        "Google no longer supports this OAuth client for individual "
                        "Gemini Code Assist access; configure GOOGLE_CLOUD_PROJECT "
                        "or use Google API credentials",
                        reason="individual_client_unsupported",
                        credential=credential,
                    )
                raise _error(
                    SubscriptionAuthError,
                    "Google Cloud project configuration is required",
                    reason="project_configuration_required",
                    credential=credential,
                )
            free_tier = tier_id.strip().casefold() in {"free", "free-tier"}
            onboard_body: dict[str, Any] = {
                "tierId": tier_id,
                "metadata": dict(_CLIENT_METADATA) if free_tier else metadata,
            }
            if configured_project is not None and not free_tier:
                onboard_body["cloudaicompanionProject"] = configured_project
            onboard_payload = self._request_json(
                credential,
                _ONBOARD_USER_ENDPOINT,
                method="POST",
                body=onboard_body,
                allowed_hosts=_CLOUD_CODE_HOSTS,
                response_reason="malformed_provisioning_response",
            )
            project = self._project_from_payload(onboard_payload)
            if project is None:
                operation = self._operation_name(onboard_payload)
                if operation is not None:
                    project = self._poll_project_operation(credential, operation)
                elif configured_project is not None and not free_tier:
                    project = configured_project
                else:
                    raise _error(
                        SubscriptionTransportError,
                        "Google project provisioning response is incomplete",
                        reason="malformed_provisioning_response",
                        credential=credential,
                    )
        return self._credential_with_metadata(
            credential,
            project_id=project,
        )

    def _ensure_account_and_project(
        self,
        credential: SubscriptionCredential,
    ) -> SubscriptionCredential:
        result = credential
        if result.project_id is not None:
            normalized_project = _safe_project_id(result.project_id)
            if normalized_project is None:
                raise _error(
                    SubscriptionAuthError,
                    "Google account project metadata is invalid",
                    reason="invalid_project_metadata",
                    credential=result,
                )
            if normalized_project != result.project_id:
                result = self._credential_with_metadata(
                    result,
                    project_id=normalized_project,
                )
        if result.account_id is None:
            try:
                result = self._merge_userinfo(result, self._fetch_userinfo(result))
            except SubscriptionError:
                # User-info lookup is non-fatal. Project setup and authenticated
                # requests remain authoritative.
                pass
        if result.project_id is None:
            result = self._provision_project(result)
        if result.project_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account project metadata is required",
                reason="missing_project_metadata",
                credential=result,
            )
        return result

    def _load_optional(self) -> SubscriptionCredential | None:
        loader = getattr(self._store, "load", None)
        if not callable(loader):
            loader = getattr(self._store, "get", None)
        if not callable(loader):
            raise _error(
                SubscriptionAuthError,
                "Google subscription store is unavailable",
                reason="store_unavailable",
            )
        try:
            credential = loader(self.provider)
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Google subscription credential could not be loaded",
                reason="store_unavailable",
            ) from None
        if credential is None:
            return None
        if isinstance(credential, SubscriptionCredential):
            if credential.provider is not self.provider:
                raise _error(
                    SubscriptionAuthError,
                    "Google subscription credential is invalid",
                    reason="corrupt_record",
                )
            return credential
        try:
            return SubscriptionCredential.model_validate(credential)
        except Exception:
            raise _error(
                SubscriptionAuthError,
                "Google subscription credential is invalid",
                reason="corrupt_record",
            ) from None

    def _load_required(self) -> SubscriptionCredential:
        credential = self._load_optional()
        if credential is None:
            raise _error(
                SubscriptionTokenExpired,
                "Google subscription authentication is required",
                reason="missing_credential",
            )
        return credential

    def _load_ready(self) -> SubscriptionCredential:
        """Load a credential and lazily finish Cloud Code project setup."""

        with self._provider_operation():
            credential = self._load_required()
            if credential.project_id is None:
                credential = self._ensure_account_and_project(credential)
                self._save(credential)
            return credential

    def _save(self, credential: SubscriptionCredential) -> None:
        saver = getattr(self._store, "save", None)
        if not callable(saver):
            raise _error(
                SubscriptionAuthError,
                "Google subscription store is unavailable",
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
                "Google subscription credential could not be saved",
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
                "Google subscription authentication has expired",
                status=401,
                reason="missing_refresh_token",
                credential=current,
            )
        if _safe_string(self._client_id) is None:
            raise _error(
                SubscriptionTokenExpired,
                "Google OAuth is not configured; set "
                "MUDIDI_GOOGLE_OAUTH_CLIENT_ID and restart MUDIDI",
                status=401,
                reason="oauth_client_configuration_missing",
                credential=current,
            )
        exchanger = getattr(self._oauth, "refresh", None)
        if not callable(exchanger):
            exchanger = getattr(self._oauth, "refresh_token", None)
        if not callable(exchanger):
            raise _error(
                SubscriptionTokenExpired,
                "Google subscription refresh is unavailable",
                status=401,
                reason="refresh_unavailable",
                credential=current,
            )
        kwargs: dict[str, Any] = {
            "refresh_token": refresh_token.get_secret_value(),
            "client_id": self._client_id,
        }
        if self._client_secret is not None:
            kwargs["client_secret"] = self._client_secret
        try:
            try:
                payload = exchanger(_TOKEN_ENDPOINT, provider=self.provider, **kwargs)
            except TypeError:
                payload = exchanger(_TOKEN_ENDPOINT, **kwargs)
        except SubscriptionError as exc:
            raise _error(
                SubscriptionTokenExpired,
                "Google subscription refresh failed",
                status=exc.status,
                reason="refresh_failed",
                credential=current,
            ) from None
        except Exception:
            raise _error(
                SubscriptionTokenExpired,
                "Google subscription refresh failed",
                status=401,
                reason="refresh_failed",
                credential=current,
            ) from None
        replacement = self._credential_from_token_response(
            payload,
            previous=current,
            require_project=False,
            require_account=False,
        )
        self._save(replacement)
        return replacement

    def logout(self) -> None:
        with self._provider_operation():
            deleter = getattr(self._store, "delete", None)
            if not callable(deleter):
                deleter = getattr(self._store, "remove", None)
            if not callable(deleter):
                raise _error(
                    SubscriptionAuthError,
                    "Google subscription store is unavailable",
                    reason="store_unavailable",
                )
            try:
                deleter(self.provider)
            except SubscriptionError:
                raise
            except Exception:
                raise _error(
                    SubscriptionAuthError,
                    "Google subscription credential could not be removed",
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
        account_label = credential.account_label
        secret_values = [credential.access_token.get_secret_value()]
        if credential.refresh_token is not None:
            secret_values.append(credential.refresh_token.get_secret_value())
        if any(secret and secret in account_label for secret in secret_values):
            account_label = "Google account"
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=authenticated,
            account_label=account_label,
            expires_at=expires_at,
            metadata=credential.redacted_metadata,
        )

    def catalog_identity(self) -> str | None:
        """Return a stable non-secret identity for model-catalog caching."""

        credential = self._load_optional()
        if credential is None:
            return None
        return (
            credential.account_id or credential.project_id or credential.account_label
        )

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        """Discover this authenticated account's structured Gemini catalog."""

        credential = self._load_ready()
        if credential.project_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account project metadata is required",
                reason="missing_project_metadata",
                credential=credential,
            )
        payload = self._request_json(
            credential,
            _FETCH_AVAILABLE_MODELS_ENDPOINT,
            method="POST",
            body={"project": credential.project_id},
            allowed_hosts=_CLOUD_CODE_HOSTS,
            response_reason="model_discovery_failed",
        )
        raw_models = payload.get("models")
        if not isinstance(raw_models, Mapping):
            raise _error(
                SubscriptionTransportError,
                "Google model catalog response is malformed",
                reason="model_discovery_failed",
                credential=credential,
            )
        grouped: dict[str, dict[str, Any]] = {}
        available_model_ids = frozenset(
            value
            for value in raw_models
            if isinstance(value, str) and value.startswith("gemini-")
        )
        for value, metadata in raw_models.items():
            if not isinstance(value, str) or not value.startswith("gemini-"):
                continue
            model_id = _canonical_model_id(value, metadata, available_model_ids)
            raw_priority = (
                metadata.get("priority") if isinstance(metadata, Mapping) else None
            )
            provider_order = (
                raw_priority
                if isinstance(raw_priority, int)
                and not isinstance(raw_priority, bool)
                and raw_priority >= 0
                else None
            )
            group = grouped.setdefault(
                model_id,
                {
                    "display_name": (
                        metadata.get("displayName")
                        if isinstance(metadata, Mapping)
                        and isinstance(metadata.get("displayName"), str)
                        else f"Gemini {model_id.removeprefix('gemini-').replace('-', ' ').title()}"
                    ),
                    "provider_order": provider_order,
                    "wire_model_map": {},
                },
            )
            current_order = group["provider_order"]
            if provider_order is not None and (
                current_order is None or provider_order < current_order
            ):
                group["provider_order"] = provider_order
            suffix_effort: ReasoningEffort | None = None
            for suffix, effort in (
                ("-extra-low", "minimal"),
                ("-low", "low"),
                ("-medium", "medium"),
                ("-high", "high"),
            ):
                if value.endswith(suffix):
                    suffix_effort = effort  # type: ignore[assignment]
                    break
            if suffix_effort is not None:
                group["wire_model_map"][suffix_effort] = value
                if suffix_effort == "low" and "-flash" in model_id:
                    group["wire_model_map"].setdefault("minimal", value)
        if not grouped:
            raise _error(
                SubscriptionTransportError,
                "Google model catalog contains no Gemini models",
                reason="model_discovery_failed",
                credential=credential,
            )
        models = tuple(
            SubscriptionModel(
                model_id=model_id,
                display_name=(
                    _gemini_display_name(model_id)
                    if group["wire_model_map"]
                    else group["display_name"]
                ),
                reasoning=resolve_reasoning_profile(
                    "google-antigravity",
                    model_id,
                    advertised_efforts=tuple(group["wire_model_map"]),
                    wire_model_map=tuple(group["wire_model_map"].items()),
                ),
                provider_order=group["provider_order"],
            )
            for model_id, group in grouped.items()
        )
        display_counts = Counter(model.display_name.casefold() for model in models)
        return tuple(
            replace(
                model,
                display_name=f"{model.display_name} — {model.model_id}",
            )
            if display_counts[model.display_name.casefold()] > 1
            else model
            for model in models
        )

    def _message_parts(self, content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            if not content:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message content is empty",
                    reason="unsupported_message_content",
                )
            return [{"text": content}]
        if isinstance(content, Mapping):
            content = [content]
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Google message content is unsupported",
                reason="unsupported_message_content",
            )
        values: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                if not part:
                    raise _error(
                        SubscriptionUnsupportedRequest,
                        "Google message content is empty",
                        reason="unsupported_message_content",
                    )
                values.append({"text": part})
                continue
            if not isinstance(part, Mapping):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message content is unsupported",
                    reason="unsupported_message_content",
                )
            kind = part.get("type")
            if kind in _IMAGE_TYPES or "image_url" in part or "imageUrl" in part:
                values.append(_image_part(part))
                continue
            if kind not in _TEXT_TYPES:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message content is unsupported",
                    reason="unsupported_message_content",
                )
            text = part.get("text")
            if not isinstance(text, str) or not text:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message content is unsupported",
                    reason="unsupported_message_content",
                )
            values.append({"text": text})
        if not values:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Google message content is empty",
                reason="unsupported_message_content",
            )
        return values

    def _reasoning_config(self, model: str, reasoning: str) -> dict[str, Any]:
        if not isinstance(model, str) or not isinstance(reasoning, str):
            raise _error(
                SubscriptionUnsupportedRequest,
                "Google reasoning level is unsupported for this model",
                reason="unsupported_reasoning",
            )
        model_id = _logical_model_id(model.rsplit("/", 1)[-1].strip().lower())
        profile = resolve_reasoning_profile("google-antigravity", model_id)
        level = choose_supported_effort(profile, reasoning)
        if level is None:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Google reasoning level is unsupported for this model",
                reason="unsupported_reasoning",
            )
        if model_id.startswith("gemini-2.5"):
            budget = _GEMINI_25_REASONING_BUDGETS.get(level)
            if budget is not None:
                return {"thinkingBudget": budget}
        elif model_id.startswith("gemini-3"):
            return {"thinkingLevel": level.upper()}
        raise _error(
            SubscriptionUnsupportedRequest,
            "Google reasoning level is unsupported for this model",
            reason="unsupported_reasoning",
        )

    def _build_request_body(
        self,
        request: CompletionRequest,
        *,
        structured: bool,
        credential: SubscriptionCredential,
    ) -> dict[str, Any]:
        native_model = resolve_subscription_model(self.provider, request.model)
        wire_model = _wire_model_id(native_model, request.reasoning)
        if wire_model != request.model:
            request = request.model_copy(update={"model": wire_model})
        contents: list[dict[str, Any]] = []
        system_parts: list[dict[str, Any]] = []
        for message in request.messages:
            if not isinstance(message, Mapping):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message shape is unsupported",
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
                    "name",
                )
            ):
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google tool messages are unsupported by this backend",
                    reason="tool_input_unsupported",
                )
            role = message.get("role")
            if role in {"system", "developer"}:
                system_parts.extend(self._message_parts(message.get("content")))
                continue
            if role == "assistant":
                selected_role = "model"
            elif role == "user":
                selected_role = "user"
            else:
                raise _error(
                    SubscriptionUnsupportedRequest,
                    "Google message role is unsupported",
                    reason="unsupported_message",
                )
            contents.append(
                {
                    "role": selected_role,
                    "parts": self._message_parts(message.get("content")),
                }
            )
        if not contents:
            raise _error(
                SubscriptionUnsupportedRequest,
                "Google completion requires at least one user or assistant message",
                reason="unsupported_message",
            )
        if credential.project_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account project metadata is required",
                reason="missing_project_metadata",
                credential=credential,
            )
        project_id = _safe_project_id(credential.project_id)
        if project_id is None:
            raise _error(
                SubscriptionAuthError,
                "Google account project metadata is invalid",
                reason="invalid_project_metadata",
                credential=credential,
            )

        generation: dict[str, Any] = {}
        if request.max_tokens is not None:
            generation["maxOutputTokens"] = request.max_tokens
        if request.reasoning is not None:
            generation["thinkingConfig"] = self._reasoning_config(
                request.model,
                request.reasoning,
            )
        if structured:
            generation["responseMimeType"] = "application/json"
            generation["responseJsonSchema"] = _as_plain_json(request.schema)

        request_id = self._prompt_id()
        provider_request: dict[str, Any] = {
            "contents": contents,
            "sessionId": request_id,
            "labels": {
                "last_step_index": "0",
                "trajectory_id": request_id,
                "used_claude": "false",
                "used_claude_conservative": "false",
            },
        }
        if system_parts:
            provider_request["systemInstruction"] = {
                "role": "user",
                "parts": system_parts,
            }
        if generation:
            provider_request["generationConfig"] = generation
        return {
            "project": project_id,
            "model": request.model,
            "requestId": request_id,
            "userAgent": "antigravity",
            "requestType": "agent",
            "request": provider_request,
        }

    def _prompt_id(self) -> str:
        try:
            value = self._prompt_id_factory()
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Google request identifier could not be created",
                reason="request_id_unavailable",
            ) from None
        if _safe_string(value, maximum=128) is None:
            raise _error(
                SubscriptionTransportError,
                "Google request identifier is invalid",
                reason="request_id_unavailable",
            )
        return value

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
        if status is None:
            status = getattr(response, "status_code", None)
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
            raise TypeError("Google response body is not readable")
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
            raise TypeError("Google response body is not bytes")
        return int(status), bytes(raw)

    def _validate_response_target(
        self, response: Any, *, credential: SubscriptionCredential
    ) -> None:
        target = getattr(response, "geturl", None)
        if callable(target):
            target = target()
        if target is None:
            target = getattr(response, "url", None)
        if target is None:
            return
        try:
            validate_provider_url(target, allowed_hosts=set(_CLOUD_CODE_HOSTS))
        except (SubscriptionError, TypeError):
            raise _error(
                SubscriptionTransportError,
                "Google response endpoint redirect is not allowed",
                reason="endpoint_redirect",
                credential=credential,
            ) from None

    def _post_json(
        self,
        credential: SubscriptionCredential,
        body: Mapping[str, Any],
    ) -> tuple[int, bytes]:
        validate_provider_url(self._endpoint, allowed_hosts=set(_CLOUD_CODE_HOSTS))
        data = json.dumps(
            _as_plain_json(body), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        headers = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {credential.access_token.get_secret_value()}",
            "User-Agent": _ANTIGRAVITY_USER_AGENT,
        }
        request = Request(self._endpoint, data=data, headers=headers, method="POST")
        try:
            response = self._open_request(request)
            self._validate_response_target(response, credential=credential)
            status, payload = self._read_response(response)
        except HTTPError as exc:
            return int(exc.code), b""
        except SubscriptionError:
            raise
        except (OSError, URLError, TimeoutError):
            raise _error(
                SubscriptionTransportError,
                "Google request could not be delivered",
                reason="transport_error",
                credential=credential,
            ) from None
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "Google response could not be read",
                reason="response_error",
                credential=credential,
            ) from None
        if len(payload) > self._max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "Google response is too large",
                status=status,
                reason="response_too_large",
                credential=credential,
            )
        return status, payload

    def _map_http_error(
        self, status: int, *, credential: SubscriptionCredential
    ) -> SubscriptionError:
        if status == 401:
            return _error(
                SubscriptionAuthError,
                "Google authentication was rejected",
                status=status,
                reason="authentication_rejected",
                credential=credential,
            )
        if status == 403:
            return _error(
                SubscriptionPolicyError,
                "Google subscription policy rejected the request",
                status=status,
                reason="policy_rejected",
                credential=credential,
            )
        if status == 429:
            return _error(
                SubscriptionTransportError,
                "Google subscription quota is exhausted or rate limited",
                category="quota_exhausted",
                status=status,
                reason="quota_exhausted",
                credential=credential,
            )
        if 500 <= status <= 599:
            return _error(
                SubscriptionTransportError,
                "Google subscription service is unavailable",
                category="provider_unavailable",
                status=status,
                reason="provider_unavailable",
                credential=credential,
            )
        if 400 <= status <= 499:
            return _error(
                SubscriptionTransportError,
                "Google request was rejected",
                status=status,
                reason="provider_client_error",
                credential=credential,
            )
        return _error(
            SubscriptionTransportError,
            "Google request failed",
            status=status,
            reason="provider_error",
            credential=credential,
        )

    def _parse_documents(self, body: bytes) -> list[Mapping[str, Any]]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise _error(
                SubscriptionTransportError,
                "Google response is not valid UTF-8",
                reason="malformed_response",
            ) from None
        if not text.strip():
            raise _error(
                SubscriptionTransportError,
                "Google response is empty",
                reason="malformed_response",
            )
        if any(line.startswith("data:") for line in text.splitlines()):
            return self._parse_sse(text)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            documents: list[Mapping[str, Any]] = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    raise _error(
                        SubscriptionTransportError,
                        "Google response is not valid JSON",
                        reason="malformed_response",
                    ) from None
                if isinstance(candidate, Mapping):
                    documents.append(candidate)
                elif isinstance(candidate, list) and all(
                    isinstance(item, Mapping) for item in candidate
                ):
                    documents.extend(candidate)
                else:
                    raise _error(
                        SubscriptionTransportError,
                        "Google response is not a JSON object",
                        reason="malformed_response",
                    )
            if not documents:
                raise _error(
                    SubscriptionTransportError,
                    "Google response is not valid JSON",
                    reason="malformed_response",
                )
            return documents
        if isinstance(decoded, Mapping):
            return [decoded]
        if isinstance(decoded, list) and all(
            isinstance(item, Mapping) for item in decoded
        ):
            return list(decoded)
        raise _error(
            SubscriptionTransportError,
            "Google response is not a JSON object",
            reason="malformed_response",
        )

    def _parse_sse(self, text: str) -> list[Mapping[str, Any]]:
        documents: list[Mapping[str, Any]] = []
        data_lines: list[str] = []
        saw_sse = False

        def flush() -> None:
            if not data_lines:
                return
            payload = "\n".join(data_lines).strip()
            data_lines.clear()
            if not payload or payload == "[DONE]":
                return
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                raise _error(
                    SubscriptionTransportError,
                    "Google SSE response chunk is not valid JSON",
                    reason="malformed_sse",
                ) from None
            if not isinstance(decoded, Mapping):
                raise _error(
                    SubscriptionTransportError,
                    "Google SSE response chunk is not a JSON object",
                    reason="malformed_sse",
                )
            documents.append(decoded)

        for raw_line in text.splitlines():
            if len(raw_line) > _MAX_SSE_LINE_LENGTH:
                raise _error(
                    SubscriptionTransportError,
                    "Google SSE response line is too large",
                    reason="malformed_sse",
                )
            line = raw_line.rstrip("\r")
            if not line:
                flush()
                continue
            if line.startswith(":"):
                saw_sse = True
                continue
            if line.startswith("data:"):
                saw_sse = True
                value = line[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)
                continue
            if line.startswith(("event:", "id:", "retry:")):
                saw_sse = True
                continue
            # SSE allows extension fields; they do not carry response data.
            if saw_sse:
                continue
        flush()
        if not documents:
            raise _error(
                SubscriptionTransportError,
                "Google SSE response did not contain a response",
                reason="malformed_sse",
            )
        return documents

    def _normalize_response(
        self,
        request: CompletionRequest,
        body: bytes,
        *,
        structured: bool,
    ) -> CompletionResult:
        documents = self._parse_documents(body)
        text_parts: list[str] = []
        usage: dict[str, int | float | None] = {}
        finish_reason: str | None = None
        selected_model = request.model
        structured_candidate: Any | None = None

        def consume_usage(raw_usage: Any) -> None:
            if not isinstance(raw_usage, Mapping):
                return
            input_tokens = _number(raw_usage.get("promptTokenCount"))
            if input_tokens is None:
                input_tokens = _number(raw_usage.get("input_tokens"))
            output_tokens = _number(raw_usage.get("candidatesTokenCount"))
            if output_tokens is None:
                output_tokens = _number(raw_usage.get("output_tokens"))
            total_tokens = _number(raw_usage.get("totalTokenCount"))
            if total_tokens is None:
                total_tokens = _number(raw_usage.get("total_tokens"))
            if input_tokens is not None:
                usage["input_tokens"] = input_tokens
            if output_tokens is not None:
                usage["output_tokens"] = output_tokens
            if total_tokens is not None:
                usage["total_tokens"] = total_tokens
            cached = _number(raw_usage.get("cachedContentTokenCount"))
            if cached is None:
                cached = _number(raw_usage.get("cached_tokens"))
            if cached is not None:
                usage["cached_tokens"] = cached
            reasoning = _number(raw_usage.get("thoughtsTokenCount"))
            if reasoning is None:
                reasoning = _number(raw_usage.get("reasoning_tokens"))
            if reasoning is not None:
                usage["reasoning_tokens"] = reasoning
            if (
                "total_tokens" not in usage
                and "input_tokens" in usage
                and "output_tokens" in usage
            ):
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]  # type: ignore[operator]

        def consume_response(
            response: Mapping[str, Any], document: Mapping[str, Any]
        ) -> None:
            nonlocal finish_reason, selected_model, structured_candidate
            model = response.get("modelVersion") or response.get("model")
            if isinstance(model, str) and model:
                selected_model = model
            consume_usage(response.get("usageMetadata"))
            consume_usage(response.get("usage"))
            candidates = response.get("candidates")
            if isinstance(candidates, Mapping):
                candidates = [candidates]
            if isinstance(candidates, Sequence) and not isinstance(
                candidates, (str, bytes, bytearray)
            ):
                for candidate in candidates:
                    if not isinstance(candidate, Mapping):
                        continue
                    raw_finish = candidate.get("finishReason") or candidate.get(
                        "finish_reason"
                    )
                    if isinstance(raw_finish, str) and raw_finish:
                        finish_reason = self._normalize_finish_reason(raw_finish)
                    content = candidate.get("content")
                    if isinstance(content, Mapping):
                        parts = content.get("parts")
                        if isinstance(parts, Mapping):
                            parts = [parts]
                        if isinstance(parts, Sequence) and not isinstance(
                            parts, (str, bytes, bytearray)
                        ):
                            for part in parts:
                                if not isinstance(part, Mapping):
                                    continue
                                if (
                                    part.get("thought") is True
                                    or part.get("isThought") is True
                                ):
                                    continue
                                part_text = part.get("text")
                                if isinstance(part_text, str):
                                    text_parts.append(part_text)
                                for key in (
                                    "structured_json",
                                    "structuredJson",
                                    "parsed",
                                ):
                                    if key in part and structured_candidate is None:
                                        structured_candidate = part[key]
                    direct_text = candidate.get("text")
                    if isinstance(direct_text, str):
                        text_parts.append(direct_text)
            for key in ("structured_json", "structuredJson", "parsed", "output_parsed"):
                if key in response and structured_candidate is None:
                    structured_candidate = response[key]
            consume_usage(document.get("usageMetadata"))
            consume_usage(document.get("usage"))
            raw_finish = document.get("finishReason") or document.get("finish_reason")
            if isinstance(raw_finish, str) and raw_finish:
                finish_reason = self._normalize_finish_reason(raw_finish)

        for document in documents:
            response = document.get("response")
            if not isinstance(response, Mapping):
                raise _error(
                    SubscriptionTransportError,
                    "Google response envelope is malformed",
                    reason="malformed_response",
                )
            consume_response(response, document)

        text = "".join(text_parts)
        if not text.strip():
            raise _error(
                SubscriptionTransportError,
                "Google response did not contain visible text",
                reason="missing_visible_text",
            )
        if structured:
            candidate = structured_candidate
            if isinstance(candidate, str):
                try:
                    candidate = _parse_structured_text(candidate)
                except (TypeError, json.JSONDecodeError):
                    candidate = None
            if candidate is None:
                try:
                    candidate = _parse_structured_text(text)
                except (TypeError, json.JSONDecodeError):
                    raise _error(
                        SubscriptionTransportError,
                        "Google structured output is invalid JSON",
                        reason="invalid_structured_output",
                    ) from None
            if not isinstance(request.schema, Mapping) or not _schema_matches(
                candidate, request.schema
            ):
                raise _error(
                    SubscriptionTransportError,
                    "Google structured output does not match the requested schema",
                    reason="structured_schema_mismatch",
                )
            structured_value = candidate
        else:
            structured_value = None
        return CompletionResult(
            text=text,
            structured_json=structured_value,
            usage=usage,
            finish_reason=finish_reason,
            provider=self.provider,
            model=selected_model,
        )

    @staticmethod
    def _normalize_finish_reason(value: str) -> str:
        normalized = value.strip()
        mapped = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "BLOCKLIST": "content_filter",
            "PROHIBITED_CONTENT": "content_filter",
            "RECITATION": "content_filter",
        }
        return mapped.get(normalized.upper(), normalized.lower())

    def _complete(
        self, request: CompletionRequest, *, structured: bool
    ) -> CompletionResult:
        native_model = resolve_subscription_model(self.provider, request.model)
        if native_model != request.model:
            request = request.model_copy(update={"model": native_model})
        credential = self._load_ready()
        body = self._build_request_body(
            request, structured=structured, credential=credential
        )
        refresh_attempted = False
        transient_retries = 0
        while True:
            status, payload = self._post_json(credential, body)
            if status == 401:
                if refresh_attempted:
                    raise _error(
                        SubscriptionTokenExpired,
                        "Google subscription authentication has expired",
                        status=401,
                        reason="refresh_rejected",
                        credential=credential,
                    )
                refresh_attempted = True
                credential = self.refresh()
                continue
            if (
                status in _TRANSIENT_STATUSES
                and transient_retries < _MAX_TRANSIENT_RETRIES
            ):
                transient_retries += 1
                continue
            if status < 200 or status >= 300:
                raise self._map_http_error(status, credential=credential)
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
                "Google structured completion requires a JSON schema",
                reason="missing_schema",
            )
        _require_supported_schema(normalized.schema)
        return self._complete(normalized, structured=True)


__all__ = ["GoogleAntigravityBackend"]
