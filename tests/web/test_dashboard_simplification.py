"""Contract tests for the simplified local production dashboard."""

from __future__ import annotations

import base64
from importlib.resources import files
from pathlib import Path

import pytest
import fitz
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mudidi.config.yaml_config import PipelineConfig
from mudidi.web.app import create_app
from mudidi.web.forms import NewRunForm, PipelineChoice


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _one_page_pdf() -> bytes:
    document = fitz.open()
    document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


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


def test_dashboard_does_not_redistribute_an_mdf_manual(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/assets/mdf-manual")

    assert response.status_code == 404
    assert not files("mudidi.assets").joinpath("MDFReferenceManual.pdf").is_file()
    assert not (
        Path(__file__).parents[2] / "assets" / "Pages from ToolboxReferenceManual.pdf"
    ).is_file()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dictionary_pages", "i-xii"),
        ("introduction_pages", "0,2"),
        ("dictionary_pages", "5-2"),
        ("introduction_pages", "1,,3"),
    ],
)
def test_dashboard_page_specs_accept_only_positive_arabic_ranges(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    pdf = tmp_path / "dictionary.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    values = {field: value, "dictionary_pages": "1-10"}
    values[field] = value

    with pytest.raises(ValidationError, match=field):
        _form(tmp_path, pages=pdf, **values)


def test_home_uses_uploads_textareas_and_mdf_manual_choices(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert 'id="pages" name="pages"' not in response.text
    assert 'name="page_files" type="file"' in response.text
    assert 'name="page_directory" type="file"' in response.text
    assert "webkitdirectory" in response.text
    assert 'name="introduction_file" type="file"' in response.text
    assert 'name="alphabet_file" type="file"' in response.text
    assert 'name="existing_mdf_guide_file" type="file"' in response.text
    assert 'name="stage1_additional_instructions"' in response.text
    assert 'name="stage2_additional_instructions"' in response.text
    assert 'name="stage1_guides"' not in response.text
    assert 'name="stage2_guides"' not in response.text
    assert "Representative MDF parsing guide pages" in response.text
    assert 'name="mdf_manual_source" value="none"' in response.text
    assert 'name="mdf_manual_source" value="upload"' in response.text
    assert 'name="mdf_manual_source" value="bundled"' not in response.text
    assert 'name="custom_mdf_manual" type="file"' in response.text
    assert "Upload my own MDF manual" in response.text
    assert "Continue without an MDF manual" in response.text
    assert "Open or download the official SIL MDF manual" in response.text
    assert (
        'href="http://www.fieldlinguiststoolbox.org/ToolboxReferenceManual.pdf"'
        in response.text
    )
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer"' in response.text
    assert 'class="primary link-button mdf-manual-link"' in response.text
    assert "fieldlinguiststoolbox.org/ToolboxReferenceManual.pdf" in response.text
    assert "pages 31–95" in response.text
    assert "65 pages" in response.text
    assert "only the pages that describe the MDF markers or tags relevant" in response.text
    assert "run Complete digitization first without an MDF manual" in response.text
    assert "human checkpoint" in response.text
    assert "MDF parsing guide inferred by the LLM" in response.text


def test_removed_bundled_manual_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="mdf_manual_source"):
        _form(tmp_path, mdf_manual_source="bundled")


def test_preview_materializes_all_context_inputs_into_run_bundle(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)
    guide = b'{"dictionary_name":"Test","markers":[],"rules":[],"abbreviations":{}}'

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_additional_instructions": "Keep uncertain letters marked.",
            "stage2_additional_instructions": "Use the custom nt marker.",
            "mdf_manual_source": "upload",
            "parse_rules_pages": "1,3-4",
        },
        files=[
            ("page_files", ("page_1.png", _PNG, "image/png")),
            ("introduction_file", ("intro.md", b"# Introduction", "text/markdown")),
            ("alphabet_file", ("alphabet.txt", b"a b c", "text/plain")),
            ("existing_mdf_guide_file", ("guide.json", guide, "application/json")),
            ("custom_mdf_manual", ("manual.pdf", _one_page_pdf(), "application/pdf")),
        ],
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    bundle = (tmp_path / "app-data" / "runs" / run.run_id / "inputs").resolve()
    for path in (
        config.input.pages,
        config.input.introduction,
        config.input.alphabet,
        config.input.toolbox_pdf,
        config.pipeline.parse_rules_file,
        config.pipeline.stage1_guides,
        config.pipeline.stage2_guides,
    ):
        assert path is not None
        assert path.resolve().is_relative_to(bundle)
    assert config.pipeline.stage1_guides.read_text(encoding="utf-8") == (
        "Keep uncertain letters marked."
    )
    assert config.pipeline.stage2_guides.read_text(encoding="utf-8") == (
        "Use the custom nt marker."
    )
