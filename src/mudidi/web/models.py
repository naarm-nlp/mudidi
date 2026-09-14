"""Curated multimodal model fallbacks for the local production UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import cmp_to_key
import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock, RLock
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from mudidi.llm.reasoning import ReasoningProfile, resolve_reasoning_profile

_MODEL_API_HOSTS = {
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
}
_MAX_MODEL_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_DISCOVERY_PAGES = 20


class _RejectRedirects(HTTPRedirectHandler):
    """Prevent provider credentials from crossing hosts through redirects."""

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_MODEL_API_OPENER = build_opener(_RejectRedirects())
_INCOMPATIBLE_MODEL_MARKERS = (
    "audio",
    "classifier",
    "embedding",
    "gpt-image",
    "image-generation",
    "image_generation",
    "imagen",
    "moderation",
    "rerank",
    "safety",
    "speech",
    "transcribe",
    "transcription",
    "tts",
    "whisper",
)
_MODEL_CACHE_TTL = timedelta(minutes=15)


_ModelRecord = TypeVar("_ModelRecord", bound="ModelOption | LiveModelOption")
_MODEL_VERSION = re.compile(r"(?<![A-Za-z])\d+(?:[.-]\d+)*")


class Provider(StrEnum):
    """Provider routes supported by MUDIDI through LiteLLM."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ModelOption:
    """One curated model known to support MUDIDI's input requirements."""

    model_id: str
    display_name: str
    provider: Provider
    image_input: bool
    recommended_for: tuple[str, ...]
    source_url: str
    reasoning: ReasoningProfile | None = None
    release_at: datetime | None = None
    provider_order: int | None = None

    def __post_init__(self) -> None:
        if self.reasoning is None:
            object.__setattr__(
                self,
                "reasoning",
                resolve_reasoning_profile(self.provider.value, self.model_id),
            )


@dataclass(frozen=True, slots=True)
class LiveModelOption:
    """Provider-returned model with only non-secret display metadata."""

    model_id: str
    display_name: str
    provider: Provider
    image_input: bool | None
    reasoning: ReasoningProfile | None = None
    release_at: datetime | None = None
    provider_order: int | None = None

    def __post_init__(self) -> None:
        if self.reasoning is None:
            object.__setattr__(
                self,
                "reasoning",
                resolve_reasoning_profile(self.provider.value, self.model_id),
            )


def _reasoning_profile(model: ModelOption | LiveModelOption) -> ReasoningProfile:
    profile = model.reasoning
    if profile is None:
        raise RuntimeError("model reasoning profile was not initialized")
    return profile


def _numeric_revision(model_id: str) -> tuple[int, ...]:
    return tuple(
        int(value)
        for version in _MODEL_VERSION.findall(model_id.lower())
        for value in re.split(r"[.-]", version)
    )


def _compare_models_newest_first(
    left: ModelOption | LiveModelOption,
    right: ModelOption | LiveModelOption,
) -> int:
    if left.release_at != right.release_at:
        if left.release_at is None:
            return 1
        if right.release_at is None:
            return -1
        return -1 if left.release_at > right.release_at else 1
    if left.provider_order != right.provider_order:
        if left.provider_order is None:
            return 1
        if right.provider_order is None:
            return -1
        return -1 if left.provider_order < right.provider_order else 1
    left_revision = _numeric_revision(left.model_id)
    right_revision = _numeric_revision(right.model_id)
    if left_revision != right_revision:
        return -1 if left_revision > right_revision else 1
    left_tie = (left.display_name.casefold(), left.model_id)
    right_tie = (right.display_name.casefold(), right.model_id)
    return (left_tie > right_tie) - (left_tie < right_tie)


def sort_models_newest_first(
    models: Sequence[_ModelRecord],
) -> tuple[_ModelRecord, ...]:
    """Return deterministic newest-first model records."""

    return tuple(sorted(models, key=cmp_to_key(_compare_models_newest_first)))


def _gemini_subscription_family_rank(
    model: ModelOption | LiveModelOption,
) -> int:
    """Place Gemini Pro ahead of Flash without disturbing family-local order."""

    family_tokens = set(re.split(r"[/_.-]+", model.model_id.casefold()))
    if "pro" in family_tokens:
        return 0
    if "flash" in family_tokens:
        return 1
    return 2


