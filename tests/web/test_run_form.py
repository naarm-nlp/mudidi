"""Tests for direct UI form to typed inference configuration mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.web.forms import NewRunForm, PipelineChoice


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
    }
    values.update(overrides)
    return NewRunForm.model_validate(values)


@pytest.mark.parametrize(
    ("choice", "stage"),
    [
        (PipelineChoice.COMPLETE, "all"),
        (PipelineChoice.TRANSCRIPTION, "1"),
        (PipelineChoice.STRUCTURE, "2"),
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


def test_agentic_maps_direct_controls(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        agentic=True,
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


def test_stage_models_accept_provider_specific_other_values(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        pipeline="transcription",
        provider="anthropic",
        model=None,
        stage1_model="__other__",
        stage1_custom_model="claude-private-vision",
    ).to_inference_config()

    assert config.models.default == "anthropic/claude-private-vision"
    assert config.models.stage1 == "anthropic/claude-private-vision"
    assert config.models.stage2_pass1 is None
    assert config.models.stage2_pass2 is None


def test_openrouter_endpoint_provider_is_typed_and_optional(tmp_path: Path) -> None:
    automatic = _form(
        tmp_path,
        provider="openrouter",
        model="openrouter/anthropic/claude-sonnet-5",
    ).to_inference_config()
    pinned = _form(
        tmp_path,
        provider="openrouter",
        model="openrouter/anthropic/claude-sonnet-5",
        openrouter_provider="anthropic",
    ).to_inference_config()

    assert automatic.models.openrouter_provider == "auto"
    assert pinned.models.openrouter_provider == "anthropic"


def test_openrouter_manual_stage_models_receive_litellm_prefix(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        pipeline="transcription",
        provider="openrouter",
        model=None,
        stage1_model="__other__",
        stage1_custom_model="qwen/qwen3-235b-a22b",
    ).to_inference_config()

    assert config.models.default == "openrouter/qwen/qwen3-235b-a22b"
    assert config.models.stage1 == "openrouter/qwen/qwen3-235b-a22b"


def test_none_reasoning_uses_lowest_supported_dashboard_level(tmp_path: Path) -> None:
    config = _form(tmp_path, reasoning="none").to_inference_config()

    assert config.models.stage1_reasoning == "low"
    assert config.models.stage2_reasoning == "low"
    assert config.agentic.reasoning == "low"


def test_each_pipeline_model_has_independent_reasoning_control(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        stage1_reasoning="none",
        stage2_pass1_reasoning="medium",
        stage2_pass2_reasoning="high",
    ).to_inference_config()

    assert config.models.stage1_reasoning == "low"
    assert config.models.stage2_pass1_reasoning == "medium"
    assert config.models.stage2_pass2_reasoning == "high"


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
    assert form.to_summary()["mdf_parsing_guide"] == "Human approval required"


def test_user_supplied_mdf_guide_skips_review_checkpoint(tmp_path: Path) -> None:
    guide = tmp_path / "field-cheatsheet.json"
    guide.write_text(
        '{"markers":[{"marker":"lx","description":"Headword"}]}',
        encoding="utf-8",
    )

    form = _form(
        tmp_path,
        pipeline=PipelineChoice.STRUCTURE,
        parse_rules_file=guide,
    )

    assert form.requires_parse_rule_review is False
    assert form.to_summary()["mdf_parsing_guide"] == "Uploaded guide used directly"


def test_advanced_form_controls_map_without_yaml(tmp_path: Path) -> None:
    stage1_guides = tmp_path / "stage1-guides.txt"
    stage2_guides = tmp_path / "stage2-guides.txt"
    stage1_guides.write_text("stage 1", encoding="utf-8")
    stage2_guides.write_text("stage 2", encoding="utf-8")

    config = _form(
        tmp_path,
        stage1_guides=stage1_guides,
        stage2_guides=stage2_guides,
        reasoning="high",
        evaluator_reasoning="medium",
        rewriter_reasoning="high",
        temperature=0.25,
        agentic=True,
        verify_stage1=True,
        verify_stage2=True,
        evaluator_model="openai/gpt-5.6",
        rewriter_model="anthropic/claude-opus-4-8",
        batch_size=3,
    ).to_inference_config()

    assert config.pipeline.stage1_mode == "flat"
    assert config.pipeline.stage1_typography is False
    assert config.pipeline.stage1_guides == stage1_guides.resolve()
    assert config.pipeline.stage2_guides == stage2_guides.resolve()
    assert config.models.temperature == 0.25
    assert config.agentic.evaluator_reasoning == "medium"
    assert config.agentic.rewriter_reasoning == "high"
    assert config.runtime.batch_size == 3
    assert config.runtime.limit is None
    assert config.runtime.prompt_cache == "auto"
    assert config.runtime.media_reference == "auto"


def test_optional_dictionary_profile_maps_five_dashboard_answers(
    tmp_path: Path,
) -> None:
    config = _form(
        tmp_path,
        profile_headword_language="Na",
        profile_headword_script="Latin and IPA",
        profile_target_languages=["English", "Chinese"],
        profile_target_scripts=["Latin", "Han"],
        profile_page_layout="There are two columns; each contains separate entries.",
        profile_information_types=["translation", "pronunciation", "other"],
        profile_other_information_types="dialect labels, semantic domains",
    ).to_inference_config()

    assert config.input.dictionary_profile is not None
    assert config.input.dictionary_profile.headword.language == "Na"
    assert [item.language for item in config.input.dictionary_profile.targets] == [
        "English",
        "Chinese",
    ]
    assert config.input.dictionary_profile.information_types == [
        "translation",
        "pronunciation",
        "other",
    ]
    assert (
        config.input.dictionary_profile.other_information_types
        == "dialect labels, semantic domains"
    )
    assert config.pipeline.stage1_typography is False


def test_dictionary_profile_is_optional_and_typography_is_not_a_web_field(
    tmp_path: Path,
) -> None:
    config = _form(tmp_path).to_inference_config()
    assert config.input.dictionary_profile is None
    assert config.pipeline.stage1_typography is False

    with pytest.raises(ValidationError, match="stage1_typography"):
        _form(tmp_path, stage1_typography=True)


def test_overwrite_output_policy_marks_existing_artifacts_for_replacement(
    tmp_path: Path,
) -> None:
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "previous.txt").write_text("do not overwrite", encoding="utf-8")

    config = _form(
        tmp_path, output_directory=output, output_policy="overwrite"
    ).to_inference_config()

    assert config.runtime.overwrite is True


def test_resume_output_policy_preserves_existing_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "existing-output"
    output.mkdir()
    previous = output / "previous.txt"
    previous.write_text("resume me", encoding="utf-8")

    config = _form(
        tmp_path,
        output_directory=output,
        output_policy="resume",
    ).to_inference_config()

    assert config.output.directory == output.resolve()
    assert config.runtime.overwrite is False
    assert previous.read_text(encoding="utf-8") == "resume me"


def test_resume_is_the_safe_default_for_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "existing-output"
    output.mkdir()
    previous = output / "previous.txt"
    previous.write_text("resume me", encoding="utf-8")

    config = _form(tmp_path, output_directory=output).to_inference_config()

    assert config.runtime.overwrite is False
    assert previous.read_text(encoding="utf-8") == "resume me"
