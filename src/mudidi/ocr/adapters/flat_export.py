"""Write ``*_stage1_flat.txt`` from OCR page directories."""

from __future__ import annotations

import csv
import logging
from enum import Enum
from pathlib import Path

from mudidi.evaluation.stage1.flatten import (
    _metadata_lines,
    flat_output_path_for_pred,
    flatten_stage1_body_rows,
    language_from_sample_path,
    write_flat_text,
)
from mudidi.ocr.adapters.glm import glm_transcript_from_page_dir
from mudidi.ocr.adapters.markdown_to_flat import (
    find_markdown_source,
    markdown_transcript_from_page_dir,
)
from mudidi.ocr.adapters.mathpix_flat import (
    mathpix_docx_path,
    mathpix_lines_path,
    mathpix_transcript_from_page_dir,
)
from mudidi.ocr.adapters.layout_to_transcript_v1 import (
    FlatTranscriptParts,
    layout_to_transcript_v1,
)
from mudidi.ocr.adapters.mineru import mineru_blocks_from_page_dir
from mudidi.ocr.adapters.paddle import paddle_blocks_from_page_dir

logger = logging.getLogger(__name__)


class OcrBackend(str, Enum):
    MARKDOWN = "markdown"
    MINERU = "mineru"
    PADDLE = "paddle"
    GLM = "glm"
    MATHPIX = "mathpix"
    COLUMN_TSV = "column_tsv"


def detect_backend(page_dir: Path, *, stem: str) -> OcrBackend | None:
    """Detect OCR backend artifacts under *page_dir*."""
    if find_markdown_source(page_dir, stem=stem) is not None:
        return OcrBackend.MARKDOWN
    if (page_dir / "content.json").is_file() or any(
        (c / "content.json").is_file() for c in page_dir.iterdir() if c.is_dir()
    ):
        return OcrBackend.MINERU
    if list(page_dir.glob("*_res.json")) or any(
        list(c.glob("*_res.json")) for c in page_dir.iterdir() if c.is_dir()
    ):
        return OcrBackend.PADDLE
    if mathpix_lines_path(page_dir).is_file() or mathpix_docx_path(page_dir).is_file():
        return OcrBackend.MATHPIX
    if (page_dir / "result.json").is_file() or (page_dir / "output.txt").is_file():
        return OcrBackend.GLM
    tsv = page_dir / f"{stem}_stage1.tsv"
    if tsv.is_file():
        return OcrBackend.COLUMN_TSV
    return None


def page_dir_to_flat_parts(page_dir: Path, *, stem: str) -> FlatTranscriptParts:
    """Build flat transcript parts from a page output directory."""
    md_source = find_markdown_source(page_dir, stem=stem)
    if md_source is not None:
        logger.debug("Using markdown flat from %s", md_source.name)
        return markdown_transcript_from_page_dir(page_dir, stem=stem)

    backend = detect_backend(page_dir, stem=stem)
    if backend is None:
        raise FileNotFoundError(f"No recognized OCR artifacts in {page_dir}")

    if backend is OcrBackend.MINERU:
        blocks = mineru_blocks_from_page_dir(page_dir)
        return layout_to_transcript_v1(blocks)
    if backend is OcrBackend.PADDLE:
        blocks = paddle_blocks_from_page_dir(page_dir, stem=stem)
        return layout_to_transcript_v1(blocks)
    if backend is OcrBackend.GLM:
        return glm_transcript_from_page_dir(page_dir)
    if backend is OcrBackend.MATHPIX:
        return mathpix_transcript_from_page_dir(page_dir)
    if backend is OcrBackend.COLUMN_TSV:
        tsv_path = page_dir / f"{stem}_stage1.tsv"
        with tsv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        return FlatTranscriptParts(
            header=_metadata_lines(rows, "header"),
            body=flatten_stage1_body_rows(
                rows, language=language_from_sample_path(page_dir)
            ),
            footer=_metadata_lines(rows, "footer"),
        )
    raise ValueError(f"Unsupported backend {backend}")


def write_stage1_flat_for_page(page_dir: Path, *, stem: str) -> Path:
    """Write ``{stem}_stage1_flat.txt`` beside OCR artifacts."""
    parts = page_dir_to_flat_parts(page_dir, stem=stem)
    out = flat_output_path_for_pred(page_dir, stem)
    write_flat_text(out, parts.all_lines())
    logger.debug("Wrote %s (%d lines)", out, len(parts.all_lines()))
    return out
