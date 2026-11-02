"""Parse Toolbox MDF text into blank-line-delimited lexicon records."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

from mudidi.utils.text import normalize_text

MDF_LINE_RE = re.compile(r"^\\([a-zA-Z0-9_-]+)\s+(.*)$")


@dataclass(frozen=True)
class FieldLine:
    """One ``\\marker value`` line."""

    marker: str
    value: str
    line_index: int


@dataclass
class MdfRecord:
    """One blank-line-delimited lexicon block."""

    index: int
    lines: List[FieldLine] = field(default_factory=list)

    @property
    def headword(self) -> str:
        for line in self.lines:
            if line.marker == "lx":
                return line.value
        return ""

    def fingerprint(self) -> str:
        """Concatenate normalized field values in block order (markers excluded)."""
        parts = [normalize_field_value(line.value) for line in self.lines if line.value.strip()]
        return " ".join(parts)


def normalize_field_value(value: str) -> str:
    """Normalize a field value for fingerprinting and line alignment."""
    text = unicodedata.normalize("NFC", value.strip())
    text = normalize_text(text)
    text = re.sub(r"</?[bi]>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_mdf(text: str) -> List[MdfRecord]:
    """Parse MDF text into records separated by blank lines.

    A non-blank line without a leading ``\\marker`` continues the field above
    it, as in Toolbox standard format, and is joined to that field's value with
    a space. One that opens a record (a page continuing a field begun on the
    previous page) is kept as a field with an empty marker.

    Args:
        text: Raw MDF file contents.

    Returns:
        Records in file order with 0-based indices.
    """
    records: List[MdfRecord] = []
    block_lines: List[FieldLine] = []
    line_index = 0

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if block_lines:
                records.append(MdfRecord(index=len(records), lines=block_lines))
                block_lines = []
            continue
        match = MDF_LINE_RE.match(stripped)
        if not match:
            if stripped.startswith("\\"):
                continue
            if block_lines:
                previous = block_lines[-1]
                block_lines[-1] = FieldLine(
                    marker=previous.marker,
                    value=f"{previous.value} {stripped}".strip(),
                    line_index=previous.line_index,
                )
            else:
                block_lines.append(
                    FieldLine(marker="", value=stripped, line_index=line_index)
                )
                line_index += 1
            continue
        block_lines.append(
            FieldLine(marker=match.group(1), value=match.group(2), line_index=line_index)
        )
        line_index += 1

    if block_lines:
        records.append(MdfRecord(index=len(records), lines=block_lines))
    return records
