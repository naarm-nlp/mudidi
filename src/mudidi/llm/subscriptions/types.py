"""Provider-neutral contracts for local subscription-backed LLM calls.

The models in this module are intentionally independent of any provider SDK or
credential store.  Subscription adapters own transport and token lifecycle;
callers communicate with them through the small request/result protocol below.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from mudidi.llm.reasoning import ReasoningProfile


class SubscriptionProvider(StrEnum):
    """Provider identifiers supported by the subscription routing boundary."""

    OPENAI = "openai"
    GOOGLE = "google"
    CLAUDE = "claude"


class AuthMode(StrEnum):
    """How an LLM request is billed and authenticated."""

    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"


_SUBSCRIPTION_MODEL_PREFIX: dict[SubscriptionProvider, str] = {
    SubscriptionProvider.OPENAI: "openai",
    SubscriptionProvider.GOOGLE: "gemini",
    SubscriptionProvider.CLAUDE: "anthropic",
}
_SUBSCRIPTION_DEFAULT_MODEL: dict[SubscriptionProvider, str] = {
    SubscriptionProvider.OPENAI: "gpt-5.6-terra",
    SubscriptionProvider.GOOGLE: "gemini-3.1-pro-low",
    SubscriptionProvider.CLAUDE: "claude-sonnet-4-6",
}


def _subscription_provider(
    provider: SubscriptionProvider | str,
) -> SubscriptionProvider:
    try:
        return (
            provider
            if isinstance(provider, SubscriptionProvider)
            else SubscriptionProvider(provider)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "subscription provider must be one of: openai, google, claude"
        ) from exc


def subscription_model_prefix(provider: SubscriptionProvider | str) -> str:
    """Return the LiteLLM prefix mapped to one subscription provider."""

    return _SUBSCRIPTION_MODEL_PREFIX[_subscription_provider(provider)]


def subscription_provider_for_model(model: str) -> SubscriptionProvider:
    """Return the subscription provider selected by a qualified model ID."""

    if not isinstance(model, str) or not model.strip() or "/" not in model:
        raise ValueError("subscription model must include a supported provider prefix")
    prefix = model.strip().split("/", maxsplit=1)[0]
    for provider, candidate in _SUBSCRIPTION_MODEL_PREFIX.items():
        if prefix == candidate:
            return provider
    raise ValueError(
        "subscription model provider must be one of: openai, gemini, anthropic"
    )


def subscription_default_model(provider: SubscriptionProvider | str) -> str:
    """Return the provider-native default model for one subscription."""

    return _SUBSCRIPTION_DEFAULT_MODEL[_subscription_provider(provider)]


def resolve_subscription_model(
    provider: SubscriptionProvider | str,
    model: str | None = None,
) -> str:
    """Validate one model for ``provider`` and return its native identifier.

    Configurations use LiteLLM-style qualified identifiers while direct
    subscription adapters use provider-native identifiers.  A slash therefore
    marks a qualified model and its prefix must exactly match the selected
    subscription provider.  Bare identifiers are interpreted as native model
    names and are returned unchanged.
    """

    selected_provider = _subscription_provider(provider)
    value = subscription_default_model(selected_provider) if model is None else model
    if not isinstance(value, str) or not value.strip():
        raise ValueError("subscription model must be a non-empty string")
    selected = value.strip()
    if "/" not in selected:
        return selected
    prefix, native = selected.split("/", maxsplit=1)
    expected = subscription_model_prefix(selected_provider)
    if prefix != expected:
        raise ValueError(
            f"model {selected!r} is incompatible with the "
            f"{selected_provider.value} subscription; expected prefix {expected!r}"
        )
    native = native.strip()
    if not native:
        raise ValueError("subscription model must include a native model name")
    return native


def qualify_subscription_model(
    provider: SubscriptionProvider | str,
    model: str | None = None,
) -> str:
    """Return one validated model in the selected provider's qualified form."""

    selected_provider = _subscription_provider(provider)
    return (
        f"{subscription_model_prefix(selected_provider)}/"
        f"{resolve_subscription_model(selected_provider, model)}"
    )


