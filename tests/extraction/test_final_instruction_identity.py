from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from mudidi.cli.extract import _instruction_manifest_identity
from mudidi.instructions import prepare_instruction_context
from mudidi.llm.pass_1 import (
    _ensure_parse_rules_cache_compatible,
    _parse_rules_cache_identity,
    _write_parse_rules_cache_metadata,
)
from mudidi.llm.pass_2 import _stage2_prompt_cache_key


def _pdf_bytes(page_count: int = 3, *, marker: bytes = b"") -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    try:
        return document.tobytes() + marker
    finally:
        document.close()


def _context(
    root: Path,
    *,
    page_spec: str | None = "1",
    content: bytes | None = None,
):
    source = root / "managed" / "reference.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(content if content is not None else _pdf_bytes())
    return prepare_instruction_context(
        source,
        page_spec=page_spec,
        cache_dir=root / "cache",
        models=[],
    )


def test_same_instruction_bytes_under_rebased_roots_share_resume_and_cache_identity(
    tmp_path: Path,
) -> None:
    content = _pdf_bytes()
    first = _context(tmp_path / "first", content=content)
    second = _context(tmp_path / "second", content=content)
    first_manifest = {
        "stage1_guides": first.manifest_entry(),
        "stage2_guides": first.manifest_entry(scope="both"),
        "stage2_guides_scope": "both",
    }
    second_manifest = {
        "stage1_guides": second.manifest_entry(),
        "stage2_guides": second.manifest_entry(scope="both"),
        "stage2_guides_scope": "both",
    }

    assert _instruction_manifest_identity(first_manifest) == _instruction_manifest_identity(
        second_manifest
    )
    assert _parse_rules_cache_identity(first, "both") == _parse_rules_cache_identity(
        second, "both"
    )
    assert _stage2_prompt_cache_key(
        model="openai/gpt-5.5",
        static_text="stable static prompt",
        prompt_cache_key=None,
        instruction_context=first,
        instruction_scope="both",
    ) == _stage2_prompt_cache_key(
        model="openai/gpt-5.5",
        static_text="stable static prompt",
        prompt_cache_key=None,
        instruction_context=second,
        instruction_scope="both",
    )


def test_parse_rules_cache_rejects_reuse_after_mdf_manual_toggle(tmp_path: Path) -> None:
    cache_path = tmp_path / "mdf_parsing_guide.json"
    cache_path.write_text(json.dumps({"markers": [], "rules": []}), encoding="utf-8")
    metadata_path = cache_path.with_name(cache_path.name + ".meta.json")
    _write_parse_rules_cache_metadata(
        cache_path, instruction_context=None, instruction_scope="both"
    )
    legacy = json.loads(metadata_path.read_text(encoding="utf-8"))
    legacy.pop("mdf_manual")
    metadata_path.write_text(json.dumps(legacy), encoding="utf-8")

    _ensure_parse_rules_cache_compatible(
        cache_path, instruction_context=None, instruction_scope="both", force_refresh=False
    )
    with pytest.raises(ValueError, match="MDF manual"):
        _ensure_parse_rules_cache_compatible(
            cache_path,
            instruction_context=None,
            instruction_scope="both",
            force_refresh=False,
            mdf_manual=True,
        )

    metadata_path.unlink()
    with pytest.raises(ValueError, match="metadata"):
        _ensure_parse_rules_cache_compatible(
            cache_path,
            instruction_context=None,
            instruction_scope="both",
            force_refresh=False,
            mdf_manual=True,
        )


def test_rebased_parse_rules_cache_metadata_is_reusable(tmp_path: Path) -> None:
    content = _pdf_bytes()
    first = _context(tmp_path / "first", content=content)
    second = _context(tmp_path / "second", content=content)
    cache_path = tmp_path / "parse-rules" / "mdf_parsing_guide.json"
    cache_path.parent.mkdir()
    cache_path.write_text(json.dumps({"markers": [], "rules": []}), encoding="utf-8")
    _write_parse_rules_cache_metadata(
        cache_path,
        instruction_context=first,
        instruction_scope="both",
    )

    _ensure_parse_rules_cache_compatible(
        cache_path,
        instruction_context=second,
        instruction_scope="both",
        force_refresh=False,
    )


def test_instruction_identity_changes_for_bytes_pages_or_scope(tmp_path: Path) -> None:
    baseline = _context(tmp_path / "baseline", page_spec="1")
    changed_pages = _context(tmp_path / "changed-pages", page_spec="2")
    changed_bytes_root = tmp_path / "changed-bytes"
    changed_bytes_source = changed_bytes_root / "managed" / "reference.pdf"
    changed_bytes_source.parent.mkdir(parents=True)
    changed_bytes_source.write_bytes(_pdf_bytes(marker=b"\n% changed"))
    changed_bytes = prepare_instruction_context(
        changed_bytes_source,
        page_spec="1",
        cache_dir=changed_bytes_root / "cache",
        models=[],
    )

    baseline_manifest = {
        "stage1_guides": baseline.manifest_entry(),
        "stage2_guides": baseline.manifest_entry(scope="both"),
        "stage2_guides_scope": "both",
    }
    pages_manifest = {
        "stage1_guides": changed_pages.manifest_entry(),
        "stage2_guides": changed_pages.manifest_entry(scope="both"),
        "stage2_guides_scope": "both",
    }
    bytes_manifest = {
        "stage1_guides": changed_bytes.manifest_entry(),
        "stage2_guides": changed_bytes.manifest_entry(scope="both"),
        "stage2_guides_scope": "both",
    }

    assert _instruction_manifest_identity(baseline_manifest) != _instruction_manifest_identity(
        pages_manifest
    )
    assert _instruction_manifest_identity(baseline_manifest) != _instruction_manifest_identity(
        bytes_manifest
    )
    assert _stage2_prompt_cache_key(
        model="openai/gpt-5.5",
        static_text="stable static prompt",
        prompt_cache_key=None,
        instruction_context=baseline,
        instruction_scope="both",
    ) != _stage2_prompt_cache_key(
        model="openai/gpt-5.5",
        static_text="stable static prompt",
        prompt_cache_key=None,
        instruction_context=baseline,
        instruction_scope="pass1",
    )
