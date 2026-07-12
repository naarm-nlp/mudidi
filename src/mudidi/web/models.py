"""Curated multimodal model fallbacks for the local production UI."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_MODEL_API_HOSTS = {
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
}
_MAX_MODEL_RESPONSE_BYTES = 5 * 1024 * 1024


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


@dataclass(frozen=True, slots=True)
class LiveModelOption:
    """Provider-returned model with only non-secret display metadata."""

    model_id: str
    display_name: str
    provider: Provider
    image_input: bool | None


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
            payload = self._fetch(url, headers)
            return _parse_live_models(provider, payload)
        except Exception as exc:
            raise ModelDiscoveryError(
                f"{provider.value} model discovery failed; bundled models remain available"
            ) from exc


class ModelCatalog:
    """Offline model fallback supplemented by live provider lists later."""

    def __init__(self, options: tuple[ModelOption, ...], *, as_of: str) -> None:
        self.options = options
        self.as_of = as_of
        self._by_id = {option.model_id: option for option in options}

    @classmethod
    def bundled(cls) -> ModelCatalog:
        """Return the provider-documented multimodal catalog bundled in v1."""

        openai_source = "https://developers.openai.com/api/docs/models"
        anthropic_source = (
            "https://platform.claude.com/docs/en/about-claude/models/overview"
        )
        gemini_source = "https://ai.google.dev/gemini-api/docs/models"
        direct = (
            ModelOption(
                "openai/gpt-5.6-sol",
                "GPT-5.6 Sol",
                Provider.OPENAI,
                True,
                ("stage1", "stage2", "verification"),
                openai_source,
            ),
            ModelOption(
                "anthropic/claude-fable-5",
                "Claude Fable 5",
                Provider.ANTHROPIC,
                True,
                ("stage1", "stage2", "verification"),
                anthropic_source,
            ),
            ModelOption(
                "openai/gpt-5.6-terra",
                "GPT-5.6 Terra",
                Provider.OPENAI,
                True,
                ("stage1", "stage2"),
                openai_source,
            ),
            ModelOption(
                "openai/gpt-5.6-luna",
                "GPT-5.6 Luna",
                Provider.OPENAI,
                True,
                ("stage1",),
                openai_source,
            ),
            ModelOption(
                "anthropic/claude-opus-4-8",
                "Claude Opus 4.8",
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
                "gemini/gemini-3.5-flash",
                "Gemini 3.5 Flash",
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
                "gemini/gemini-3.1-flash-lite",
                "Gemini 3.1 Flash-Lite",
                Provider.GEMINI,
                True,
                ("stage1",),
                gemini_source,
            ),
        )
        return cls(
            direct,
            as_of="2026-07-12",
        )

    def get(self, model_id: str) -> ModelOption:
        """Return one bundled model or raise ``KeyError``."""

        return self._by_id[model_id]

    def for_provider(self, provider: Provider) -> tuple[ModelOption, ...]:
        """Return curated direct models for one provider."""

        return tuple(option for option in self.options if option.provider is provider)
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
    return tuple(found[model_id] for model_id in sorted(found))


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
        image_input = True
    elif provider is Provider.OPENAI:
        if not raw_id.startswith(("gpt-", "o")):
            return None
        display = raw_id
        image_input = None
    else:
        if not raw_id.startswith("claude-"):
            return None
        display = str(raw.get("display_name") or raw_id)
        image_input = True
    if not raw_id:
        return None
    return LiveModelOption(
        model_id=normalize_custom_model(provider, raw_id),
        display_name=display,
        provider=provider,
        image_input=image_input,
    )


def _fetch_json(url: str, headers: Mapping[str, str]) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in _MODEL_API_HOSTS:
        raise ValueError("model discovery URL is not allowlisted")
    request = Request(url, headers=dict(headers), method="GET")
    # Scheme and host are checked against the fixed provider allowlist above.
    with urlopen(request, timeout=5) as response:  # nosec B310
        raw = response.read(_MAX_MODEL_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_MODEL_RESPONSE_BYTES:
        raise ValueError("provider model list response is too large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response is not an object")
    return payload