def validate_subscription_models(
    provider: SubscriptionProvider | str,
    *,
    default: str,
    stage1: str | None = None,
    stage2_pass1: str | None = None,
    stage2_pass2: str | None = None,
    evaluator: str | None = None,
    rewriter: str | None = None,
    stage1_active: bool = True,
    stage2_pass1_active: bool = True,
    stage2_pass2_active: bool = True,
    agentic_active: bool = False,
) -> None:
    """Validate active pipeline and agentic models for one subscription."""

    selected_provider = _subscription_provider(provider)
    models: list[tuple[str, str | None]] = [("models.default", default)]
    if stage1_active:
        models.append(("models.stage1", stage1))
    if stage2_pass1_active:
        models.append(("models.stage2_pass1", stage2_pass1))
    if stage2_pass2_active:
        models.append(("models.stage2_pass2", stage2_pass2))
    if agentic_active:
        models.extend(
            [
                ("agentic.evaluator_model", evaluator),
                ("agentic.rewriter_model", rewriter),
            ]
        )
    for label, model_name in models:
        if model_name is None:
            continue
        try:
            resolve_subscription_model(selected_provider, model_name)
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc


class _FrozenDict(dict[str, Any]):
    """A dict-shaped JSON object that rejects all in-place mutations."""

    def __init__(self, value: Mapping[str, Any] = ()) -> None:
        dict.__init__(self, value)

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        raise TypeError("frozen JSON objects cannot be mutated")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __ior__(self, value: Mapping[str, Any]) -> _FrozenDict:
        self._immutable(value)
        return self


class _FrozenList(list[Any]):
    """A list-shaped JSON array that rejects all in-place mutations."""

    def __init__(self, value: Iterable[Any] = ()) -> None:
        list.__init__(self, value)

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        raise TypeError("frozen JSON arrays cannot be mutated")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable


def _freeze_json(value: Any) -> Any:
    """Recursively wrap validated JSON containers in immutable equivalents."""

    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


with warnings.catch_warnings():
    # ``schema`` is part of the provider-neutral public contract.  Pydantic
    # warns because BaseModel retains a legacy class method with that name.
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in "CompletionRequest" shadows an attribute in parent "BaseModel"',
        category=UserWarning,
    )

    class CompletionRequest(BaseModel):
        """Normalized, provider-neutral input to a completion backend."""

        model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

        model: str
        messages: list[dict[str, JsonValue]]
        max_tokens: int | None = Field(
            default=None,
            validation_alias=AliasChoices("max_tokens", "max_output_tokens"),
        )
        reasoning: str | None = Field(
            default=None,
            validation_alias=AliasChoices("reasoning", "reasoning_effort"),
        )
        schema: dict[str, JsonValue] | None = Field(
            default=None,
            validation_alias=AliasChoices("schema", "response_schema", "output_schema"),
        )
        cache_key: str | None = Field(
            default=None,
            validation_alias=AliasChoices("cache_key", "prompt_cache_key"),
        )

        @field_validator("messages", "schema", mode="after")
        @classmethod
        def _freeze_request_json(cls, value: Any) -> Any:
            return _freeze_json(value)


class CompletionResult(BaseModel):
    """Normalized completion output returned by a subscription backend."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        validate_default=True,
    )

    text: str = Field(validation_alias=AliasChoices("text", "visible_text"))
    structured_json: JsonValue | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "structured_json", "structured", "structured_output"
        ),
    )
    usage: dict[str, StrictInt | StrictFloat | None] = Field(default_factory=dict)
    finish_reason: str | None = None
    provider: SubscriptionProvider | None = None
    model: str | None = None
    billing_mode: Literal["subscription"] = "subscription"

    @field_validator("structured_json", "usage", mode="after")
    @classmethod
    def _freeze_result_json(cls, value: Any) -> Any:
        return _freeze_json(value)

    @property
    def visible_text(self) -> str:
        """Alias used by callers that distinguish visible text from reasoning."""

        return self.text

    @property
    def structured(self) -> JsonValue | None:
        """Short alias for the optional structured JSON payload."""

        return self.structured_json


class BackendCapabilities(BaseModel):
    """Provider-neutral feature flags advertised by a subscription backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_input: bool = False
    structured_output: bool = False
    streaming: bool = False
    cancellation: bool = False
    usage: bool = False
    reasoning: bool = False
    model_discovery: bool = False


