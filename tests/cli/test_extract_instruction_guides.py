from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
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
def test_context_preparation_is_once_before_multiple_page_calls(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def prepare(path, *, page_spec, cache_dir, models):
        calls.append((path, page_spec, cache_dir, tuple(models)))
        return SimpleNamespace(
            text="prepared",
            metadata=SimpleNamespace(original_filename="guide.txt"),
            content_parts=lambda model, stage_label: [
                {"type": "text", "text": "prepared"}
            ],
        )

    monkeypatch.setattr("mudidi.cli.extract.prepare_instruction_context", prepare)
    args = SimpleNamespace(
        strategy="two_stage",
        stage1_guides_path=tmp_path / "stage1.txt",
        stage2_guides_path=tmp_path / "stage2.txt",
        stage1_guides_pages=None,
        stage2_guides_pages=None,
        stage_models=SimpleNamespace(
            stage_1="provider/generation",
            stage_2_pass_1="provider/pass1",
            stage_2_pass_2="provider/pass2",
        ),
        agentic_evaluator_model="provider/evaluator",
        agentic_rewriter_model="provider/rewriter",
    )
    extract._prepare_instruction_contexts(args, tmp_path / "output", argparse.ArgumentParser())
    for _ in range(3):
        args.stage1_instruction_context.content_parts(
            "provider/generation", stage_label="Stage 1"
        )
    assert len(calls) == 2
