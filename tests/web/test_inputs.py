"""Tests for upload materialization limits independent of HTTP framing."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from mudidi.web.inputs import InputMaterializer


def test_materializer_rejects_chunked_content_above_cumulative_limit(
    tmp_path: Path,
) -> None:
    materializer = InputMaterializer(data_dir=tmp_path, max_total_bytes=5)
    upload = UploadFile(filename="page_1.png", file=BytesIO(b"123456"))

    with pytest.raises(ValueError, match="too large"):
        asyncio.run(materializer.materialize("run-limit", [upload]))

    assert not (tmp_path / "uploads" / "run-limit").exists()


def test_materializer_rejects_windows_path_components(tmp_path: Path) -> None:
    materializer = InputMaterializer(data_dir=tmp_path)
    upload = UploadFile(filename=r"folder\page_1.png", file=BytesIO(b"image"))

    with pytest.raises(ValueError, match="path components"):
        asyncio.run(materializer.materialize("run-path", [upload]))
