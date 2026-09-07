from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import pymupdf
from docx import Document

import mudidi.instructions as instruction_module
from mudidi.instructions import (
    InstructionMetadata,
    PreparedInstructionContext,
    prepare_instruction_context,
    read_instruction_text,
    resolve_instruction_pdf_pages,
)


def _write_pdf(path: Path, page_count: int) -> None:
    document = pymupdf.open()
    try:
        for page_number in range(1, page_count + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"instruction page {page_number}")
        document.save(str(path))
    finally:
        document.close()


def _write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(str(path))


def test_no_source_has_empty_immutable_context_and_manifest() -> None:
    context = prepare_instruction_context(
        None,
        page_spec=None,
        cache_dir=Path("cache"),
        models=(),
    )

    assert isinstance(context, PreparedInstructionContext)
    assert context.text == ""
    assert context.metadata == InstructionMetadata(
        source_path=None,
        kind="none",
        original_filename=None,
        byte_count=0,
        sha256=None,
        pdf_page_count=None,
        selected_pages=(),
        selected_path=None,
        selected_sha256=None,
    )
    assert context.content_parts("any/model", stage_label="Stage 1") == []
    manifest = context.manifest_entry(scope="both")
    assert json.dumps(manifest)
    assert "text" not in manifest


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_text_instruction_is_decoded_once_and_manifest_contains_digest(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / f"guide{suffix}"
    raw = "Use the established marker order.\n".encode("utf-8")
    source.write_bytes(raw)

    context = prepare_instruction_context(
        source,
        page_spec=None,
        cache_dir=tmp_path / "cache",
        models=(),
    )

    assert context.text == raw.decode("utf-8")
    assert context.metadata.kind == "text"
    assert context.metadata.byte_count == len(raw)
    assert context.metadata.sha256 == hashlib.sha256(raw).hexdigest()
    assert context.metadata.selected_pages == ()
    manifest = context.manifest_entry(scope="pass1")
    assert manifest["source_path"] == str(source)
    assert manifest["original_filename"] == source.name
    assert manifest["scope"] == "pass1"
    assert "Use the established marker order" not in json.dumps(manifest)


def test_docx_guide_remains_accepted_by_runtime(tmp_path: Path) -> None:
    source = tmp_path / "guide.docx"
    _write_docx(source, "DOCX guide text")

    assert read_instruction_text(source) == "DOCX guide text"
    context = prepare_instruction_context(
        source,
        page_spec=None,
        cache_dir=tmp_path / "cache",
        models=(),
    )
    assert context.metadata.kind == "text"
    assert context.text == "DOCX guide text"


@pytest.mark.parametrize("content", [" \n\t", "x" * 20_001])
def test_blank_or_oversized_text_is_rejected(tmp_path: Path, content: str) -> None:
    source = tmp_path / "guide.txt"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="guide.txt"):
        prepare_instruction_context(
            source,
            page_spec=None,
            cache_dir=tmp_path / "cache",
            models=(),
        )


def test_pdf_all_pages_and_subset_are_materialized_once(tmp_path: Path) -> None:
    source = tmp_path / "guide.pdf"
    _write_pdf(source, 4)

    assert resolve_instruction_pdf_pages(source, None) == (1, 2, 3, 4)
    assert resolve_instruction_pdf_pages(source, "3,1-2,2") == (3, 1, 2)

    context = prepare_instruction_context(
        source,
        page_spec="3,1-2,2",
        cache_dir=tmp_path / "cache",
        models=("gemini/gemini-2.5-flash",),
    )
    assert context.metadata.kind == "pdf"
    assert context.metadata.pdf_page_count == 4
    assert context.metadata.selected_pages == (3, 1, 2)
    assert context.metadata.selected_path is not None
    assert context.metadata.selected_path.exists()
    assert context.metadata.selected_sha256
    with pymupdf.open(str(context.metadata.selected_path)) as selected:
        assert selected.page_count == 3

    assert context.pdf_data_url is not None
    first = context.content_parts("gemini/gemini-2.5-flash", stage_label="Stage 1")
    second = context.content_parts("gemini/gemini-2.5-flash", stage_label="Stage 1")
    assert first == second
    assert first[0]["type"] == "text"
    assert "reference instructions" in first[0]["text"]
    assert "Stage 1" in first[0]["text"]
    assert first[1]["type"] == "file"
    assert first[1]["file"]["file_data"] is context.pdf_data_url


def test_pdf_raster_pages_are_prepared_once_for_unknown_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "guide.pdf"
    _write_pdf(source, 2)
    monkeypatch.setattr(instruction_module, "model_supports_pdf_input", lambda _model: False)
    original_render = instruction_module.render_pdf_pages
    render_calls: list[Path] = []

    def counted_render(pdf_path: Path, cache_dir: Path, **kwargs: object) -> list[Path]:
        render_calls.append(pdf_path)
        return original_render(pdf_path, cache_dir, **kwargs)

    monkeypatch.setattr(instruction_module, "render_pdf_pages", counted_render)
    context = prepare_instruction_context(
        source,
        page_spec="1-2",
        cache_dir=tmp_path / "cache",
        models=("unknown/model",),
    )

    assert context.pdf_data_url is None
    assert len(context.raster_data_urls) == 2
    assert len(render_calls) == 1
    first = context.content_parts("unknown/model", stage_label="Stage 2 pass 2")
    second = context.content_parts("unknown/model", stage_label="Stage 2 pass 2")
    assert first == second
    assert [part["type"] for part in first] == ["text", "image_url", "image_url"]
    assert "reference instructions" in first[0]["text"]
    assert "page order" in first[0]["text"]


def test_pdf_mixed_models_prepare_direct_and_raster_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "guide.pdf"
    _write_pdf(source, 1)
    monkeypatch.setattr(
        instruction_module,
        "model_supports_pdf_input",
        lambda model: model == "direct/model",
    )

    context = prepare_instruction_context(
        source,
        page_spec=None,
        cache_dir=tmp_path / "cache",
        models=("direct/model", "unknown/model"),
    )

    assert context.pdf_data_url is not None
    assert len(context.raster_data_urls) == 1
    assert context.content_parts("direct/model", stage_label="Stage 1")[1]["type"] == "file"
    assert context.content_parts("unknown/model", stage_label="Stage 1")[1]["type"] == "image_url"


def test_invalid_pdf_page_selection_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "guide.pdf"
    _write_pdf(source, 2)

    with pytest.raises(ValueError, match="page 3"):
        prepare_instruction_context(
            source,
            page_spec="1,3",
            cache_dir=tmp_path / "cache",
            models=("direct/model",),
        )
