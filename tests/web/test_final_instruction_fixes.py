from __future__ import annotations

import asyncio
import re
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from mudidi.web.app import create_app
from mudidi.web.inputs import (
    InputMaterializer,
    InstructionMaterializationError,
    read_managed_instruction_metadata,
)


def _pdf_bytes(page_count: int = 3) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def _run_id(html: str) -> str:
    match = re.search(r'action="/runs/([^/]+)/start"', html)
    assert match is not None
    return match.group(1)


def test_refresh_kept_pdf_revalidates_pages_and_atomically_updates_sidecar(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    original = _pdf_bytes()
    path = asyncio.run(
        materializer.materialize_instruction_upload(
            "run-refresh",
            "stage2",
            UploadFile(filename="reference.pdf", file=BytesIO(original)),
            page_spec="2",
            stage2_scope="pass2",
        )
    )

    refreshed = materializer.refresh_managed_instruction(
        "run-refresh",
        "stage2",
        path,
        page_spec="1,3",
        stage2_scope="pass1",
    )

    assert refreshed == path
    assert path.read_bytes() == original
    metadata = read_managed_instruction_metadata(path)
    assert metadata is not None
    assert metadata["pdf_page_count"] == 3
    assert metadata["selected_pages"] == [1, 3]
    assert metadata["stage2_scope"] == "pass1"

    with pytest.raises(InstructionMaterializationError, match="outside"):
        materializer.refresh_managed_instruction(
            "run-refresh",
            "stage2",
            path,
            page_spec="99",
            stage2_scope="pass1",
        )
    assert read_managed_instruction_metadata(path) == metadata


def test_kept_preset_pdf_page_and_scope_edits_persist_into_resaved_preset(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    original_pdf = _pdf_bytes()
    uploaded = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "source-output"),
            "pipeline": "structure",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage2_instruction_source": "file",
            "stage2_instruction_pdf_pages": "2",
            "stage2_instruction_scope": "pass2",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(1), "application/pdf"),
            "stage2_instruction_file": (
                "reference.pdf",
                original_pdf,
                "application/pdf",
            ),
        },
    )
    assert uploaded.status_code == 200
    source_run_id = _run_id(uploaded.text)
    saved = client.post(
        f"/runs/{source_run_id}/presets",
        data={"name": "Original selected PDF"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    source_preset = app.state.run_store.list_presets()[0]

    kept = client.post(
        "/runs/preview",
        data={
            "preset_id": source_preset.preset_id,
            "output_directory": str(tmp_path / "edited-output"),
            "pipeline": "structure",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage2_instruction_source": "file",
            "stage2_instruction_pdf_pages": "1,3",
            "stage2_instruction_keep_existing": "true",
            "stage2_instruction_scope": "pass1",
        },
    )
    assert kept.status_code == 200
    edited_run_id = _run_id(kept.text)
    edited_config = app.state.job_controller.load_inference_config(edited_run_id)
    assert edited_config.pipeline.stage2_guides_pages == "1,3"
    assert edited_config.pipeline.stage2_guides_scope == "pass1"
    edited_guide = edited_config.pipeline.stage2_guides
    assert edited_guide is not None
    assert edited_guide.read_bytes() == original_pdf
    edited_metadata = read_managed_instruction_metadata(edited_guide)
    assert edited_metadata is not None
    assert edited_metadata["selected_pages"] == [1, 3]
    assert edited_metadata["stage2_scope"] == "pass1"

    resaved = client.post(
        f"/runs/{edited_run_id}/presets",
        data={"name": "Edited selected PDF"},
        follow_redirects=False,
    )
    assert resaved.status_code == 303
    edited_preset = next(
        preset
        for preset in app.state.run_store.list_presets()
        if preset.name == "Edited selected PDF"
    )
    assert edited_preset.config.pipeline.stage2_guides_pages == "1,3"
    assert edited_preset.config.pipeline.stage2_guides_scope == "pass1"
    preset_guide = (
        app.state.inputs.presets_root
        / edited_preset.preset_id
        / "inputs"
        / "instructions"
        / "stage2"
        / "reference.pdf"
    )
    preset_metadata = read_managed_instruction_metadata(preset_guide)
    assert preset_metadata is not None
    assert preset_metadata["selected_pages"] == [1, 3]
    assert preset_metadata["stage2_scope"] == "pass1"


def test_review_hides_typed_internal_filename_and_shows_selected_pdf_count(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_additional_instructions": "Typed instructions stay secret.",
            "stage2_instruction_source": "file",
            "stage2_instruction_pdf_pages": "2",
            "stage2_instruction_scope": "pass2",
        },
        files=[
            ("dictionary_pdf", ("dictionary.pdf", _pdf_bytes(1), "application/pdf")),
            (
                "stage2_instruction_file",
                ("visible.pdf", _pdf_bytes(), "application/pdf"),
            ),
        ],
    )
    assert response.status_code == 200
    run_id = _run_id(response.text)
    recovered = client.get(f"/runs/{run_id}/review")
    assert recovered.status_code == 200

    for html in (response.text, recovered.text):
        assert "Typed instructions stay secret." not in html
        assert "Original filename: stage1.txt" not in html
        assert "Original filename: visible.pdf" in html
        assert "Selected PDF page count: 1" in html
        assert "PDF page count: 3" in html
