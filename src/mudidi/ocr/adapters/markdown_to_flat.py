"""Normalize VLM OCR markdown into stage-1 flat lines (``<b>`` / ``<i>`` tags)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from mudidi.evaluation.stage1.normalize_typography import normalize_line
from mudidi.ocr.adapters.layout_to_transcript_v1 import FlatTranscriptParts

logger = logging.getLogger(__name__)

MARKDOWN_FLAT_SPEC_VERSION = "v1"

# GLM wraps output in fenced blocks.
_MD_FENCE_START = re.compile(r"^```(?:markdown|md)?\s*$", re.IGNORECASE)
_MD_FENCE_END = re.compile(r"^```\s*$")

# Strip non-text markdown / HTML before per-line typography normalization.
_MD_HEADER = re.compile(r"^#{1,6}\s+")
_MD_EMPTY_HEADER = re.compile(r"^#{1,6}\s*$")
_MD_LIST_ITEM = re.compile(r"^\s{0,12}[-*+•]\s+")
_MD_BULLET_SEP = re.compile(r"\s*•\s*")
_MD_DISPLAY_MATH = re.compile(r"\$\$.+?\$\$", re.DOTALL)
_INLINE_MD_HEADERS = re.compile(r"\s*#{1,6}\s+")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_TABLE_SEP = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_MD_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_HATHITRUST_JUNK = re.compile(
    r"(?:Generated at University|HathiTrust|hdl\.handle\.net|Public Domain, Google-digitized)",
    re.IGNORECASE,
)
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_HTML_DIV = re.compile(r"<div\b[^>]*>.*?</div>", re.IGNORECASE | re.DOTALL)
_HTML_ONLY_LINE = re.compile(
    r"^\s*(?:<div\b|<img\b).*",
    re.IGNORECASE | re.DOTALL,
)
# HTML block tags replaced with spaces so table cell text survives img stripping.
_HTML_BLOCK_TAG = re.compile(
    r"</?(?:table|tr|td|th|tbody|thead|tfoot|div|span|br|p)\b[^>]*>",
    re.IGNORECASE,
)
_TRAILING_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_JUNK_ONLY_DOLLARS = re.compile(r"^\$+\s*$")


def find_markdown_source(page_dir: Path, *, stem: str) -> Path | None:
    """Return the best markdown/text artifact under a page directory, if any."""
    candidates = (
        page_dir / "output.md",
        page_dir / f"{stem}.md",
        page_dir / "output.txt",
        page_dir / f"{stem}.mmd",
        page_dir / "output.mmd",
    )
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def unwrap_markdown_fences(text: str) -> str:
    """Remove a single outer ```markdown ... ``` wrapper (GLM-OCR)."""
    lines = text.splitlines()
    if not lines:
        return text
    start = 0
    end = len(lines)
    if _MD_FENCE_START.match(lines[0].strip()):
        start = 1
    if end > start and _MD_FENCE_END.match(lines[end - 1].strip()):
        end -= 1
    return "\n".join(lines[start:end])


def is_boilerplate_line(line: str) -> bool:
    """True for digitization boilerplate (HathiTrust, handle.net, etc.)."""
    return bool(_HATHITRUST_JUNK.search(line))


def expand_inline_markdown_headers(line: str) -> list[str]:
    """Split MinerU lines that concatenate many ``###`` section headers."""
    stripped = line.strip()
    if not stripped:
        return []
    parts = [part.strip() for part in _INLINE_MD_HEADERS.split(stripped) if part.strip()]
    return parts if len(parts) > 1 else [stripped]


def expand_markdown_pipe_line(line: str) -> list[str] | None:
    """Expand markdown pipe tables into cell lines; drop separator rows."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    if _MD_TABLE_SEP.match(stripped):
        return []
    match = _MD_TABLE_ROW.match(stripped)
    if not match:
        return None
    cells = [cell.strip() for cell in match.group(1).split("|")]
    return [cell for cell in cells if cell and not re.fullmatch(r":?-{3,}:?", cell)]


def strip_markdown_line_artifacts(line: str) -> str | None:
    """Drop images/headers/list markers; keep dictionary text for normalization."""
    stripped = line.strip()
    if not stripped:
        return None
    if _MD_FENCE_START.match(stripped) or _MD_FENCE_END.match(stripped):
        return None
    if _MD_EMPTY_HEADER.match(stripped):
        return None
    if is_boilerplate_line(stripped):
        return None
    if _HTML_ONLY_LINE.match(stripped):
        return None

    text = _MD_DISPLAY_MATH.sub("", stripped)
    text = _HTML_BLOCK_TAG.sub(" ", text)
    prev = None
    while prev != text:
        prev = text
        text = _MD_HEADER.sub("", text).strip()
    text = _MD_LIST_ITEM.sub("", text)
    text = _MD_BULLET_SEP.sub(" ", text)
    text = _MD_IMAGE.sub("", text)
    text = _HTML_IMG.sub("", text)
    text = _HTML_DIV.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return None
    if _TRAILING_PAGE_NUMBER.fullmatch(text):
        return None
    return text


def markdown_text_to_flat_lines(text: str) -> list[str]:
    """Convert raw OCR markdown into normalized flat lines."""
    body = unwrap_markdown_fences(text)
    lines: list[str] = []
    for raw in body.splitlines():
        if is_boilerplate_line(raw) or _MD_EMPTY_HEADER.match(raw.strip()):
            continue
        cell_lines = expand_markdown_pipe_line(raw)
        if cell_lines is not None:
            candidates = cell_lines
        else:
            candidates = expand_inline_markdown_headers(raw)
        for candidate in candidates:
            prepared = strip_markdown_line_artifacts(candidate)
            if prepared is None:
                continue
            normalized = normalize_line(prepared)
            if not normalized or _JUNK_ONLY_DOLLARS.fullmatch(normalized):
                continue
            lines.append(normalized)
    return lines


def markdown_transcript_from_page_dir(page_dir: Path, *, stem: str) -> FlatTranscriptParts:
    """Build flat transcript parts from markdown/text OCR output."""
    source = find_markdown_source(page_dir, stem=stem)
    if source is None:
        raise FileNotFoundError(f"No markdown/text OCR output under {page_dir}")
    text = source.read_text(encoding="utf-8", errors="replace")
    body = markdown_text_to_flat_lines(text)
    logger.debug(
        "Markdown flat %s: %d lines from %s",
        page_dir.name,
        len(body),
        source.name,
    )
    return FlatTranscriptParts(header=[], body=body, footer=[])
