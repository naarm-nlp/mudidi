"""Contract tests for the simplified local production dashboard."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mudidi.config.yaml_config import PipelineConfig
from mudidi.web.app import create_app
from mudidi.web.forms import NewRunForm, PipelineChoice


def _form(tmp_path: Path, **overrides: object) -> NewRunForm:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "pages": pages,
        "output_directory": tmp_path / "output",
        "pipeline": "complete",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-5",
        "reasoning": "low",
    }
    values.update(overrides)
    return NewRunForm.model_validate(values)


def test_dashboard_pipeline_contract_has_exactly_three_choices() -> None:
    assert {choice.value for choice in PipelineChoice} == {
        "complete",
        "transcription",
        "structure",
    }


@pytest.mark.parametrize(
    ("choice", "stage"),
    [
        ("complete", "all"),
        ("transcription", "1"),
        ("structure", "2"),
    ],
)
def test_dashboard_pipeline_is_flat_two_stage_without_ocr_hint(
    tmp_path: Path,
    choice: str,
    stage: str,
) -> None:
    config = _form(tmp_path, pipeline=choice).to_inference_config()

    assert config.pipeline.stage == stage
    assert config.pipeline.strategy == "two_stage"
    assert config.pipeline.stage1_mode == "flat"
    assert config.pipeline.stage1_typography is False
    assert config.input.ocr_text is None
    assert config.vlm.model is None


@pytest.mark.parametrize(
    "removed",
    [
        {"quality": "verified"},
        {"stage1_mode": "column"},
        {"strategy": "vlm_ocr"},
        {"ocr_text": "/tmp/ocr.txt"},
        {"vlm_model": "mineru2.5-pro"},
        {"mathpix_max_wait_seconds": 10},
    ],
)
def test_dashboard_rejects_removed_specialist_fields(
    tmp_path: Path,
    removed: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match=next(iter(removed))):
        _form(tmp_path, **removed)


def test_agentic_is_off_by_default(tmp_path: Path) -> None:
    config = _form(tmp_path).to_inference_config()

    assert config.agentic.stage1 is False
    assert config.agentic.stage2 is False
    assert _form(tmp_path).to_summary()["agentic"] == "Off"


@pytest.mark.parametrize(
    ("pipeline", "verify_stage1", "verify_stage2", "expected"),
    [
        ("complete", True, True, (True, True)),
        ("complete", False, True, (False, True)),
        ("transcription", True, True, (True, False)),
        ("structure", True, True, (False, True)),
    ],
)
def test_agentic_intersects_user_checks_with_active_pipeline(
    tmp_path: Path,
    pipeline: str,
    verify_stage1: bool,
    verify_stage2: bool,
    expected: tuple[bool, bool],
) -> None:
    config = _form(
        tmp_path,
        pipeline=pipeline,
        agentic=True,
        verify_stage1=verify_stage1,
        verify_stage2=verify_stage2,
    ).to_inference_config()

    assert (config.agentic.stage1, config.agentic.stage2) == expected


def test_agentic_false_ignores_forged_verification_values(tmp_path: Path) -> None:
    config = _form(
        tmp_path,
        agentic=False,
        verify_stage1=True,
        verify_stage2=True,
    ).to_inference_config()

    assert config.agentic.stage1 is False
    assert config.agentic.stage2 is False


def test_shared_yaml_pipeline_still_supports_advanced_cli_values() -> None:
    pipeline = PipelineConfig(
        stage="1",
        strategy="vlm_ocr",
        stage1_mode="flat",
        stage1_guides=Path("stage1.txt"),
    )

    assert pipeline.strategy == "vlm_ocr"
    assert pipeline.stage1_guides == Path("stage1.txt")


def test_home_uses_accessible_pipeline_radios_and_agentic_controls(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert response.text.count('type="radio" name="pipeline"') == 3
    assert "Parse transcription into MDF (Multi-Dictionary Formatter)" in response.text
    assert "Discover and review parse rules" not in response.text
    assert 'name="quality"' not in response.text
    assert 'name="agentic"' in response.text
    assert "Custom verification" in response.text
    assert 'name="verify_stage1"' in response.text
    assert 'name="verify_stage2"' in response.text
    assert 'name="verify_stage1" type="checkbox" value="true" checked' in response.text
    assert 'name="verify_stage2" type="checkbox" value="true" checked' in response.text
    assert 'name="strategy"' not in response.text
    assert 'name="stage1_mode"' not in response.text
    assert 'name="ocr_text"' not in response.text
    assert "Expert OCR backends" not in response.text


def test_bundled_mdf_manual_is_downloadable(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/assets/mdf-manual")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert len(response.content) == 530_169
    assert (
        hashlib.sha256(response.content).hexdigest()
        == "6c654140ab6a9914baf1f6384750b0b10e7408c72bf16df1242f9ff4bb7cd015"
    )
