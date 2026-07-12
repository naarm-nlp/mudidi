"""Validated form models for YAML-free production inference configuration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from mudidi.config.yaml_config import (
    AgenticConfig,
    InferenceConfig,
    InputConfig,
    MathpixConfig,
    ModelsConfig,
    OutputConfig,
    PipelineConfig,
    RuntimeConfig,
    VlmConfig,
)
from mudidi.schemas.dictionary_profile import (
    DictionaryProfile,
    InformationType,
    ProfileLanguage,
)
from mudidi.web.models import Provider, normalize_custom_model


class PipelineChoice(StrEnum):
    """Safe production workflows exposed by the local UI."""

    COMPLETE = "complete"
    TRANSCRIPTION = "transcription"
    STRUCTURE = "structure"
    DISCOVER_RULES = "discover_rules"


class QualityChoice(StrEnum):
    """User-facing verification presets."""

    STANDARD = "standard"
    VERIFIED = "verified"
    CUSTOM = "custom"


ProviderName = Literal["anthropic", "openai", "gemini", "openrouter", "custom"]
ReasoningChoice = Literal["none", "low", "medium", "high"]

_PIPELINE_STAGE = {
    PipelineChoice.COMPLETE: "all",
    PipelineChoice.TRANSCRIPTION: "1",
    PipelineChoice.STRUCTURE: "2",
    PipelineChoice.DISCOVER_RULES: "2-pass-1",
}


class NewRunForm(BaseModel):
    """Complete non-secret browser form state for one production run."""

    model_config = ConfigDict(extra="forbid")

    pages: Path
    output_directory: Path
    output_policy: Literal["new", "resume"] = "new"
    dictionary_pages: str | None = None
    introduction: Path | None = None
    introduction_pages: str | None = None
    alphabet: Path | None = None
    ocr_text: Path | None = None
    profile_headword_language: str | None = None
    profile_headword_script: str | None = None
    profile_target_languages: list[str] = Field(default_factory=list)
    profile_target_scripts: list[str] = Field(default_factory=list)
    profile_page_layout: str | None = Field(default=None, max_length=2000)
    profile_information_types: list[InformationType] = Field(default_factory=list)
    profile_other_information_types: str | None = Field(default=None, max_length=1000)
    toolbox_pdf: Path | None = None

    pipeline: PipelineChoice = PipelineChoice.COMPLETE
    stage1_mode: Literal["flat", "column"] = "flat"
    strategy: Literal["two_stage", "vlm_ocr", "mathpix_ocr"] = "two_stage"
    stage1_guides: Path | None = None
    stage2_guides: Path | None = None
    parse_rules_pages: list[str] = Field(default_factory=list)
    parse_rules_file: Path | None = None

    provider: ProviderName
    model: str | None = None
    reasoning: ReasoningChoice = "low"
    stage1_model: str | None = None
    stage1_custom_model: str | None = None
    stage2_pass1_model: str | None = None
    stage2_pass1_custom_model: str | None = None
    stage2_pass2_model: str | None = None
    stage2_pass2_custom_model: str | None = None
    openrouter_provider: str | None = Field(default=None, max_length=100)
    temperature: float = Field(default=0.1, ge=0.0)

    quality: QualityChoice = QualityChoice.VERIFIED
    verify_stage1: bool = False
    verify_stage2: bool = False
    max_iterations: int = Field(default=2, ge=0, le=10)
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    verifier_patches: bool = True
    require_concrete_retry: bool = True
    evaluator_model: str | None = None
    rewriter_model: str | None = None
    evaluator_reasoning: ReasoningChoice | None = None
    rewriter_reasoning: ReasoningChoice | None = None

    batch_size: int = Field(default=1, ge=1, le=32)
    page_limit: int | None = Field(default=None, ge=1)
    prompt_cache: Literal["auto", "off"] = "auto"
    media_reference: Literal["auto", "inline", "file-uri"] = "auto"

    vlm_model: Literal["mineru2.5-pro", "paddleocr-vl-1.5", "glm-ocr"] | None = None
    vlm_dpi: int = Field(default=200, ge=72)
    mineru_batch_size: int = Field(default=8, ge=1)
    mineru_max_new_tokens: int = Field(default=1024, ge=1)
    mineru_backend: Literal["transformers", "vllm"] = "transformers"
    paddle_rec_backend: Literal["native", "vllm-server"] = "native"
    paddle_server_url: str | None = None
    paddle_auto_server: bool = True
    paddle_server_port: int = Field(default=8765, ge=1, le=65535)
    paddle_server_python: Path | None = None
    glm_prompt: str = "Text Recognition:"
    glm_max_new_tokens: int = Field(default=8192, ge=1)
    glm_backend: Literal["transformers", "vllm"] = "transformers"
    glm_auto_server: bool = True
    glm_server_url: str | None = None
    glm_server_port: int = Field(default=8081, ge=1, le=65535)
    glm_server_python: Path | None = None
    mathpix_poll_interval_seconds: float = Field(default=3.0, gt=0)
    mathpix_max_wait_seconds: float = Field(default=600.0, gt=0)
    mathpix_request_timeout_seconds: float = Field(default=60.0, gt=0)

    @computed_field
    @property
    def requires_parse_rule_review(self) -> bool:
        """Return whether this workflow stops at the human checkpoint."""

        return self.pipeline is not PipelineChoice.TRANSCRIPTION

    def to_inference_config(self) -> InferenceConfig:
        """Build the existing authoritative typed production configuration."""

        if self.pages.suffix.lower() == ".pdf" and not self.dictionary_pages:
            raise ValueError("dictionary_pages is required for PDF input")
        output = self.output_directory.expanduser().resolve()
        if output.exists() and not output.is_dir():
            raise ValueError("output path exists and is not a directory")
        if self.output_policy == "new" and output.is_dir() and any(output.iterdir()):
            raise ValueError(
                "output directory already contains files; choose resume or another path"
            )

        stage = _PIPELINE_STAGE[self.pipeline]
        verify_stage1, verify_stage2 = self._verification_stages()
        default_model, stage1_model, pass1_model, pass2_model = self._stage_models()
        effective_reasoning = "low" if self.reasoning == "none" else self.reasoning
        return InferenceConfig(
            input=InputConfig(
                pages=self.pages.expanduser().resolve(),
                dictionary_pages=self.dictionary_pages,
                introduction=(
                    self.introduction.expanduser().resolve()
                    if self.introduction
                    else None
                ),
                introduction_pages=self.introduction_pages,
                alphabet=self.alphabet.expanduser().resolve()
                if self.alphabet
                else None,
                ocr_text=self.ocr_text.expanduser().resolve()
                if self.ocr_text
                else None,
                dictionary_profile=self._dictionary_profile(),
                toolbox_pdf=(
                    self.toolbox_pdf.expanduser().resolve()
                    if self.toolbox_pdf
                    else None
                ),
            ),
            output=OutputConfig(directory=output),
            pipeline=PipelineConfig(
                stage=stage,
                strategy=self.strategy,
                stage1_mode=self.stage1_mode,
                stage1_typography=False,
                parse_rules_pages=self.parse_rules_pages,
                parse_rules_file=(
                    self.parse_rules_file.expanduser().resolve()
                    if self.parse_rules_file
                    else None
                ),
                stage1_guides=(
                    self.stage1_guides.expanduser().resolve()
                    if self.stage1_guides
                    else None
                ),
                stage2_guides=(
                    self.stage2_guides.expanduser().resolve()
                    if self.stage2_guides
                    else None
                ),
            ),
            models=ModelsConfig(
                default=default_model,
                stage1=stage1_model,
                stage2_pass1=pass1_model,
                stage2_pass2=pass2_model,
                openrouter_provider=self._resolved_openrouter_provider(),
                stage1_reasoning=effective_reasoning,
                stage2_reasoning=effective_reasoning,
                temperature=self.temperature,
            ),
            agentic=AgenticConfig(
                stage1=verify_stage1,
                stage2=verify_stage2,
                max_iterations=self.max_iterations,
                evaluator_model=_clean_optional(self.evaluator_model),
                rewriter_model=_clean_optional(self.rewriter_model),
                reasoning=effective_reasoning,
                evaluator_reasoning=self.evaluator_reasoning,
                rewriter_reasoning=self.rewriter_reasoning,
                min_retry_confidence=self.min_retry_confidence,
                verifier_patches=self.verifier_patches,
                require_concrete_retry=self.require_concrete_retry,
            ),
            runtime=RuntimeConfig(
                batch_size=self.batch_size,
                limit=self.page_limit,
                prompt_cache=self.prompt_cache,
                media_reference=self.media_reference,
            ),
            vlm=VlmConfig(
                model=self.vlm_model,
                dpi=self.vlm_dpi,
                mineru_batch_size=self.mineru_batch_size,
                mineru_max_new_tokens=self.mineru_max_new_tokens,
                mineru_backend=self.mineru_backend,
                paddle_rec_backend=self.paddle_rec_backend,
                paddle_server_url=_clean_optional(self.paddle_server_url),
                paddle_auto_server=self.paddle_auto_server,
                paddle_server_port=self.paddle_server_port,
                paddle_server_python=_resolved_optional(self.paddle_server_python),
                glm_prompt=self.glm_prompt,
                glm_max_new_tokens=self.glm_max_new_tokens,
                glm_backend=self.glm_backend,
                glm_auto_server=self.glm_auto_server,
                glm_server_url=_clean_optional(self.glm_server_url),
                glm_server_port=self.glm_server_port,
                glm_server_python=_resolved_optional(self.glm_server_python),
            ),
            mathpix=MathpixConfig(
                poll_interval_seconds=self.mathpix_poll_interval_seconds,
                max_wait_seconds=self.mathpix_max_wait_seconds,
                request_timeout_seconds=self.mathpix_request_timeout_seconds,
            ),
        )

    def to_summary(self) -> dict[str, str]:
        """Return concise, non-secret review labels for the UI."""

        return {
            "input": str(self.pages),
            "output": str(self.output_directory),
            "pipeline": self.pipeline.value,
            "model": self._stage_models()[0],
            "quality": self.quality.value,
            "parse_rules": (
                "Human approval required"
                if self.requires_parse_rule_review
                else "Not used"
            ),
        }

    def _verification_stages(self) -> tuple[bool, bool]:
        if self.quality is QualityChoice.STANDARD:
            return False, False
        stage1_selected = self.pipeline in {
            PipelineChoice.COMPLETE,
            PipelineChoice.TRANSCRIPTION,
        }
        stage2_selected = self.pipeline in {
            PipelineChoice.COMPLETE,
            PipelineChoice.STRUCTURE,
        }
        if self.quality is QualityChoice.VERIFIED:
            return stage1_selected, stage2_selected
        return (
            self.verify_stage1 and stage1_selected,
            self.verify_stage2 and stage2_selected,
        )

    def _stage_models(self) -> tuple[str, str | None, str | None, str | None]:
        """Resolve legacy and provider-aware stage model fields."""

        legacy = _clean_optional(self.model)
        selected = {
            "stage1": self._resolve_model(
                self.stage1_model, self.stage1_custom_model, "Stage 1"
            ),
            "pass1": self._resolve_model(
                self.stage2_pass1_model,
                self.stage2_pass1_custom_model,
                "Stage 2 Pass 1",
            ),
            "pass2": self._resolve_model(
                self.stage2_pass2_model,
                self.stage2_pass2_custom_model,
                "Stage 2 Pass 2",
            ),
        }
        required = {
            PipelineChoice.COMPLETE: ("stage1", "pass1", "pass2"),
            PipelineChoice.TRANSCRIPTION: ("stage1",),
            PipelineChoice.STRUCTURE: ("pass1", "pass2"),
            PipelineChoice.DISCOVER_RULES: ("pass1",),
        }[self.pipeline]
        if legacy is None:
            missing = [name for name in required if selected[name] is None]
            if missing:
                raise ValueError(
                    "Select a model for each active pipeline stage: "
                    + ", ".join(missing)
                )
        default = legacy or next(
            selected[name] for name in required if selected[name] is not None
        )
        return default, selected["stage1"], selected["pass1"], selected["pass2"]

    def _resolve_model(
        self,
        selected: str | None,
        custom: str | None,
        label: str,
    ) -> str | None:
        selected = _clean_optional(selected)
        custom = _clean_optional(custom)
        if selected is None and custom is None:
            return None
        if selected == "__other__":
            if custom is None:
                raise ValueError(f"{label} requires a custom model name")
            selected = custom
        elif selected is None:
            selected = custom
        assert selected is not None
        provider = Provider(self.provider)
        if provider is not Provider.OPENROUTER and "/" in selected:
            return selected
        return normalize_custom_model(provider, selected)

    def _resolved_openrouter_provider(self) -> str | None:
        """Return automatic or pinned OpenRouter endpoint routing."""

        selected = _clean_optional(self.openrouter_provider)
        if self.provider != Provider.OPENROUTER.value:
            if selected not in {None, "auto"}:
                raise ValueError(
                    "openrouter_provider is only valid with the OpenRouter provider"
                )
            return None
        return selected or "auto"

    def _dictionary_profile(self) -> DictionaryProfile | None:
        """Build a profile only when the user answers the optional questionnaire."""

        has_any_answer = any(
            (
                _clean_optional(self.profile_headword_language),
                _clean_optional(self.profile_headword_script),
                self.profile_target_languages,
                self.profile_target_scripts,
                self.profile_page_layout,
                self.profile_information_types,
                _clean_optional(self.profile_other_information_types),
            )
        )
        if not has_any_answer:
            return None
        if not all(
            (
                _clean_optional(self.profile_headword_language),
                _clean_optional(self.profile_headword_script),
                self.profile_target_languages,
                self.profile_target_scripts,
                self.profile_page_layout,
                self.profile_information_types,
            )
        ):
            raise ValueError(
                "Complete all Dictionary Profile questions, or leave all of them blank"
            )
        if len(self.profile_target_languages) != len(self.profile_target_scripts):
            raise ValueError(
                "Each Dictionary Profile target language must have a matching script"
            )
        headword_language = _clean_optional(self.profile_headword_language)
        headword_script = _clean_optional(self.profile_headword_script)
        page_layout = self.profile_page_layout
        assert headword_language is not None
        assert headword_script is not None
        assert page_layout is not None
        targets = [
            ProfileLanguage(language=language, script=script)
            for language, script in zip(
                self.profile_target_languages,
                self.profile_target_scripts,
                strict=True,
            )
        ]
        return DictionaryProfile(
            headword=ProfileLanguage(
                language=headword_language,
                script=headword_script,
            ),
            targets=targets,
            page_layout=page_layout,
            information_types=self.profile_information_types,
            other_information_types=_clean_optional(
                self.profile_other_information_types
            ),
        )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _resolved_optional(value: Path | None) -> Path | None:
    return value.expanduser().resolve() if value is not None else None
