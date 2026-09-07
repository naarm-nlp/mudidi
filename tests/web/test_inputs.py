"""Tests for upload materialization limits independent of HTTP framing."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from io import BytesIO
import os
import time
from pathlib import Path

import fitz
import pytest
from starlette.datastructures import UploadFile

from mudidi.web.inputs import (
    InputMaterializer,
    read_managed_instruction_metadata,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _pdf_bytes(page_count: int) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_materializer_rejects_chunked_content_above_cumulative_limit(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path, max_total_bytes=5)
    upload = UploadFile(filename="page_1.png", file=BytesIO(b"123456"))

    with pytest.raises(ValueError, match="too large"):
        asyncio.run(materializer.materialize("run-limit", [upload]))

    assert not (tmp_path / "runs" / "run-limit" / "inputs").exists()


def test_materializer_rejects_windows_path_components(tmp_path: Path) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    upload = UploadFile(filename=r"folder\page_1.png", file=BytesIO(b"image"))

    with pytest.raises(ValueError, match="path components"):
        asyncio.run(materializer.materialize("run-path", [upload]))




def test_instruction_upload_writes_managed_source_and_metadata(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    content = b"Use the established marker order.\n"
    upload = UploadFile(filename="nested/guide.md", file=BytesIO(content))

    path = asyncio.run(
        materializer.materialize_instruction_upload(
            "run-1",
            "stage1",
            upload,
            page_spec=None,
            stage2_scope=None,
        )
    )

    stage_dir = tmp_path / "runs" / "run-1" / "inputs" / "instructions" / "stage1"
    assert path == stage_dir / "guide.md"
    assert path.read_bytes() == content
    metadata = json.loads((stage_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "source_mode": "file",
        "original_filename": "guide.md",
        "suffix": ".md",
        "kind": "text",
        "byte_count": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "pdf_page_count": None,
        "selected_pages": [],
        "stage2_scope": None,
    }


def test_typed_instruction_uses_same_stage_layout_and_sidecar(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)

    path = materializer.materialize_instruction(
        "run-typed",
        "stage2",
        "Apply this to both passes.",
    )

    assert path == (
        tmp_path / "runs" / "run-typed" / "inputs" / "instructions" / "stage2" / "stage2.txt"
    )
    metadata = read_managed_instruction_metadata(path)
    assert metadata is not None
    assert metadata["source_mode"] == "typed"
    assert metadata["original_filename"] == "stage2.txt"
    assert metadata["kind"] == "text"
    assert metadata["stage2_scope"] == "both"


def test_instruction_pdf_metadata_resolves_unique_selected_pages(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    upload = UploadFile(filename="instructions.pdf", file=BytesIO(_pdf_bytes(4)))

    path = asyncio.run(
        materializer.materialize_instruction_upload(
            "run-pdf",
            "stage2",
            upload,
            page_spec="2-3,2,4",
            stage2_scope="pass1",
        )
    )

    metadata = read_managed_instruction_metadata(path)
    assert metadata is not None
    assert metadata["kind"] == "pdf"
    assert metadata["pdf_page_count"] == 4
    assert metadata["selected_pages"] == [2, 3, 4]
    assert metadata["stage2_scope"] == "pass1"


def test_sidecar_free_legacy_instruction_returns_no_metadata(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-guide.docx"
    legacy.write_bytes(b"legacy")

    assert read_managed_instruction_metadata(legacy) is None


@pytest.mark.parametrize(
    ("filename", "content", "page_spec", "message"),
    [
        ("guide.txt", b"\xff", None, "UTF-8"),
        ("guide.txt", b"   \n", None, "blank"),
        ("guide.pdf", b"not a PDF", None, "signature"),
        ("guide.txt", b"instructions", "1", "PDF"),
    ],
)
def test_instruction_upload_validation_rejects_unsafe_content(
    tmp_path: Path,
    filename: str,
    content: bytes,
    page_spec: str | None,
    message: str,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    upload = UploadFile(filename=filename, file=BytesIO(content))

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            materializer.materialize_instruction_upload(
                "run-invalid-instruction",
                "stage1",
                upload,
                page_spec=page_spec,
                stage2_scope=None,
            )
        )
    assert not (
        tmp_path
        / "runs"
        / "run-invalid-instruction"
        / "inputs"
        / "instructions"
        / "stage1"
    ).exists()
def test_materializer_rejects_spoofed_image_content(tmp_path: Path) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    upload = UploadFile(filename="page_1.png", file=BytesIO(b"not an image"))

    with pytest.raises(ValueError, match="image is unreadable"):
        asyncio.run(materializer.materialize_pages("run-spoof", [upload]))


def test_materializer_rejects_duplicate_flattened_directory_names(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    uploads = [
        UploadFile(filename="a/page.png", file=BytesIO(_PNG)),
        UploadFile(filename="b/page.png", file=BytesIO(_PNG)),
    ]

    with pytest.raises(ValueError, match="unique after flattening"):
        asyncio.run(materializer.materialize_pages("run-duplicate", uploads))


def test_reconcile_removes_old_orphans_but_keeps_known_runs(tmp_path: Path) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    orphan = materializer.bundle("orphan-run")
    known = materializer.bundle("known-run")
    orphan.mkdir(parents=True)
    known.mkdir(parents=True)
    (orphan / "stale.part").write_bytes(b"partial")
    (known / "page.png").write_bytes(_PNG)
    old = time.time() - 7_200
    os.utime(orphan.parent, (old, old))

    removed = materializer.reconcile({"known-run"}, grace_seconds=3_600)

    assert removed == {"orphan-run"}
    assert not orphan.parent.exists()
    assert known.exists()
