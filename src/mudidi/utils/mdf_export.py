"""Normalization helpers for direct Toolbox MDF output."""

from __future__ import annotations

import re

_MDF_MARKER_LINE = re.compile(r"^\\(\w+)\s+(.*)$", re.UNICODE)
_END_SENTENCE_PUNCT = frozenset(".!?")
_MARKUP_TAG_RE = re.compile(r"</?[bi]>", re.IGNORECASE)


def strip_end_of_sentence_punctuation(text: str) -> str:
    """Remove trailing sentence punctuation from an MDF field value."""
    trimmed = text.rstrip()
    while trimmed and trimmed[-1] in _END_SENTENCE_PUNCT:
        trimmed = trimmed[:-1].rstrip()
    return trimmed


def strip_mdf_markup(value: str) -> str:
    """Remove leaked ``<b>``/``<i>`` tags from an MDF field value."""
    return _MARKUP_TAG_RE.sub("", value)


def normalize_mdf_field_value(value: str) -> str:
    """Strip markup and trailing sentence punctuation from one field value."""
    return strip_end_of_sentence_punctuation(strip_mdf_markup(value))


def normalize_mdf_text(mdf_text: str) -> str:
    """Normalize marker values in direct MDF output."""
    out_lines: list[str] = []
    for line in mdf_text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append("")
            continue
        match = _MDF_MARKER_LINE.match(stripped)
        if match:
            marker, value = match.groups()
            out_lines.append(f"\\{marker} {normalize_mdf_field_value(value)}")
        else:
            out_lines.append(stripped)
    return "\n".join(out_lines)
