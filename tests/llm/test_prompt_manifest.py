"""Tests for manifest-backed prompt templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mudidi.llm import prompt_store as prompt_store_module
from mudidi.llm.prompt_store import (
    PromptStore,
    configure_prompts,
    default_prompts_path,
    get_prompt_store,
    parse_prompt_file,
)


EXPECTED_PROMPT_FILES = {
    "stage_1_system_benchmark": "stage_1/system_benchmark.txt",
    "stage_1_system_inference": "stage_1/system_inference.j2",
    "stage_1_column_system": "stage_1/legacy/column_system.j2",
    "stage_1_user_benchmark": "stage_1/user_benchmark.j2",
    "stage_1_user_inference": "stage_1/user_inference.j2",
    "stage_2_pass_1_system": "stage_2/pass_1/system.j2",
    "stage_2_pass_1_user_single": "stage_2/pass_1/user_single.j2",
    "stage_2_pass_1_user_multi": "stage_2/pass_1/user_multi.j2",
    "mdf_marker_reference": "stage_2/pass_1/mdf_marker_reference.txt",
    "stage_2_pass_2_system_benchmark": "stage_2/pass_2/system_benchmark.txt",
    "stage_2_pass_2_system_inference": "stage_2/pass_2/system_inference.j2",
    "stage_2_pass_2_user_benchmark": "stage_2/pass_2/user_benchmark.j2",
    "stage_2_pass_2_user_inference": "stage_2/pass_2/user_inference.j2",
    "page_boundary_rules": "stage_2/pass_2/page_boundary_rules.txt",
}

RETIRED_COMPONENT_PROMPT_IDS = {
    "stage_1_neighbor_context",
    "stage_1_typography_instruction",
    "stage_1_user_alphabet",
    "stage_1_user_closing",
    "stage_1_user_ocr_reference",
    "stage_2_toolbox_pdf_section",
    "stage_2_toolbox_text_section",
}


def _write_manifest(path: Path, entries: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def test_default_prompt_source_is_manifest_with_external_templates() -> None:
    manifest_path = default_prompts_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path.name == "manifest.json"
    assert manifest_path.parent.name == "prompts"
    assert manifest == {
        prompt_id: {**manifest[prompt_id], "file": expected_file}
        for prompt_id, expected_file in EXPECTED_PROMPT_FILES.items()
    }
    for entry in manifest.values():
        assert "file" in entry
        assert "prompt" not in entry
        assert (manifest_path.parent / entry["file"]).is_file()


def test_manifest_groups_primary_stage_prompts_before_supporting_blocks() -> None:
    manifest = json.loads(default_prompts_path().read_text(encoding="utf-8"))

    assert list(manifest) == list(EXPECTED_PROMPT_FILES)
    assert RETIRED_COMPONENT_PROMPT_IDS.isdisjoint(manifest)


def test_prompt_manifest_keeps_complete_message_templates_readable() -> None:
    store = get_prompt_store()

    assert "{% if alphabet_text %}" in store.get("stage_1_user_benchmark")
    assert "{% if ocr_hint %}" in store.get("stage_1_user_benchmark")
    assert "dictionary_profile" not in store.get("stage_1_user_benchmark")
    assert "{% if dictionary_profile %}" in store.get("stage_1_user_inference")
    assert "This profile is context only." in store.get("stage_1_user_inference")
    benchmark_system = store.get("stage_1_system_benchmark")
    inference_system = store.get("stage_1_system_inference")
    assert "full left column top-to-bottom" in benchmark_system
    assert "aligned table columns" not in benchmark_system
    assert "full left column top-to-bottom" in inference_system
    assert "aligned table columns" not in inference_system
    for prompt_id in ("stage_2_pass_1_user_single", "stage_2_pass_1_user_multi"):
        assert "{% if config_hint %}" in store.get(prompt_id)

    pass2_benchmark = store.get("stage_2_pass_2_user_benchmark")
    pass2_inference = store.get("stage_2_pass_2_user_inference")
    assert "{% if toolbox_reference_mode == 'pdf' %}" in store.get(
        "stage_2_pass_2_user_benchmark"
    )
    assert "{% elif toolbox_reference_mode == 'text_fallback' %}" in store.get(
        "stage_2_pass_2_user_benchmark"
    )
    assert "{% if guides %}" in pass2_benchmark
    for variable in (
        "guides",
        "current_page_context",
        "page_image_order",
        "previous_page_context",
        "next_page_context",
    ):
        assert f"{{% if {variable} %}}" in pass2_inference


def test_prompt_layout_includes_human_request_assembly_maps() -> None:
    prompt_root = default_prompts_path().parent

    stage1_readme = (prompt_root / "stage_1" / "README.md").read_text(encoding="utf-8")
    pass1_readme = (
        prompt_root / "stage_2" / "pass_1" / "README.md"
    ).read_text(encoding="utf-8")
    pass2_readme = (
        prompt_root / "stage_2" / "pass_2" / "README.md"
    ).read_text(encoding="utf-8")

    assert "system_benchmark.txt" in stage1_readme
    assert "user_benchmark.j2" in stage1_readme
    assert "user_inference.j2" in stage1_readme
    assert "system.j2" in pass1_readme
    assert "user_single.j2" in pass1_readme
    assert "system_benchmark.txt" in pass2_readme
    assert "user_benchmark.j2" in pass2_readme


def test_prompt_store_loads_and_reloads_external_template(tmp_path: Path) -> None:
    template = tmp_path / "stage_1" / "system.txt"
    template.parent.mkdir()
    template.write_text("first prompt\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        {
            "example": {
                "description": "Example prompt",
                "file": "stage_1/system.txt",
                "variables": [],
            }
        },
    )
    store = PromptStore(manifest_path)

    assert store.get("example") == "first prompt"

    template.write_text("second prompt with a different size\n", encoding="utf-8")

    assert store.get("example") == "second prompt with a different size"


def test_prompt_store_renders_jinja_conditionals_for_j2_templates(tmp_path: Path) -> None:
    template = tmp_path / "stage_1" / "user.j2"
    template.parent.mkdir()
    template.write_text(
        "{% if alphabet_text %}alphabet={{ alphabet_text }}\\n{% endif %}closing",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        {
            "example": {
                "description": "Jinja example",
                "file": "stage_1/user.j2",
                "variables": [],
            }
        },
    )
    store = PromptStore(manifest_path)

    assert store.format("example", alphabet_text="abc") == "alphabet=abc\\nclosing"
    assert store.format("example", alphabet_text="") == "closing"


def test_prompt_manifest_rejects_template_outside_its_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    manifest_path = prompt_dir / "manifest.json"
    _write_manifest(
        manifest_path,
        {
            "example": {
                "description": "Invalid path",
                "file": "../outside.txt",
                "variables": [],
            }
        },
    )

    with pytest.raises(ValueError, match="must remain inside"):
        PromptStore(manifest_path).get("example")


def test_legacy_inline_prompt_json_remains_supported(tmp_path: Path) -> None:
    prompts_path = tmp_path / "PROMPT.json"
    _write_manifest(
        prompts_path,
        {
            "legacy": {
                "description": "Legacy inline prompt",
                "prompt": "inline text",
                "variables": [],
            }
        },
    )

    assert PromptStore(prompts_path).get("legacy") == "inline text"


def test_prompt_entry_requires_exactly_one_text_source(tmp_path: Path) -> None:
    template = tmp_path / "prompt.txt"
    template.write_text("external", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        {
            "ambiguous": {
                "description": "Invalid entry",
                "file": "prompt.txt",
                "prompt": "inline",
                "variables": [],
            }
        },
    )

    with pytest.raises(ValueError, match="exactly one of 'file' or 'prompt'"):
        PromptStore(manifest_path).get("ambiguous")


@pytest.mark.parametrize("file_name", ["", "/absolute/prompt.txt"])
def test_prompt_manifest_rejects_invalid_template_paths(
    tmp_path: Path,
    file_name: str,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        {
            "invalid": {
                "description": "Invalid path",
                "file": file_name,
                "variables": [],
            }
        },
    )

    with pytest.raises(ValueError, match="must remain inside"):
        PromptStore(manifest_path).get("invalid")


def test_parse_prompt_file_requires_base_directory_for_manifest_entries() -> None:
    text = json.dumps(
        {
            "external": {
                "description": "External prompt",
                "file": "prompt.txt",
                "variables": [],
            }
        }
    )

    with pytest.raises(ValueError, match="base_dir is required"):
        parse_prompt_file(text)


def test_prompt_store_formats_variables_and_reports_unknown_ids(tmp_path: Path) -> None:
    prompts_path = tmp_path / "PROMPT.json"
    _write_manifest(
        prompts_path,
        {
            "greeting": {
                "description": "Greeting",
                "prompt": "Hello, {name}!",
                "variables": [
                    {
                        "name": "name",
                        "tag": None,
                        "description": "Person to greet",
                    }
                ],
            }
        },
    )
    store = PromptStore(prompts_path)

    assert store.format("greeting", name="Ada") == "Hello, Ada!"
    assert store.format("greeting") == "Hello, {name}!"
    assert store.variables("greeting")[0].name == "name"
    with pytest.raises(KeyError, match="Available: greeting"):
        store.get("missing")


def test_prompt_store_set_path_and_missing_file_errors(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _write_manifest(
        first_path,
        {"first": {"description": "", "prompt": "one", "variables": []}},
    )
    _write_manifest(
        second_path,
        {"second": {"description": "", "prompt": "two", "variables": []}},
    )
    store = PromptStore(first_path)
    assert store.get("first") == "one"

    store.set_path(second_path)

    assert store.path == second_path
    assert store.prompt_ids() == ["second"]
    with pytest.raises(FileNotFoundError, match="Prompts file not found"):
        PromptStore(tmp_path / "missing.json").prompt_ids()


def test_configure_prompts_supports_legacy_inline_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts_path = tmp_path / "custom.json"
    _write_manifest(
        prompts_path,
        {"custom": {"description": "", "prompt": "custom", "variables": []}},
    )
    monkeypatch.setattr(prompt_store_module, "_configured_path", None)
    monkeypatch.setattr(prompt_store_module, "_store", None)

    configure_prompts(prompts_path)

    assert get_prompt_store().path == prompts_path.resolve()
    assert get_prompt_store().get("custom") == "custom"


def test_zip_resource_materialization_copies_manifest_and_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_store_module, "_bundled_prompts_cache", None)
    monkeypatch.setattr(
        prompt_store_module.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    manifest_path = prompt_store_module._materialize_zip_resource_prompts()

    assert manifest_path == tmp_path / "mudidi" / "prompts" / "manifest.json"
    assert PromptStore(manifest_path).get("stage_1_user_benchmark")
    assert PromptStore(manifest_path).get("stage_1_user_inference")
    assert prompt_store_module._materialize_zip_resource_prompts() == manifest_path