@dataclass(frozen=True, slots=True)
class SubscriptionModel:
    """Non-secret model metadata returned by authenticated discovery."""

    model_id: str
    display_name: str
    reasoning: ReasoningProfile
    release_at: datetime | None = None
    provider_order: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model_id, str)
            or not self.model_id.strip()
            or self.model_id != self.model_id.strip()
            or "/" in self.model_id
        ):
            raise ValueError(
                "subscription model ID must be a non-empty native identifier"
            )
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("subscription model display name must not be empty")
        if self.release_at is not None:
            if not isinstance(self.release_at, datetime):
                raise ValueError("subscription model release time must be a datetime")
            release_at = self.release_at
            normalized_release = (
                release_at.replace(tzinfo=UTC)
                if release_at.tzinfo is None
                else release_at.astimezone(UTC)
            )
            object.__setattr__(self, "release_at", normalized_release)
        if self.provider_order is not None and (
            not isinstance(self.provider_order, int)
            or isinstance(self.provider_order, bool)
            or self.provider_order < 0
        ):
            raise ValueError(
                "subscription model provider order must be a non-negative integer"
            )


@dataclass(frozen=True, slots=True)
class SubscriptionLoginTransaction:
    """Short-lived server-side PKCE transaction for web callbacks."""

    authorization_url: str
    redirect_uri: str
    state: str
    verifier: SecretStr = field(repr=False)

    def __repr__(self) -> str:
        return (
            "SubscriptionLoginTransaction("
            "authorization_url='[redacted]', redirect_uri='[redacted]', "
            "state='[redacted]', verifier='[redacted]')"
        )


class SubscriptionStatus(BaseModel):
    """Safe authentication status returned by a subscription backend."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    provider: SubscriptionProvider
    authenticated: bool = False
    account_label: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _sanitize_metadata(cls, value: Any) -> dict[str, JsonValue]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be an object")
        return _redact_metadata(value)

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> Any:
        return _freeze_json(value)

    @property
    def redacted_metadata(self) -> dict[str, Any]:
        """Return status metadata after removing secret-looking fields."""

        result: dict[str, Any] = {
            "provider": self.provider.value,
            "authenticated": self.authenticated,
            "account_label": self.account_label,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
        }
        for key, value in _redact_metadata(self.metadata).items():
            result.setdefault(key, value)
        return result


class SubscriptionCredential(BaseModel):
    """Authenticated subscription identity with tokens kept out of public output.

    Access and refresh tokens are retained as :class:`~pydantic.SecretStr`
    values for backend use only.  The fields are excluded from every normal
    Pydantic dump and from the model representation; callers should use
    :attr:`redacted_metadata` for status or logging.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    provider: SubscriptionProvider
    account_label: str
    access_token: SecretStr = Field(exclude=True, repr=False)
    refresh_token: SecretStr | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    expires_at: datetime | None = None
    project_id: str | None = None
    account_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("account_label")
    @classmethod
    def _require_account_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("account_label must not be empty")
        return value

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def _require_token(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, SecretStr):
            if not value.get_secret_value().strip():
                raise ValueError("token must not be empty")
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("token must be a non-empty string")
        return SecretStr(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _sanitize_metadata(cls, value: Any) -> dict[str, JsonValue]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be an object")
        return _redact_metadata(value)

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> Any:
        return _freeze_json(value)

    @model_validator(mode="after")
    def _redact_token_values(self) -> SubscriptionCredential:
        secret_values = [self.access_token.get_secret_value()]
        if self.refresh_token is not None:
            secret_values.append(self.refresh_token.get_secret_value())
        safe_metadata = _redact_metadata(self.metadata, secret_values=secret_values)
        object.__setattr__(self, "metadata", _freeze_json(safe_metadata))
        return self

    @property
    def redacted_metadata(self) -> dict[str, Any]:
        """Return display-safe identity and expiry metadata without token values."""

        result: dict[str, Any] = {
            "provider": self.provider.value,
            "account_label": self.account_label,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
            "project_id": self.project_id,
            "account_id": self.account_id,
            "schema_version": self.schema_version,
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at is not None else None
            ),
        }
        for key, value in _redact_metadata(self.metadata).items():
            result.setdefault(key, value)
        return result


