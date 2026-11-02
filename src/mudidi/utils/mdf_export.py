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
    """Normalize field values in direct MDF output.

    A non-blank line without a leading ``\\marker`` continues the field above
    it (Toolbox standard format); a page's first line may continue a field the
    previous page opened. Markup is stripped from every line of a field, and
    trailing sentence punctuation only from the field's last line.
    """
    lines = [line.strip() for line in mdf_text.splitlines()]

    def continues(index: int) -> bool:
        return (
            index < len(lines)
            and bool(lines[index])
            and not lines[index].startswith("\\")
        )

    out_lines: list[str] = []
    for index, stripped in enumerate(lines):
        if not stripped:
            out_lines.append("")
            continue
        ends_field = not continues(index + 1)
        match = _MDF_MARKER_LINE.match(stripped)
        if match:
            marker, value = match.groups()
            value = strip_mdf_markup(value)
            if ends_field:
                value = strip_end_of_sentence_punctuation(value)
            out_lines.append(f"\\{marker} {value}")
        elif stripped.startswith("\\"):
            out_lines.append(stripped)
        else:
            value = strip_mdf_markup(stripped)
            if ends_field:
                value = strip_end_of_sentence_punctuation(value)
            if value:
                # A continuation that was only punctuation adds nothing;
                # keeping it as a blank line would end the record.
                out_lines.append(value)
    return "\n".join(out_lines)
