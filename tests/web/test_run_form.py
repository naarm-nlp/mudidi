"""Tests for direct UI form to typed inference configuration mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.web.forms import NewRunForm, PipelineChoice, QualityChoice


def _form(tmp_path: Path, **overrides: object) -> NewRunForm:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    output = tmp_path / "output"
    values: dict[str, object] = {
        "pages": pages,
        "output_directory": output,
        "pipeline": PipelineChoice.COMPLETE,
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
        "reasoning": "low",
        "quality": QualityChoice.VERIFIED,
    }
    values.update(overrides)
    return NewRunForm.model_validate(values)


@pytest.mark.parametrize(
    ("choice", "stage"),
    [
        (PipelineChoice.COMPLETE, "all"),
        (PipelineChoice.TRANSCRIPTION, "1"),
        (PipelineChoice.STRUCTURE, "2"),
        (PipelineChoice.DISCOVER_RULES, "2-pass-1"),
    ],
)
def test_pipeline_choices_map_to_supported_safe_stages(
    tmp_path: Path,
    choice: PipelineChoice,
    stage: str,
) -> None:
    config = _form(tmp_path, pipeline=choice).to_inference_config()

    assert config.pipeline.stage == stage
    assert config.pipeline.stage != "2-pass-2"


def test_verified_quality_enables_selected_stage_verification(tmp_path: Path) -> None:
    config = _form(tmp_path, quality=QualityChoice.VERIFIED).to_inference_config()

    assert config.agentic.stage1 is True
    assert config.agentic.stage2 is True
    assert config.agentic.max_iterations == 2


def test_standard_quality_disables_agentic_verification(tmp_path: Path) -> None:
    config = _form(tmp_path, quality=QualityChoice.STANDARD).to_inference_config()

    assert config.agentic.stage1 is False
    assert config.agentic.stage2 is False


def test_custom_quality_maps_direct_controls(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        quality=QualityChoice.CUSTOM,
        verify_stage1=True,
        verify_stage2=False,
        max_iterations=4,
        min_retry_confidence=0.7,
        verifier_patches=False,
        require_concrete_retry=False,
    ).to_inference_config()

    assert config.agentic.stage1 is True
    assert config.agentic.stage2 is False
    assert config.agentic.max_iterations == 4
    assert config.agentic.min_retry_confidence == 0.7
    assert config.agentic.verifier_patches is False
    assert config.agentic.require_concrete_retry is False


def test_stage_specific_models_override_default(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        stage1_model="gemini/gemini-3.1-pro-preview",
        stage2_pass1_model="anthropic/claude-opus-4-6",
        stage2_pass2_model="openai/gpt-5.4",
    ).to_inference_config()

    assert config.models.default == "anthropic/claude-sonnet-4-6"
    assert config.models.stage1 == "gemini/gemini-3.1-pro-preview"
    assert config.models.stage2_pass1 == "anthropic/claude-opus-4-6"
    assert config.models.stage2_pass2 == "openai/gpt-5.4"


def test_form_rejects_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="provider"):
        _form(tmp_path, provider="mystery")


def test_pdf_input_requires_dictionary_page_range(tmp_path: Path) -> None:
    pdf = tmp_path / "dictionary.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with pytest.raises(ValueError, match="dictionary_pages"):
        _form(tmp_path, pages=pdf).to_inference_config()


def test_stage2_workflow_always_reports_review_checkpoint(tmp_path: Path) -> None:
    form = _form(tmp_path, pipeline=PipelineChoice.STRUCTURE)

    assert form.requires_parse_rule_review is True
    assert form.to_summary()["parse_rules"] == "Human approval required"