@runtime_checkable
class SubscriptionBackend(Protocol):
    """Synchronous contract implemented by each subscription adapter."""

    @property
    def provider(self) -> SubscriptionProvider:
        """Provider served by this backend."""

    @property
    def capabilities(self) -> BackendCapabilities:
        """Feature flags for this backend."""

    def login(self) -> SubscriptionCredential:
        """Run provider authentication and return the acquired credential."""

    def refresh(self) -> SubscriptionCredential:
        """Refresh the current credential and return its replacement."""

    def logout(self) -> None:
        """Revoke or delete the current provider credential."""

    def status(self) -> SubscriptionStatus:
        """Return authentication state without exposing credential material."""

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        """Return every model visible to the authenticated account."""

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Complete a normalized unstructured request."""

    def complete_structured(self, request: CompletionRequest) -> CompletionResult:
        """Complete a normalized request with its requested JSON schema."""


@runtime_checkable
class SubscriptionLifecycleBackend(Protocol):
    """Provider-neutral two-step OAuth lifecycle implemented by adapters."""

    def begin_login(self, redirect_uri: str) -> SubscriptionLoginTransaction:
        """Begin a non-blocking loopback PKCE authorization transaction."""

    def complete_login(
        self,
        code: str,
        state: str,
        transaction: SubscriptionLoginTransaction,
    ) -> SubscriptionCredential:
        """Validate and exchange one pending authorization callback."""


@dataclass(frozen=True, slots=True)
class SubscriptionRuntime:
    """In-memory subscription backend selected for one extraction run.

    The runtime object deliberately carries only the backend handle and its
    provider identity.  Credentials remain owned by the backend/store and are
    never part of configuration, namespaces, or serialized run metadata.
    """

    provider: SubscriptionProvider
    backend: SubscriptionBackend


@dataclass(frozen=True, slots=True)
class SubscriptionRuntimeRouter:
    """Authenticated subscription runtimes indexed by model provider prefix."""

    runtimes: tuple[SubscriptionRuntime, ...]

    def __post_init__(self) -> None:
        providers = [runtime.provider for runtime in self.runtimes]
        if not providers:
            raise ValueError(
                "subscription runtime router requires at least one runtime"
            )
        if len(set(providers)) != len(providers):
            raise ValueError("subscription runtime router providers must be unique")

    def runtime_for_model(self, model: str) -> SubscriptionRuntime:
        provider = subscription_provider_for_model(model)
        for runtime in self.runtimes:
            if runtime.provider is provider:
                return runtime
        raise ValueError(
            f"no authenticated {provider.value} subscription runtime for model {model!r}"
        )


_SECRET_FIELD_PATTERN = (
    r"(?:access[_ -]?token|refresh[_ -]?token|id[_ -]?token|api[_ -]?key|"
    r"client[_ -]?secret|authorization(?:[_ -]?code)?|verifier|token)"
)
_SECRET_KEY_RE = re.compile(
    rf"(?<![A-Za-z0-9]){_SECRET_FIELD_PATTERN}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_AUTHORIZATION_BEARER_RE = re.compile(
    r"""(?P<prefix>["']?Authorization["']?\s*[:=]\s*)
    (?P<quote>["']?)Bearer\s+[^\s,"';)}\]]+(?P=quote)""",
    re.IGNORECASE | re.VERBOSE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"""(?P<prefix>["']?{_SECRET_FIELD_PATTERN}["']?\s*[:=]\s*)
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;)}}\]]+)""",
    re.IGNORECASE | re.VERBOSE,
)
_BEARER_RE = re.compile(r"(\bBearer\s+)[^\s,;)}\]]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def _redact_authorization(match: re.Match[str]) -> str:
    prefix = match.group("prefix")
    quote = match.group("quote")
    return f"{prefix}{quote}[redacted]{quote}"


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = f"{value[0]}[redacted]{value[-1]}"
    else:
        value = "[redacted]"
    return f"{match.group('prefix')}{value}"


def _redact_text(value: str, secret_values: Iterable[str] = ()) -> str:
    """Remove explicit and conventionally labelled secret values from text."""

    redacted = value
    for secret in sorted(
        (secret for secret in secret_values if secret), key=len, reverse=True
    ):
        redacted = redacted.replace(secret, "[redacted]")
    redacted = _AUTHORIZATION_BEARER_RE.sub(_redact_authorization, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(_redact_assignment, redacted)
    redacted = _BEARER_RE.sub(r"\1[redacted]", redacted)
    return _JWT_RE.sub("[redacted]", redacted)


def _redact_metadata_value(value: Any, secret_values: Iterable[str]) -> Any:
    if isinstance(value, Mapping):
        return _redact_metadata(value, secret_values=secret_values)
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, (list, tuple)):
        return [_redact_metadata_value(item, secret_values) for item in value]
    return value


def _redact_metadata(
    value: Mapping[str, Any],
    *,
    secret_values: Iterable[str] = (),
) -> dict[str, Any]:
    """Recursively remove token-shaped keys and values from display metadata."""

    redacted: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key.casefold() == "policy_warning" or _SECRET_KEY_RE.search(key):
            continue
        redacted[key] = _redact_metadata_value(raw_value, secret_values)
    return redacted


class SubscriptionError(RuntimeError):
    """Base class for secret-safe normalized subscription failures."""

    default_category = "provider_error"

    def __init__(
        self,
        message: str = "",
        *,
        provider: SubscriptionProvider | str | None = None,
        category: str | None = None,
        status: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        secret_values: Iterable[str] = (),
    ) -> None:
        self.provider = _coerce_provider(provider)
        self.category = category or self.default_category
        self.status = status
        secrets = tuple(secret for secret in secret_values if secret)
        self._message = _redact_text(str(message), secrets)
        self._metadata = _redact_metadata(metadata or {}, secret_values=secrets)
        super().__init__(self._message)

    @property
    def metadata(self) -> dict[str, Any]:
        """Return normalized non-secret error metadata."""

        result: dict[str, Any] = {
            "provider": _provider_value(self.provider),
            "category": self.category,
            "status": self.status,
        }
        for key, value in self._metadata.items():
            result.setdefault(key, value)
        return result

    @property
    def status_code(self) -> int | None:
        """Alias for callers that use HTTP terminology."""

        return self.status

    @property
    def http_status(self) -> int | None:
        """Alias for callers that use HTTP terminology."""

        return self.status

    def __str__(self) -> str:
        return self._message

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self._message!r}, "
            f"provider={_provider_value(self.provider)!r}, "
            f"category={self.category!r}, status={self.status!r})"
        )


class SubscriptionAuthError(SubscriptionError):
    """Authentication or authorization failure."""

    default_category = "authentication"


class SubscriptionTokenExpired(SubscriptionAuthError):
    """Credential is expired and requires a refresh or login."""

    default_category = "expired_session"


class SubscriptionTransportError(SubscriptionError):
    """Provider transport or response-delivery failure."""

    default_category = "transport"


class SubscriptionPolicyError(SubscriptionError):
    """Provider-policy or terms restriction prevented the request."""

    default_category = "policy"


class SubscriptionUnsupportedRequest(SubscriptionError):
    """Backend cannot preserve the semantics of a requested operation."""

    default_category = "unsupported_capability"


def _coerce_provider(
    provider: SubscriptionProvider | str | None,
) -> SubscriptionProvider | str | None:
    if provider is None or isinstance(provider, SubscriptionProvider):
        return provider
    try:
        return SubscriptionProvider(provider)
    except ValueError:
        return provider


def _provider_value(provider: SubscriptionProvider | str | None) -> str | None:
    if isinstance(provider, SubscriptionProvider):
        return provider.value
    return provider


__all__ = [
    "AuthMode",
    "SubscriptionRuntime",
    "BackendCapabilities",
    "CompletionRequest",
    "CompletionResult",
    "qualify_subscription_model",
    "resolve_subscription_model",
    "subscription_default_model",
    "subscription_model_prefix",
    "validate_subscription_models",
    "SubscriptionAuthError",
    "SubscriptionBackend",
    "SubscriptionCredential",
    "SubscriptionLifecycleBackend",
    "SubscriptionLoginTransaction",
    "SubscriptionError",
    "SubscriptionPolicyError",
    "SubscriptionProvider",
    "SubscriptionStatus",
    "SubscriptionTokenExpired",
    "SubscriptionTransportError",
    "SubscriptionUnsupportedRequest",
]
