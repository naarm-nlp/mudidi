"""Tests for Pass 1 document and image attachment handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mudidi.cli.extract import _collect_intro
from mudidi.llm.pass_1 import discover_field_cheatsheet


@patch("mudidi.llm.pass_1.complete_with_usage")
def test_pass1_attaches_pdf_introduction_and_sample_as_file_parts(
    mock_complete,
    tmp_path: Path,
) -> None:
    mock_complete.return_value = (
        '{"markers": [{"marker": "lx", "description": "headword"}], '
        '"rules": [], "abbreviations": {}}',
        {"total_tokens": 1},
    )
    intro_pdf = tmp_path / "introduction.pdf"
    sample_pdf = tmp_path / "sample.pdf"
    intro_pdf.write_bytes(b"%PDF-1.7 intro")
    sample_pdf.write_bytes(b"%PDF-1.7 sample")

    discover_field_cheatsheet(
        transcription="sample transcription",
        sample_image=sample_pdf,
        intro_images=[intro_pdf],
        model="gemini/gemini-3-flash-preview",
    )

    content = mock_complete.call_args.kwargs["messages"][1]["content"]
    assert content[1]["type"] == "file"
    assert content[1]["file"]["format"] == "application/pdf"
    assert content[1]["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert content[2]["type"] == "file"
    assert content[2]["file"]["format"] == "application/pdf"


def test_collect_intro_rejects_text_files(tmp_path: Path) -> None:
    intro_text = tmp_path / "introduction.txt"
    intro_text.write_text("intro text", encoding="utf-8")

    with pytest.raises(ValueError, match="only image and PDF"):
        _collect_intro(intro_text, tmp_path / "cache", render_pdfs=False)
