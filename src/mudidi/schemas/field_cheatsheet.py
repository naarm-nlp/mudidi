"""Compact MDF parsing guide for direct Stage 2 extraction."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, List

from pydantic import BaseModel, ConfigDict, Field


class MarkerLine(BaseModel):
    """One MDF marker used in this dictionary."""

    marker: str = Field(description="Two-letter MDF code without backslash, e.g. lx, gn.")
    description: str = Field(description="One-line role description for Pass 2.")


class DictionaryMarkerCheatsheet(BaseModel):
    """
    Pass 1 MDF parsing-guide output — markers and rules, without typography.

    Rendered for Pass 2 in the same style as the static Chukchi experiment script.
    """

    model_config = ConfigDict(extra="forbid")

    markers: List[MarkerLine] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)
    abbreviations: dict[str, str] = Field(default_factory=dict)

    def format_prompt_block(self) -> str:
        """Render the guide as a compact Pass 2 field map."""
        lines = [
            "MDF parsing guide (use these markers exactly):",
            "",
        ]
        for m in self.markers:
            code = m.marker.lstrip("\\")
            lines.append(f"\\{code}   {m.description}")
        if self.rules:
            lines.append("")
            lines.append("Rules:")
            for rule in self.rules:
                lines.append(f"- {rule}")
        if self.abbreviations:
            lines.append("")
            lines.append("Abbreviations:")
            for abbr, meaning in sorted(self.abbreviations.items()):
                lines.append(f"- {abbr} → {meaning}")
        return "\n".join(lines)


_MARKER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def validate_marker_cheatsheet(
    value: DictionaryMarkerCheatsheet | Mapping[str, Any],
) -> DictionaryMarkerCheatsheet:
    """Validate and normalize marker semantics shared by review and execution.

    Marker codes are stored without the MDF backslash so equivalent inputs such
    as ``lx`` and ``\\lx`` cannot evade duplicate detection.
    """

    parsed = (
        value
        if isinstance(value, DictionaryMarkerCheatsheet)
        else DictionaryMarkerCheatsheet.model_validate(value)
    )
    normalized_markers: list[MarkerLine] = []
    seen: set[str] = set()
    for marker in parsed.markers:
        code = marker.marker.strip().lstrip("\\")
        if not _MARKER_PATTERN.fullmatch(code):
            raise ValueError(f"invalid marker code: {marker.marker!r}")
        canonical = code.lower()
        if canonical in seen:
            raise ValueError(f"duplicate marker after normalization: {code!r}")
        seen.add(canonical)
        normalized_markers.append(
            MarkerLine(marker=canonical, description=marker.description.strip())
        )
    return parsed.model_copy(update={"markers": normalized_markers})
