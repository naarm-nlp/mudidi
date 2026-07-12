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


def test_advanced_form_controls_map_without_yaml(tmp_path: Path) -> None:
    stage1_guides = tmp_path / "stage1-guides.txt"
    stage2_guides = tmp_path / "stage2-guides.txt"
    stage1_guides.write_text("stage 1", encoding="utf-8")
    stage2_guides.write_text("stage 2", encoding="utf-8")

    config = _form(
        tmp_path,
        stage1_mode="column",
        stage1_guides=stage1_guides,
        stage2_guides=stage2_guides,
        reasoning="high",
        evaluator_reasoning="medium",
        rewriter_reasoning="high",
        temperature=0.25,
        quality="custom",
        verify_stage1=True,
        verify_stage2=True,
        evaluator_model="openai/gpt-5.6",
        rewriter_model="anthropic/claude-opus-4-8",
        batch_size=3,
        page_limit=12,
        prompt_cache="off",
        media_reference="inline",
    ).to_inference_config()

    assert config.pipeline.stage1_mode == "column"
    assert config.pipeline.stage1_typography is False
    assert config.pipeline.stage1_guides == stage1_guides.resolve()
    assert config.pipeline.stage2_guides == stage2_guides.resolve()
    assert config.models.temperature == 0.25
    assert config.agentic.evaluator_reasoning == "medium"
    assert config.agentic.rewriter_reasoning == "high"
    assert config.runtime.batch_size == 3
    assert config.runtime.limit == 12
    assert config.runtime.prompt_cache == "off"
    assert config.runtime.media_reference == "inline"


def test_optional_dictionary_profile_maps_five_dashboard_answers(tmp_path: Path) -> None:
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


def test_expert_vlm_backend_controls_map_without_yaml(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        pipeline="transcription",
        strategy="vlm_ocr",
        vlm_model="paddleocr-vl-1.5",
        vlm_dpi=250,
        paddle_rec_backend="vllm-server",
        paddle_server_url="http://127.0.0.1:9000",
        paddle_auto_server=False,
        paddle_server_port=9000,
    ).to_inference_config()

    assert config.pipeline.strategy == "vlm_ocr"
    assert config.pipeline.stage == "1"
    assert config.vlm.model == "paddleocr-vl-1.5"
    assert config.vlm.dpi == 250
    assert config.vlm.paddle_rec_backend == "vllm-server"
    assert config.vlm.paddle_server_url == "http://127.0.0.1:9000"
    assert config.vlm.paddle_auto_server is False
    assert config.vlm.paddle_server_port == 9000


def test_expert_backend_rejects_non_stage1_pipeline(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="vlm_ocr requires pipeline.stage"):
        _form(
            tmp_path,
            pipeline="complete",
            strategy="vlm_ocr",
            vlm_model="mineru2.5-pro",
        ).to_inference_config()


def test_new_output_policy_rejects_nonempty_directory(tmp_path: Path) -> None:
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "previous.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(ValueError, match="already contains files"):
        _form(
            tmp_path, output_directory=output, output_policy="new"
        ).to_inference_config()


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
    assert previous.read_text(encoding="utf-8") == "resume me"
