"""Tests for PyMuPDF-backed PDF page extraction."""

from __future__ import annotations

from pathlib import Path

import pytest
import pymupdf

from mudidi.utils.pdf_split import extract_pdf_pages


def _write_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page()
        page.insert_text((72, 72), label)
    document.save(path)
    document.close()


def _page_text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return document[0].get_text().strip()


def test_extract_pdf_pages_opens_source_once_and_reports_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.pdf"
    output_dir = tmp_path / "split"
    _write_pdf(source, ["page-1", "page-2", "page-3"])

    real_open = pymupdf.open
    source_open_count = 0

    def tracked_open(*args: object, **kwargs: object) -> pymupdf.Document:
        nonlocal source_open_count
        if args and Path(str(args[0])).resolve() == source.resolve():
            source_open_count += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", tracked_open)
    paths = extract_pdf_pages(source, [3, 1, 3], output_dir)

    assert source_open_count == 1
    assert [path.name for path in paths] == ["page_3.pdf", "page_1.pdf", "page_3.pdf"]
    assert [_page_text(path) for path in paths] == ["page-3", "page-1", "page-3"]
    output = capsys.readouterr().out
    assert "PDF split: source.pdf (3 requested page(s))" in output
    assert "PDF split: wrote source page 3 (1/3)" in output
    assert "PDF split: reused source page 3 (3/3)" in output
    assert "PDF split complete: source.pdf (3 page(s))" in output


def test_extract_pdf_pages_preserves_cache_and_overwrite_behavior(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output_dir = tmp_path / "split"
    _write_pdf(source, ["before"])

    extract_pdf_pages(source, [1], output_dir)
    _write_pdf(source, ["after"])

    extract_pdf_pages(source, [1], output_dir)
    assert _page_text(output_dir / "page_1.pdf") == "before"

    extract_pdf_pages(source, [1], output_dir, overwrite=True)
    assert _page_text(output_dir / "page_1.pdf") == "after"


def test_extract_pdf_pages_rejects_page_outside_source(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _write_pdf(source, ["only-page"])

    with pytest.raises(ValueError, match="page 2 is outside source PDF"):
        extract_pdf_pages(source, [2], tmp_path / "split")
