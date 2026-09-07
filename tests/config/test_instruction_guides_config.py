from __future__ import annotations

import json
from pathlib import Path

import pytest
import pymupdf
from pydantic import ValidationError

from mudidi.config.yaml_config import (
    InferenceConfig,
    PipelineConfig,
    redacted_config_dict,
    validate_config_paths,
)


def _write_pdf(path: Path, page_count: int) -> None:
    document = pymupdf.open()
    try:
        for page_number in range(1, page_count + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"guide page {page_number}")
        document.save(str(path))
    finally:
        document.close()


def _inference_config(pipeline: PipelineConfig) -> InferenceConfig:
    values: dict[str, object] = {
        "kind": "inference",
        "pipeline": pipeline,
        "input": {"pages": Path(".")},
        "output": {"directory": Path("output")},
    }
    if pipeline.strategy == "vlm_ocr":
        values["vlm"] = {"model": "glm-ocr"}
    return InferenceConfig.model_validate(values)


def test_instruction_guide_fields_round_trip_and_redact() -> None:
    pipeline = PipelineConfig(
        stage1_guides=Path("stage1.pdf"),
        stage1_guides_pages="2-4,8",
        stage2_guides=Path("stage2.pdf"),
        stage2_guides_pages="1,3",
        stage2_guides_scope="pass1",
    )

    assert pipeline.stage1_guides_pages == "2-4,8"
    assert pipeline.stage2_guides_pages == "1,3"
    assert pipeline.stage2_guides_scope == "pass1"
    assert PipelineConfig().stage2_guides_scope == "both"

    config = _inference_config(pipeline)
    snapshot = redacted_config_dict(config)
    assert snapshot["pipeline"]["stage1_guides_pages"] == "2-4,8"
    assert snapshot["pipeline"]["stage2_guides_pages"] == "1,3"
    assert snapshot["pipeline"]["stage2_guides_scope"] == "pass1"
    json.dumps(snapshot)


@pytest.mark.parametrize(
    ("pipeline_kwargs", "field"),
    [
        ({"stage1_guides_pages": "1"}, "stage1_guides_pages"),
    ],
)
def test_instruction_page_specs_require_pdf_guide(
    pipeline_kwargs: dict[str, object], field: str
) -> None:
    pipeline = PipelineConfig(**pipeline_kwargs)
    with pytest.raises(ValueError, match=field):
        validate_config_paths(_inference_config(pipeline))


@pytest.mark.parametrize("suffix", [".txt", ".md", ".docx"])
def test_instruction_page_specs_reject_text_guide_types(
    tmp_path: Path, suffix: str
) -> None:
    guide = tmp_path / f"guide{suffix}"
    guide.write_text("guide text", encoding="utf-8")
    pipeline = PipelineConfig(stage1_guides=guide, stage1_guides_pages="1")

    with pytest.raises(ValueError, match="stage1_guides_pages"):
        validate_config_paths(_inference_config(pipeline))


def test_instruction_page_spec_rejects_descending_range() -> None:
    with pytest.raises(ValidationError, match="stage1_guides_pages"):
        PipelineConfig(stage1_guides_pages="4-2")


def test_instruction_pdf_pages_are_in_bounds_and_deduplicated(tmp_path: Path) -> None:
    guide = tmp_path / "guide.pdf"
    _write_pdf(guide, 4)
    pipeline = PipelineConfig(stage1_guides=guide, stage1_guides_pages="2-3,2,4")

    validate_config_paths(_inference_config(pipeline))
    assert pipeline.stage1_guides_pages == "2-3,4"

    out_of_bounds = PipelineConfig(stage1_guides=guide, stage1_guides_pages="1,5")
    with pytest.raises(ValueError, match="stage1_guides_pages"):
        validate_config_paths(_inference_config(out_of_bounds))


def test_instruction_pdf_guides_are_not_silently_discarded_by_ocr_backends(
    tmp_path: Path,
) -> None:
    guide = tmp_path / "guide.pdf"
    _write_pdf(guide, 1)

    for strategy in ("vlm_ocr", "mathpix_ocr"):
        pipeline = PipelineConfig(strategy=strategy, stage="1", stage1_guides=guide)
        with pytest.raises(ValueError, match="stage1_guides"):
            validate_config_paths(_inference_config(pipeline))


def test_text_only_guide_configuration_remains_compatible(tmp_path: Path) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("guide text", encoding="utf-8")
    pipeline = PipelineConfig(stage1_guides=guide)

    validate_config_paths(_inference_config(pipeline))
