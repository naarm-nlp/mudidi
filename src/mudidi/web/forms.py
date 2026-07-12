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
    ModelsConfig,
    OutputConfig,
    PipelineConfig,
    RuntimeConfig,
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
    dictionary_pages: str | None = None
    introduction: Path | None = None
    introduction_pages: str | None = None
    alphabet: Path | None = None
    ocr_text: Path | None = None
    dictionary_languages: Path | None = None
    toolbox_pdf: Path | None = None

    pipeline: PipelineChoice = PipelineChoice.COMPLETE
    stage1_mode: Literal["flat", "column"] = "flat"
    stage1_typography: bool = False
    parse_rules_pages: list[str] = Field(default_factory=list)
    parse_rules_file: Path | None = None

    provider: ProviderName
    model: str = Field(min_length=1)
    reasoning: ReasoningChoice = "low"
    stage1_model: str | None = None
    stage2_pass1_model: str | None = None
    stage2_pass2_model: str | None = None

    quality: QualityChoice = QualityChoice.VERIFIED
    verify_stage1: bool = False
    verify_stage2: bool = False
    max_iterations: int = Field(default=2, ge=0, le=10)
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    verifier_patches: bool = True
    require_concrete_retry: bool = True
    evaluator_model: str | None = None
    rewriter_model: str | None = None

    batch_size: int = Field(default=1, ge=1, le=32)
    page_limit: int | None = Field(default=None, ge=1)
    prompt_cache: Literal["auto", "off"] = "auto"

    @computed_field
    @property
    def requires_parse_rule_review(self) -> bool:
        """Return whether this workflow stops at the human checkpoint."""

        return self.pipeline is not PipelineChoice.TRANSCRIPTION

    def to_inference_config(self) -> InferenceConfig:
        """Build the existing authoritative typed production configuration."""

        if self.pages.suffix.lower() == ".pdf" and not self.dictionary_pages:
            raise ValueError("dictionary_pages is required for PDF input")

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
            output=OutputConfig(directory=self.output_directory.expanduser().resolve()),
            pipeline=PipelineConfig(
                stage=stage,
                stage1_mode=self.stage1_mode,
                stage1_typography=self.stage1_typography,
                parse_rules_pages=self.parse_rules_pages,
                parse_rules_file=(
                    self.parse_rules_file.expanduser().resolve()
                    if self.parse_rules_file
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
            ),
            agentic=AgenticConfig(
                stage1=verify_stage1,
                stage2=verify_stage2,
                max_iterations=self.max_iterations,
                evaluator_model=_clean_optional(self.evaluator_model),
                rewriter_model=_clean_optional(self.rewriter_model),
                reasoning=self.reasoning,
                min_retry_confidence=self.min_retry_confidence,
                verifier_patches=self.verifier_patches,
                require_concrete_retry=self.require_concrete_retry,
            ),
            runtime=RuntimeConfig(
                batch_size=self.batch_size,
                limit=self.page_limit,
                prompt_cache=self.prompt_cache,
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
