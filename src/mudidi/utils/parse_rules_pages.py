"""Resolve Stage 2 Pass 1 parse-rules sample page stems and image paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from mudidi.utils.pdf_split import parse_page_spec

_PAGE_STEM_RE = re.compile(r"^page_(\d+)$", re.IGNORECASE)


def _stem_to_page_label(stem: str) -> str:
    """Map ``page_{N}`` stems to 1-based page numbers for CLI error messages."""
    match = _PAGE_STEM_RE.match(stem)
    if match:
        return match.group(1)
    return stem


def _part_to_stems(part: str) -> list[str]:
    """Map one CLI token to ``page_{N}`` stem(s).

    Accepts legacy stems (``page_50``) or the same 1-based page specs as
    ``--dict-pages`` (``1``, ``1-4``, ``1,3,5``).
    """
    token = part.strip()
    if not token:
        return []
    if _PAGE_STEM_RE.match(token):
        return [token]
    page_numbers = parse_page_spec(token)
    return [f"page_{page}" for page in page_numbers]


def normalize_parse_rules_page_stems(
    values: str | list[str] | None,
) -> list[str]:
    """Expand CLI ``--parse-rules-page`` values into an ordered stem list.

    Supports repeated flags and comma-separated lists using the same page
    number syntax as ``--dict-pages``, e.g. ``--parse-rules-page 1``,
    ``--parse-rules-page 1-4``, or ``--parse-rules-page 50,200``.
    Legacy ``page_{N}`` stems are still accepted.
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    stems: list[str] = []
    for raw in values:
        for part in raw.split(","):
            stems.extend(_part_to_stems(part))
    return stems


def select_parse_rules_sample_images(
    images: list[Path],
    stems: list[str],
) -> list[Path]:
    """Map parse-rules stems to snippet paths from the current run."""
    if not images:
        raise ValueError("No dictionary pages available for parse-rules discovery.")

    by_stem = {path.stem: path for path in images}
    if not stems:
        return [images[0]]

    missing = [stem for stem in stems if stem not in by_stem]
    if missing:
        missing_labels = [_stem_to_page_label(stem) for stem in missing]
        available_labels = [_stem_to_page_label(stem) for stem in sorted(by_stem)]
        raise ValueError(
            f"--parse-rules-page page(s) not found in --pages input: {missing_labels}. "
            f"Available pages: {available_labels}"
        )
    return [by_stem[stem] for stem in stems]


def format_sample_pages_block(samples: Sequence[tuple[str, str]]) -> str:
    """Build multi-sample transcription block for Pass 1 user prompt."""
    blocks: list[str] = []
    for stem, transcription in samples:
        blocks.append(
            f'<sample_transcription page="{stem}">\n{transcription.strip()}\n</sample_transcription>'
        )
    return "\n\n".join(blocks)
