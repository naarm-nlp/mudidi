"""Unit tests for Label Studio OCR gold sync (no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "label-studio"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from export_label_studio_gold import (  # noqa: E402
    Annotation,
    AnnotationResult,
    LabelStudioTask,
    TaskData,
    TextAreaValue,
)

_SYNC_SPEC = importlib.util.spec_from_file_location(
    "legacy_label_studio_sync",
    _REPO_ROOT / "label-studio" / "sync_from_label_studio.py",
)
assert _SYNC_SPEC is not None and _SYNC_SPEC.loader is not None
_SYNC_MODULE = importlib.util.module_from_spec(_SYNC_SPEC)
sys.modules[_SYNC_SPEC.name] = _SYNC_MODULE
_SYNC_SPEC.loader.exec_module(_SYNC_MODULE)

apply_page_sync = _SYNC_MODULE.apply_page_sync
compare_text = _SYNC_MODULE.compare_text
flat_content_from_body = _SYNC_MODULE.flat_content_from_body
flat_content_from_tsv_columns = _SYNC_MODULE.flat_content_from_tsv_columns
gold_paths = _SYNC_MODULE.gold_paths
plan_page_sync = _SYNC_MODULE.plan_page_sync
tsv_content_from_columns = _SYNC_MODULE.tsv_content_from_columns


def _annotation(from_name: str, text: str) -> Annotation:
    return Annotation(
        id=1,
        result=[
            AnnotationResult(
                from_name=from_name,
                type="textarea",
                value=TextAreaValue(text=[text]),
            )
        ],
    )


def _task(page: str, *, body: str = "", left: str = "", right: str = "") -> LabelStudioTask:
    if body:
        annotation = _annotation("body_text", body)
    else:
        annotation = Annotation(
            id=1,
            result=[
                AnnotationResult(
                    from_name="left_text",
                    type="textarea",
                    value=TextAreaValue(text=[left]),
                ),
                AnnotationResult(
                    from_name="right_text",
                    type="textarea",
                    value=TextAreaValue(text=[right]),
                ),
            ],
        )
    return LabelStudioTask(
        id=1,
        data=TaskData(
            page_name=page,
            language="Hindi-Russian",
            body_text=body,
            left_text=left,
            right_text=right,
        ),
        annotations=[annotation],
    )


def test_gold_paths_follow_dataset_convention(tmp_path):
    paths = gold_paths(tmp_path, "Japanese-English", "page_351")
    assert paths.page_dir == tmp_path / "Japanese-English" / "Stage 1 Gold OCR" / "page_351"
    assert paths.flat_path.name == "page_351_stage1_GOLD_flat.txt"
    assert paths.tsv_path.name == "page_351_stage1_GOLD.tsv"


def test_plan_page_sync_flat_format_marks_new(tmp_path):
    task = _task("page_27", body="hello\nworld")
    plan = plan_page_sync(task, tmp_path, gold_format="flat", include_prefill=False)
    assert plan.status == "new"
    assert plan.flat_content == "hello\nworld"
    assert plan.tsv_content is None


def test_plan_page_sync_flat_format_unchanged_when_disk_matches(tmp_path):
    task = _task("page_27", body="same text")
    paths = gold_paths(tmp_path, "Hindi-Russian", "page_27")
    paths.page_dir.mkdir(parents=True)
    paths.flat_path.write_text("same text\n", encoding="utf-8")

    plan = plan_page_sync(task, tmp_path, gold_format="flat", include_prefill=False)
    assert plan.status == "unchanged"


def test_plan_page_sync_tsv_format_writes_both(tmp_path):
    task = _task("page_28", left="left one", right="right one")
    plan = plan_page_sync(task, tmp_path, gold_format="tsv", include_prefill=False)

    assert plan.status == "new"
    assert "left\t1\tleft one" in (plan.tsv_content or "")
    assert plan.flat_content == "left one\nright one"


def test_apply_page_sync_tsv_format_writes_tsv_and_flat(tmp_path):
    task = _task("page_28", left="left one", right="right one")
    plan = plan_page_sync(task, tmp_path, gold_format="tsv", include_prefill=False)
    apply_page_sync(plan, dry_run=False)

    paths = gold_paths(tmp_path, "Hindi-Russian", "page_28")
    assert paths.tsv_path.is_file()
    assert paths.flat_path.is_file()
    assert "left one" in paths.tsv_path.read_text(encoding="utf-8")
    assert paths.flat_path.read_text(encoding="utf-8").strip() == "left one\nright one"


def test_plan_page_sync_tsv_format_detects_stale_flat(tmp_path):
    task = _task("page_28", left="left one", right="right one")
    paths = gold_paths(tmp_path, "Hindi-Russian", "page_28")
    paths.page_dir.mkdir(parents=True)
    paths.tsv_path.write_text(tsv_content_from_columns({"left": "left one", "right": "right one"}), encoding="utf-8")
    paths.flat_path.write_text("stale flat\n", encoding="utf-8")

    plan = plan_page_sync(task, tmp_path, gold_format="tsv", include_prefill=False)
    assert plan.status == "changed"


def test_compare_text_detects_change(tmp_path):
    path = tmp_path / "page.txt"
    path.write_text("old\n", encoding="utf-8")
    assert compare_text(path, "new") == "changed"


def test_flat_content_from_tsv_columns_includes_header_and_footer():
    columns = {
        "header": "HDR",
        "left": "left line",
        "right": "right line",
        "footer": "FTR",
    }
    flat = flat_content_from_tsv_columns(columns, dictionary="Hindi-Russian")
    assert flat.splitlines() == ["HDR", "left line", "right line", "FTR"]


def test_flat_content_from_body():
    assert flat_content_from_body({"single": "line one\nline two"}) == "line one\nline two"


def test_tsv_content_from_columns_has_header_row():
    content = tsv_content_from_columns({"single": "only line"})
    assert content.splitlines()[0] == "column_id\tline_number\ttext"
    assert "single\t1\tonly line" in content
