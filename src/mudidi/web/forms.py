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
    dictionary_languages: Path | None = None
    toolbox_pdf: Path | None = None

    pipeline: PipelineChoice = PipelineChoice.COMPLETE
    stage1_mode: Literal["flat", "column"] = "flat"
    strategy: Literal["two_stage", "vlm_ocr", "mathpix_ocr"] = "two_stage"
    stage1_typography: bool = False
    stage1_guides: Path | None = None
    stage2_guides: Path | None = None
    parse_rules_pages: list[str] = Field(default_factory=list)
    parse_rules_file: Path | None = None

    provider: ProviderName
    model: str = Field(min_length=1)
    reasoning: ReasoningChoice = "low"
    stage1_model: str | None = None
    stage2_pass1_model: str | None = None
    stage2_pass2_model: str | None = None
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
                dictionary_languages=(
                    self.dictionary_languages.expanduser().resolve()
                    if self.dictionary_languages
                    else None
                ),
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
                stage1_typography=self.stage1_typography,
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
                default=self.model.strip(),
                stage1=_clean_optional(self.stage1_model),
                stage2_pass1=_clean_optional(self.stage2_pass1_model),
                stage2_pass2=_clean_optional(self.stage2_pass2_model),
                stage1_reasoning=self.reasoning,
                stage2_reasoning=(
                    self.reasoning if self.reasoning != "none" else "low"
                ),
                temperature=self.temperature,
            ),
            agentic=AgenticConfig(
                stage1=verify_stage1,
                stage2=verify_stage2,
                max_iterations=self.max_iterations,
                evaluator_model=_clean_optional(self.evaluator_model),
                rewriter_model=_clean_optional(self.rewriter_model),
                reasoning=self.reasoning,
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
            "model": self.model,
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


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _resolved_optional(value: Path | None) -> Path | None:
    return value.expanduser().resolve() if value is not None else None
