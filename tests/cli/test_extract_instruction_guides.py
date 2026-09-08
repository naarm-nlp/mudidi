from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import pymupdf

import pytest
import mudidi.instructions as instruction_module
from mudidi.cli import extract
from mudidi.cli.main import build_parser
from mudidi.cli.run import register_run_arguments
from mudidi.cli.extract import (
    _instruction_manifest_identity,
    _write_run_config,
)


def test_public_and_direct_surfaces_parse_all_instruction_flags() -> None:
    public = build_parser().parse_args(
        [
            "run",
            "--pages",
            "pages",
            "--output-dir",
            "out",
            "--stage-1-guides",
            "one.pdf",
            "--stage-1-guides-pages",
            "1-2",
            "--stage-2-guides",
            "two.md",
            "--stage-2-guides-pages",
            "3",
            "--stage-2-guides-scope",
            "pass1",
        ]
    )
    assert public.stage1_guides_path == "one.pdf"
    assert public.stage1_guides_pages == "1-2"
    assert public.stage2_guides_path == "two.md"
    assert public.stage2_guides_pages == "3"
    assert public.stage2_guides_scope == "pass1"

    parser = argparse.ArgumentParser()
    register_run_arguments(parser)
    direct = parser.parse_args([])
    assert direct.stage2_guides_scope == "both"


def test_sparse_public_guide_overrides_are_omitted() -> None:
    args = build_parser().parse_args(["run", "--pages", "pages", "--output-dir", "out"])
    assert "stage1_guides_path" not in vars(args)
    assert "stage1_guides_pages" not in vars(args)
    assert "stage2_guides_path" not in vars(args)
    assert "stage2_guides_pages" not in vars(args)
    assert "stage2_guides_scope" not in vars(args)


