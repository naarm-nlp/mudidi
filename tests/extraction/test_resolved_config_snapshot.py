from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from mudidi.cli.extract import _guides_manifest_entry, _write_run_config


def test_resolved_config_is_written_beside_stage_manifest(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"

    _write_run_config(
        stage_dir,
        {"stage": "1"},
        force=False,
        resolved_config={"kind": "benchmark_run", "input": {"pages": "/pages"}},
    )

    assert json.loads((stage_dir / "run_config.json").read_text()) == {"stage": "1"}
    assert json.loads((stage_dir / "resolved_config.json").read_text())["kind"] == (
        "benchmark_run"
    )


def test_run_manifest_serializes_path_based_guides_without_content(
    tmp_path: Path,
) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"
    guides_path = tmp_path / "stage1.txt"
    guides_path.write_text("Preserve accents.", encoding="utf-8")
    from mudidi.instructions import prepare_instruction_context

    context = prepare_instruction_context(
        guides_path,
        page_spec=None,
        cache_dir=tmp_path / "cache",
        models=[],
    )

    _write_run_config(
        stage_dir,
        {
            "stage": "1",
            "stage1_guides": _guides_manifest_entry(context),
        },
        force=False,
    )

    manifest = json.loads((stage_dir / "run_config.json").read_text())
    assert manifest["stage1_guides"]["source_path"] == str(guides_path.resolve())
    assert manifest["stage1_guides"]["sha256"]
    assert "text" not in manifest["stage1_guides"]


def test_resolved_config_resume_guard_preserves_existing_snapshot(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"
    _write_run_config(
        stage_dir,
        {"stage": "1"},
        force=False,
        resolved_config={"version": 1},
    )

    _write_run_config(
        stage_dir,
        {"stage": "changed"},
        force=False,
        resolved_config={"version": 2},
    )
    assert json.loads((stage_dir / "run_config.json").read_text())["stage"] == "1"
    assert json.loads((stage_dir / "resolved_config.json").read_text())["version"] == 1



def test_pdf_guide_manifest_is_json_safe_and_preserves_selected_artifact(
    tmp_path: Path,
) -> None:
    guide = tmp_path / "stage2-reference.pdf"
    document = pymupdf.open()
    for page_number in range(1, 4):
        document.new_page().insert_text((72, 72), f"reference page {page_number}")
    document.save(str(guide))
    document.close()

    from mudidi.instructions import prepare_instruction_context

    context = prepare_instruction_context(
        guide,
        page_spec="2,1",
        cache_dir=tmp_path / "cache",
        models=("gemini/gemini-2.5-flash", "unknown/model"),
    )
    manifest_entry = _guides_manifest_entry(context, scope="pass2")

    assert manifest_entry["kind"] == "pdf"
    assert manifest_entry["selected_pages"] == [2, 1]
    assert manifest_entry["selected_path"] is not None
    assert Path(manifest_entry["selected_path"]).is_file()
    assert manifest_entry["selected_sha256"]
    assert "text" not in manifest_entry
    json.dumps(manifest_entry)

    stage_dir = tmp_path / "stage-2" / "experiment"
    _write_run_config(
        stage_dir,
        {
            "stage": "2",
            "stage2_guides_scope": "pass2",
            "stage2_guides": manifest_entry,
        },
        force=False,
    )
    written = json.loads((stage_dir / "run_config.json").read_text(encoding="utf-8"))
    assert written["stage2_guides"]["selected_pages"] == [2, 1]
    assert written["stage2_guides"]["scope"] == "pass2"
