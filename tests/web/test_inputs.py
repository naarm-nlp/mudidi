"""Tests for upload materialization limits independent of HTTP framing."""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import os
import time
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from mudidi.web.inputs import InputMaterializer

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