def test_pdf_guide_rejected_before_ocr_dispatch(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    args = argparse.Namespace(
        strategy="vlm_ocr",
        stage1_guides_path=str(tmp_path / "guide.pdf"),
        stage2_guides_path=None,
    )
    with pytest.raises(SystemExit):
        extract._reject_unsupported_pdf_instruction_guides(args, parser)


def test_legacy_unused_manifest_identity_matches_empty_context() -> None:
    legacy = {"used": False, "path": None, "text": None}
    empty = {
        "source_path": None,
        "kind": "none",
        "original_filename": None,
        "byte_count": 0,
        "sha256": None,
        "pdf_page_count": None,
        "selected_pages": [],
        "selected_path": None,
        "selected_sha256": None,
    }
    assert _instruction_manifest_identity(
        {"stage1_guides": legacy, "stage2_guides": legacy}
    ) == _instruction_manifest_identity(
        {"stage1_guides": empty, "stage2_guides": empty, "stage2_guides_scope": "both"}
    )

def test_guide_manifest_helper_never_serializes_text() -> None:
    entry = extract._guides_manifest_entry(None)
    assert "text" not in entry
    assert "data" not in json.dumps(entry)
def test_matching_legacy_no_guide_manifest_can_resume(tmp_path: Path) -> None:
    target = tmp_path / "stage"
    legacy = {"used": False, "path": None, "text": None}
    _write_run_config(
        target,
        {"stage": "1", "stage1_guides": legacy, "stage2_guides": legacy},
        force=False,
    )
    _write_run_config(
        target,
        {
            "stage": "1",
            "stage1_guides": extract._guides_manifest_entry(None),
            "stage2_guides": extract._guides_manifest_entry(None, scope="both"),
            "stage2_guides_scope": "both",
        },
        force=False,
    )


@pytest.mark.parametrize(
    ("field", "first", "second"),
    [
        ("sha256", "a", "b"),
        ("selected_pages", [1], [2]),
        ("scope", "pass1", "pass2"),
    ],
)
def test_changed_instruction_bytes_pages_or_scope_requires_overwrite(
    tmp_path: Path,
    field: str,
    first,
    second,
) -> None:
    target = tmp_path / field
    first_entry = extract._guides_manifest_entry(None, scope="pass1")
    second_entry = extract._guides_manifest_entry(None, scope="pass1")
    if field == "scope":
        first_manifest_scope = first
        second_manifest_scope = second
        first_entry["scope"] = first
        second_entry["scope"] = second
    else:
        first_manifest_scope = second_manifest_scope = "pass1"
        first_entry[field] = first
        second_entry[field] = second
    _write_run_config(
        target,
        {
            "stage": "2",
            "stage2_guides_scope": first_manifest_scope,
            "stage2_guides": first_entry,
        },
        force=False,
    )
    with pytest.raises(ValueError, match="overwrite"):
        _write_run_config(
            target,
            {
                "stage": "2",
                "stage2_guides_scope": second_manifest_scope,
                "stage2_guides": second_entry,
            },
            force=False,
        )
def test_manifest_declares_dictionary_profile_variable() -> None:
    manifest = json.loads(Path(extract.default_prompts_path()).read_text())
    variables = {
        item["name"] for item in manifest["stage_1_user_inference"]["variables"]
    }
    assert "dictionary_profile" in variables
def test_real_single_entry_prepares_guides_once_before_two_page_workers(
    tmp_path: Path, monkeypatch
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
    )
    (pages / "page_1.png").write_bytes(png)
    (pages / "page_2.png").write_bytes(png)
    guide = tmp_path / "guide.pdf"
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.save(str(guide))
    document.close()

    original_prepare = extract.prepare_instruction_context
    prepared_contexts = []
    prepare_calls = []

    def spy_prepare(*args, **kwargs):
        prepare_calls.append((args, kwargs))
        context = original_prepare(*args, **kwargs)
        prepared_contexts.append(context)
        return context
    original_file_content_part = instruction_module.file_content_part
    original_image_data_url = instruction_module.image_data_url
    original_render_pdf_pages = instruction_module.render_pdf_pages
    file_calls = []
    image_calls = []
    render_calls = []

    def spy_file_content_part(*args, **kwargs):
        file_calls.append((args, kwargs))
        return original_file_content_part(*args, **kwargs)

    def spy_image_data_url(*args, **kwargs):
        image_calls.append((args, kwargs))
        return original_image_data_url(*args, **kwargs)

    def spy_render_pdf_pages(*args, **kwargs):
        render_calls.append((args, kwargs))
        return original_render_pdf_pages(*args, **kwargs)

    completion_calls = []

    def fake_complete_structured(*args, **kwargs):
        completion_calls.append((args, kwargs))
        return SimpleNamespace(header=[], lines=["line"], footer=[]), "{}", {}

    monkeypatch.setattr(extract, "prepare_instruction_context", spy_prepare)
    monkeypatch.setattr(instruction_module, "file_content_part", spy_file_content_part)
    monkeypatch.setattr(instruction_module, "image_data_url", spy_image_data_url)
    monkeypatch.setattr(instruction_module, "render_pdf_pages", spy_render_pdf_pages)
    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured",
        fake_complete_structured,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "mudidi-extract",
            "--input-image",
            str(pages),
            "--output",
            str(tmp_path / "output"),
            "--strategy",
            "two_stage",
            "--stage",
            "1",
            "--stage1-mode",
            "flat",
            "--model",
            "gemini/gemini-2.5-flash",
            "--agentic-evaluator-model",
            "gemini/gemini-2.5-flash",
            "--agentic-rewriter-model",
            "unknown/model",
            "--stage-1-guides",
            str(guide),
            "--stage-1-guides-pages",
            "1-2",
            "--no-alphabet",
            "--no-ocr-hint",
            "--no-intro",
        ],
    )
    assert extract.main() == 0
    assert len(completion_calls) == 2
    assert len(prepare_calls) == 2
    pdf_calls = [call for call in prepare_calls if call[0][0] == guide.resolve()]
    assert len(pdf_calls) == 1
    context = next(
        context
        for context in prepared_contexts
        if context.metadata.source_path == guide.resolve()
    )
    assert context.raster_data_urls
    assert context.pdf_data_url is not None
    assert len(file_calls) == 1
    assert len(render_calls) == 1
    assert len(image_calls) == 2
    first_parts = completion_calls[0][1]["messages"][-1]["content"]
    second_parts = completion_calls[1][1]["messages"][-1]["content"]
    first_file = next(part["file"]["file_data"] for part in first_parts if "file" in part)
    second_file = next(part["file"]["file_data"] for part in second_parts if "file" in part)
    assert first_file == second_file == context.pdf_data_url