def _parse_release_at(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class CatalogStage(StrEnum):
    """Logical inference stages used to group model recommendations."""

    STAGE1 = "stage1"
    STAGE2 = "stage2"
    VERIFICATION = "verification"


class CatalogAuthMode(StrEnum):
    """Credential source used to build a model catalog."""

    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """One non-secret model option returned to the browser."""

    model_id: str
    display_name: str
    compatibility: str
    reasoning: ReasoningProfile = field(
        default_factory=lambda: resolve_reasoning_profile("custom", "unknown")
    )


@dataclass(frozen=True, slots=True)
class CatalogWarning:
    """Fixed degraded-mode warning safe for browser display."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """Stage-grouped, non-secret model catalog response."""

    provider: Provider
    stage: CatalogStage
    auth_mode: CatalogAuthMode
    source: str
    stale: bool
    fetched_at: datetime | None
    recommended: tuple[CatalogItem, ...]
    available: tuple[CatalogItem, ...]
    warning: CatalogWarning | None = None

    def to_payload(self) -> dict[str, object]:
        """Return JSON-compatible browser data without credential material."""

        def item_payload(item: CatalogItem) -> dict[str, object]:
            return {
                "model_id": item.model_id,
                "display_name": item.display_name,
                "compatibility": item.compatibility,
                "reasoning": item.reasoning.to_payload(),
            }

        warning = (
            None
            if self.warning is None
            else {"code": self.warning.code, "message": self.warning.message}
        )
        return {
            "provider": self.provider.value,
            "stage": self.stage.value,
            "auth_mode": self.auth_mode.value,
            "source": self.source,
            "stale": self.stale,
            "fetched_at": (
                self.fetched_at.isoformat().replace("+00:00", "Z")
                if self.fetched_at is not None
                else None
            ),
            "recommended": [item_payload(item) for item in self.recommended],
            "available": [item_payload(item) for item in self.available],
            "warning": warning,
        }


class _SecretCredential(Protocol):
    def get_secret_value(self) -> str:
        """Return the provider credential at the network boundary."""


class ModelDiscoveryError(RuntimeError):
    """Safe provider-list failure that never includes request credentials."""


FetchModels = Callable[[str, Mapping[str, str]], dict[str, Any]]


class ModelDiscovery:
    """Fetch current models from fixed official provider endpoints."""

    def __init__(self, *, fetch: FetchModels | None = None) -> None:
        self._fetch = fetch or _fetch_json

    def discover(
        self,
        provider: Provider,
        *,
        api_key: str,
    ) -> tuple[LiveModelOption, ...]:
        """Return normalized provider models or a credential-safe error."""

        try:
            url, headers = _discovery_request(provider, api_key)
            found: dict[str, LiveModelOption] = {}
            seen_urls: set[str] = set()
            for _ in range(_MAX_DISCOVERY_PAGES):
                if url in seen_urls:
                    raise ValueError("provider model pagination repeated a page")
                seen_urls.add(url)
                payload = self._fetch(url, headers)
                page_models = _parse_live_models(provider, payload)
                model_field = "models" if provider is Provider.GEMINI else "data"
                if not payload.get(model_field, []):
                    return sort_models_newest_first(tuple(found.values()))
                for model in page_models:
                    found[model.model_id] = model
                next_url = _next_page_url(provider, url, payload)
                if next_url is None:
                    return sort_models_newest_first(tuple(found.values()))
                url = next_url
            raise ValueError("provider model pagination exceeded its page limit")
        except Exception as exc:
            raise ModelDiscoveryError(
                f"{provider.value} model discovery failed; bundled models remain available"
            ) from exc


class ModelCatalog:
    """Offline model fallback supplemented by live provider lists later."""

    def __init__(
        self,
        options: tuple[ModelOption, ...],
        *,
        as_of: str,
    ) -> None:
        self.options = options
        self.as_of = as_of
        self._by_id = {option.model_id: option for option in options}

    @classmethod
    def bundled(cls) -> ModelCatalog:
        """Return the provider-documented multimodal catalog bundled in v1."""

        anthropic_source = (
            "https://platform.claude.com/docs/en/about-claude/models/overview"
        )
        gemini_source = "https://ai.google.dev/gemini-api/docs/models"
        direct = (
            ModelOption(
                "anthropic/claude-fable-5",
                "Claude Fable 5",
                Provider.ANTHROPIC,
                True,
                ("stage1", "stage2", "verification"),
                anthropic_source,
            ),
            ModelOption(
                "anthropic/claude-opus-5",
                "Claude Opus 5",
                Provider.ANTHROPIC,
                True,
                ("stage1", "stage2", "verification"),
                anthropic_source,
            ),
            ModelOption(
                "anthropic/claude-sonnet-5",
                "Claude Sonnet 5",
                Provider.ANTHROPIC,
                True,
                ("stage1", "stage2"),
                anthropic_source,
            ),
            ModelOption(
                "anthropic/claude-haiku-4-5",
                "Claude Haiku 4.5",
                Provider.ANTHROPIC,
                True,
                ("stage1",),
                anthropic_source,
            ),
            ModelOption(
                "gemini/gemini-3.8-flash",
                "Gemini 3.8 Flash",
                Provider.GEMINI,
                True,
                ("stage1", "stage2"),
                gemini_source,
            ),
            ModelOption(
                "gemini/gemini-3.1-pro-preview",
                "Gemini 3.1 Pro",
                Provider.GEMINI,
                True,
                ("stage1", "stage2", "verification"),
                gemini_source,
            ),
            ModelOption(
                "gemini/gemini-3.5-flash-lite",
                "Gemini 3.5 Flash-Lite",
                Provider.GEMINI,
                True,
                ("stage1",),
                gemini_source,
            ),
        )
        return cls(direct, as_of="2026-07-12")

    def get(self, model_id: str) -> ModelOption:
        """Return one bundled model or raise ``KeyError``."""

        return self._by_id[model_id]

    def for_provider(self, provider: Provider) -> tuple[ModelOption, ...]:
        """Return curated direct models for one provider."""

        return tuple(option for option in self.options if option.provider is provider)


@dataclass(frozen=True, slots=True)
class _ModelCacheEntry:
    models: tuple[LiveModelOption, ...]
    fetched_at: datetime


class ModelCatalogService:
    """Build stage-aware model catalogs without exposing credentials."""

    def __init__(
        self,
        *,
        catalog: ModelCatalog,
        discovery: ModelDiscovery,
        credential_resolver: Callable[[Provider], _SecretCredential | None],
        subscription_auth_resolver: Callable[[Provider], bool | str] | None = None,
        subscription_discovery_resolver: (
            Callable[[Provider], tuple[LiveModelOption, ...]] | None
        ) = None,
        clock: Callable[[], datetime] | None = None,
        fingerprint_key: bytes | None = None,
    ) -> None:
        self._catalog = catalog
        self._discovery = discovery
        self._credential_resolver = credential_resolver
        self._subscription_auth_resolver = subscription_auth_resolver or (
            lambda _provider: False
        )
        self._subscription_discovery_resolver = (
            subscription_discovery_resolver or self._missing_subscription_discovery
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fingerprint_key = fingerprint_key or secrets.token_bytes(32)
        self._cache: dict[tuple[Provider, str], _ModelCacheEntry] = {}
        self._cache_lock = RLock()
        self._refresh_locks = {}
        self._refresh_generations: dict[tuple[Provider, str], int] = {}
        self._refresh_failures: dict[
            tuple[Provider, str],
            tuple[int, _ModelCacheEntry | None],
        ] = {}

    def list_models(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        force_refresh: bool = False,
    ) -> CatalogResult:
        """Return provider-discovered or curated models for one stage."""

        if auth_mode is CatalogAuthMode.SUBSCRIPTION:
            subscription_identity = self._subscription_auth_resolver(provider)
            if not subscription_identity:
                return self._authentication_required_result(
                    provider,
                    stage=stage,
                    auth_mode=auth_mode,
                )
            if provider in {Provider.OPENAI, Provider.GEMINI, Provider.ANTHROPIC}:
                discriminator = (
                    subscription_identity
                    if isinstance(subscription_identity, str)
                    else "subscription"
                )
                return self._discovered_result(
                    provider,
                    stage=stage,
                    auth_mode=auth_mode,
                    cache_key=(provider, f"subscription:{discriminator}"),
                    discover=lambda: self._subscription_discovery_resolver(provider),
                    force_refresh=force_refresh,
                )
            return self._curated_result(
                provider,
                stage=stage,
                auth_mode=auth_mode,
                options=(),
                source="unavailable",
            )

        credential = self._credential_resolver(provider)
        if credential is None:
            return self._authentication_required_result(
                provider,
                stage=stage,
                auth_mode=auth_mode,
            )
        api_key = credential.get_secret_value()
        fingerprint = hashlib.blake2b(
            api_key.encode("utf-8"),
            digest_size=16,
            key=self._fingerprint_key,
        ).hexdigest()
        return self._discovered_result(
            provider,
            stage=stage,
            auth_mode=auth_mode,
            cache_key=(provider, fingerprint),
            discover=lambda: self._discovery.discover(
                provider,
                api_key=api_key,
            ),
            force_refresh=force_refresh,
        )

    @staticmethod
    def _missing_subscription_discovery(
        _provider: Provider,
    ) -> tuple[LiveModelOption, ...]:
        raise ModelDiscoveryError("subscription model discovery is unavailable")

    def _discovered_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        cache_key: tuple[Provider, str],
        discover: Callable[[], tuple[LiveModelOption, ...]],
        force_refresh: bool,
    ) -> CatalogResult:
        now = self._clock()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            refresh_lock = self._refresh_locks.setdefault(cache_key, Lock())
            generation = self._refresh_generations.get(cache_key, 0)
        if (
            cached is not None
            and not force_refresh
            and now - cached.fetched_at < _MODEL_CACHE_TTL
        ):
            return self._live_result(
                provider,
                stage=stage,
                auth_mode=auth_mode,
                models=cached.models,
                source="cached",
                fetched_at=cached.fetched_at,
            )

        with refresh_lock:
            now = self._clock()
            with self._cache_lock:
                current = self._cache.get(cache_key)
                completed_generation = self._refresh_generations.get(cache_key, 0)
                failure = self._refresh_failures.get(cache_key)
            if completed_generation != generation:
                if failure is not None and failure[0] == completed_generation:
                    return self._unavailable_result(
                        provider,
                        stage=stage,
                        auth_mode=auth_mode,
                        cached=failure[1],
                    )
                if current is not None:
                    return self._live_result(
                        provider,
                        stage=stage,
                        auth_mode=auth_mode,
                        models=current.models,
                        source="cached",
                        fetched_at=current.fetched_at,
                    )
            if (
                current is not None
                and now - current.fetched_at < _MODEL_CACHE_TTL
                and (not force_refresh or current is not cached)
            ):
                return self._live_result(
                    provider,
                    stage=stage,
                    auth_mode=auth_mode,
                    models=current.models,
                    source="cached",
                    fetched_at=current.fetched_at,
                )
            cached = current
            try:
                models = discover()
            except ModelDiscoveryError:
                with self._cache_lock:
                    completed_generation = (
                        self._refresh_generations.get(cache_key, 0) + 1
                    )
                    self._refresh_generations[cache_key] = completed_generation
                    self._refresh_failures[cache_key] = (
                        completed_generation,
                        cached,
                    )
                return self._unavailable_result(
                    provider,
                    stage=stage,
                    auth_mode=auth_mode,
                    cached=cached,
                )
            entry = _ModelCacheEntry(models=models, fetched_at=self._clock())
            with self._cache_lock:
                self._cache[cache_key] = entry
                self._refresh_generations[cache_key] = (
                    self._refresh_generations.get(cache_key, 0) + 1
                )
                self._refresh_failures.pop(cache_key, None)
            return self._live_result(
                provider,
                stage=stage,
                auth_mode=auth_mode,
                models=models,
                source="live",
                fetched_at=entry.fetched_at,
            )

    def _unavailable_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        cached: _ModelCacheEntry | None,
    ) -> CatalogResult:
        if cached is not None:
            return self._live_result(
                provider,
                stage=stage,
                auth_mode=auth_mode,
                models=cached.models,
                source="stale",
                stale=True,
                fetched_at=cached.fetched_at,
                warning=CatalogWarning(
                    "provider_unavailable",
                    "Provider model discovery is unavailable; showing the last successful list.",
                ),
            )
        if provider is not Provider.OPENAI and auth_mode is CatalogAuthMode.API_KEY:
            return self._bundled_result(
                provider,
                stage=stage,
                warning=CatalogWarning(
                    "provider_unavailable",
                    "Provider model discovery is unavailable; bundled models remain available.",
                ),
            )
        return self._curated_result(
            provider,
            stage=stage,
            auth_mode=auth_mode,
            options=(),
            source="unavailable",
            warning=CatalogWarning(
                "provider_unavailable",
                "Provider model discovery is unavailable; no model list is available.",
            ),
        )

    def invalidate(self, provider: Provider) -> None:
        """Invalidate cached data for one provider."""

        with self._cache_lock:
            keys = [key for key in self._cache if key[0] is provider]
            for key in keys:
                del self._cache[key]

    def _live_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        models: tuple[LiveModelOption, ...],
        source: str,
        fetched_at: datetime,
        stale: bool = False,
        warning: CatalogWarning | None = None,
    ) -> CatalogResult:
        curated = (
            {}
            if provider is Provider.OPENAI
            else {
                option.model_id: option
                for option in self._catalog.for_provider(provider)
            }
        )
        recommended: list[CatalogItem] = []
        available: list[CatalogItem] = []
        ordered_models = sort_models_newest_first(models)
        if (
            provider is Provider.GEMINI
            and auth_mode is CatalogAuthMode.SUBSCRIPTION
        ):
            ordered_models = tuple(
                sorted(ordered_models, key=_gemini_subscription_family_rank)
            )
        for model in ordered_models:
            known = curated.get(model.model_id)
            item = CatalogItem(
                model.model_id,
                model.display_name,
                "verified" if known is not None else "unverified",
                _reasoning_profile(model),
            )
            if (
                auth_mode is not CatalogAuthMode.SUBSCRIPTION
                and known is not None
                and stage.value in known.recommended_for
            ):
                recommended.append(item)
            else:
                available.append(item)
        return CatalogResult(
            provider=provider,
            stage=stage,
            auth_mode=auth_mode,
            source=source,
            stale=stale,
            fetched_at=fetched_at,
            recommended=tuple(recommended),
            available=tuple(available),
            warning=warning,
        )

    def _authentication_required_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
    ) -> CatalogResult:
        return self._curated_result(
            provider,
            stage=stage,
            auth_mode=auth_mode,
            options=(),
            source="authentication_required",
            warning=CatalogWarning(
                "authentication_required",
                "Authenticate this provider to load models.",
            ),
        )

    def _bundled_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        warning: CatalogWarning,
    ) -> CatalogResult:
        return self._curated_result(
            provider,
            stage=stage,
            auth_mode=CatalogAuthMode.API_KEY,
            options=self._catalog.for_provider(provider),
            source="bundled",
            warning=warning,
        )

    def _curated_result(
        self,
        provider: Provider,
        *,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        options: tuple[ModelOption, ...],
        source: str,
        warning: CatalogWarning | None = None,
    ) -> CatalogResult:
        recommended: list[CatalogItem] = []
        available: list[CatalogItem] = []
        for option in sort_models_newest_first(options):
            item = CatalogItem(
                option.model_id,
                option.display_name,
                "verified",
                _reasoning_profile(option),
            )
            target = (
                recommended
                if auth_mode is not CatalogAuthMode.SUBSCRIPTION
                and stage.value in option.recommended_for
                else available
            )
            target.append(item)
        return CatalogResult(
            provider=provider,
            stage=stage,
            auth_mode=auth_mode,
            source=source,
            stale=False,
            fetched_at=None,
            recommended=tuple(recommended),
            available=tuple(available),
            warning=warning,
        )


def normalize_custom_model(provider: Provider, model_id: str) -> str:
    """Normalize custom entry to the LiteLLM provider naming used by MUDIDI."""

    cleaned = model_id.strip().strip("/")
    if not cleaned:
        raise ValueError("custom model identifier must not be empty")
    if provider is Provider.CUSTOM:
        return cleaned
    prefix = f"{provider.value}/"
    if cleaned.startswith(prefix):
        return cleaned
    return f"{prefix}{cleaned}"


def _discovery_request(
    provider: Provider,
    api_key: str,
) -> tuple[str, dict[str, str]]:
    if provider is Provider.OPENAI:
        return (
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {api_key}"},
        )
    if provider is Provider.ANTHROPIC:
        return (
            "https://api.anthropic.com/v1/models?limit=1000",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if provider is Provider.GEMINI:
        return (
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            {"x-goog-api-key": api_key},
        )
    if provider is Provider.OPENROUTER:
        return (
            "https://openrouter.ai/api/v1/models?input_modalities=image",
            {"Authorization": f"Bearer {api_key}"},
        )
    raise ValueError("custom routing has no provider model-list endpoint")


def _next_page_url(
    provider: Provider,
    current_url: str,
    payload: Mapping[str, Any],
) -> str | None:
    if provider is Provider.ANTHROPIC:
        has_more = payload.get("has_more", False)
        if not isinstance(has_more, bool):
            raise ValueError("provider model pagination field is malformed")
        if not has_more:
            return None
        parameter = "after_id"
        token = payload.get("last_id")
    elif provider is Provider.GEMINI:
        parameter = "pageToken"
        if "nextPageToken" not in payload:
            return None
        token = payload["nextPageToken"]
    else:
        return None
    if not isinstance(token, str) or not token:
        raise ValueError("provider model pagination token is invalid")
    parsed = _validated_pagination_url(provider, current_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[parameter] = token
    next_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
    _validated_pagination_url(provider, next_url)
    return next_url


def _validated_pagination_url(provider: Provider, url: str):
    expected_host = {
        Provider.ANTHROPIC: "api.anthropic.com",
        Provider.GEMINI: "generativelanguage.googleapis.com",
    }.get(provider)
    parsed = urlsplit(url)
    if (
        expected_host is None
        or parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.netloc != expected_host
    ):
        raise ValueError("provider model pagination URL is not allowlisted")
    return parsed


def _is_generation_candidate(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _INCOMPATIBLE_MODEL_MARKERS)


def _parse_live_models(
    provider: Provider,
    payload: dict[str, Any],
) -> tuple[LiveModelOption, ...]:
    raw_models = payload.get("models" if provider is Provider.GEMINI else "data", [])
    if not isinstance(raw_models, list):
        raise ValueError("provider model list is malformed")
    found: dict[str, LiveModelOption] = {}
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_live_model(provider, raw)
        if parsed is not None:
            found[parsed.model_id] = parsed
    return sort_models_newest_first(tuple(found.values()))


def _parse_live_model(
    provider: Provider,
    raw: dict[str, Any],
) -> LiveModelOption | None:
    raw_id = str(raw.get("name" if provider is Provider.GEMINI else "id", ""))
    if provider is Provider.GEMINI:
        methods = raw.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            return None
        raw_id = str(raw.get("baseModelId") or raw_id.removeprefix("models/"))
        if not _is_generation_candidate(raw_id):
            return None
        display = str(raw.get("displayName") or raw_id)
        image_input: bool | None = True
    elif provider is Provider.OPENROUTER:
        architecture = raw.get("architecture", {})
        modalities = (
            architecture.get("input_modalities", [])
            if isinstance(architecture, dict)
            else []
        )
        if "image" not in modalities:
            return None
        display = str(raw.get("name") or raw_id)
        if not _is_generation_candidate(raw_id):
            return None
        image_input = True
    elif provider is Provider.OPENAI:
        if (
            not isinstance(raw.get("id"), str)
            or not raw_id
            or not _is_generation_candidate(raw_id)
        ):
            return None
        display = raw_id
        image_input = None
    else:
        if not raw_id.startswith("claude-") or not _is_generation_candidate(raw_id):
            return None
        display = str(raw.get("display_name") or raw_id)
        image_input = True
    if not raw_id:
        return None
    model_id = normalize_custom_model(provider, raw_id)
    priority = raw.get("priority")
    provider_order = (
        priority
        if isinstance(priority, int)
        and not isinstance(priority, bool)
        and priority >= 0
        else None
    )
    return LiveModelOption(
        model_id=model_id,
        display_name=display,
        provider=provider,
        image_input=image_input,
        reasoning=resolve_reasoning_profile(
            provider.value,
            model_id,
            advertised_efforts=raw.get("supported_reasoning_levels"),
            advertised_default=raw.get("default_reasoning_level"),
        ),
        release_at=_parse_release_at(raw.get("created_at", raw.get("created"))),
        provider_order=provider_order,
    )


def _fetch_json(url: str, headers: Mapping[str, str]) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in _MODEL_API_HOSTS:
        raise ValueError("model discovery URL is not allowlisted")
    request = Request(url, headers=dict(headers), method="GET")
    # Scheme and host are checked against the fixed provider allowlist above.
    with _MODEL_API_OPENER.open(request, timeout=5) as response:
        response_url = response.geturl() if hasattr(response, "geturl") else url
        redirected = urlsplit(response_url)
        if redirected.scheme != "https" or redirected.hostname not in _MODEL_API_HOSTS:
            raise ValueError("model discovery URL is not allowlisted")
        raw = response.read(_MAX_MODEL_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_MODEL_RESPONSE_BYTES:
        raise ValueError("provider model list response is too large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response is not an object")
    return payload
