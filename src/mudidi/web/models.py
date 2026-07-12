"""Curated multimodal model fallbacks for the local production UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
        return cls(
            (
                ModelOption(
                    "openai/gpt-5.6",
                    "GPT-5.6 Sol",
                    Provider.OPENAI,
                    True,
                    ("stage1", "stage2", "verification"),
                    openai_source,
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
                    "anthropic/claude-sonnet-4-6",
                    "Claude Sonnet 4.6",
                    Provider.ANTHROPIC,
                    True,
                    ("stage1", "stage2"),
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
                    "gemini/gemini-3-flash",
                    "Gemini 3 Flash",
                    Provider.GEMINI,
                    True,
                    ("stage1",),
                    gemini_source,
                ),
            ),
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
