"""Tests for manifest-backed prompt templates."""

from __future__ import annotations

import hashlib
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


EXPECTED_PROMPT_SHA256 = {
    "stage_1_system_benchmark": "461a4dd48f6cdced298e32e9f448994aba1fd41238b8e8bf707505fba2e79a7c",
    "stage_1_system_inference": "2f84adac1fbd07adc4b55d101f3203219a8bd5fadc05471e31d9d2b1a35093a9",
    "stage_1_column_system": "f421cf7c6d4d44f412a0584920752cb0ccc9fb241a7b3c227868fedc4aee82fa",
    "stage_1_user_alphabet": "db00fb25b0c84b1d15cae70cccadf1101039591b954677897c2f225b7eea8447",
    "stage_1_user_ocr_reference": "901c167bb7676517cebb84e78c5c6a41f2922d1db79f590df4b10a4abe361fd1",
    "stage_1_user_closing": "3589b298538ddf5c23c8ea521621ee564f264b615ee6e030929a37a5c03d3aed",
    "stage_1_neighbor_context": "f7daa871de07dd43379f02c0436c8896e9eb949c26b2823bc5b8ae61df85b4a0",
    "stage_1_typography_instruction": "7476af1dff1cd501782363e1d081a4318eaad4c6a6416fb84199a2e76ab6bb18",
    "stage_2_pass_1_system": "d062fe1c778846c57e27346233b1d3c9fb013af915f5fd2f735de36b092969f9",
    "stage_2_pass_1_user_single": "764f6b056f085f089bb25291efc0cfc87a1b630ac07443a438476143bc57226e",
    "stage_2_pass_1_user_multi": "742e52de744dd3634e6903582b088612c13ceec05dfcbed763319a87fe175b71",
    "stage_2_pass_2_system_benchmark": "2897d430cd34c110c2842e6a7476495d9cfdfd6b70e8f4b22aa3640af25b03ef",
    "stage_2_pass_2_system_inference": "6fec6cd690c11e66804633a023409afe17fd79f575509d15e9f08f49f2960925",
    "stage_2_pass_2_user_benchmark": "49ccc67e97caaecf3cbd97163af46bb458190cfea7a8988c5fc7c9fdc7ab2044",
    "stage_2_pass_2_user_inference": "239fa63c0b1898cd76edbcf23bcca2fb303a27abde94379682eea7d79772eec9",
    "page_boundary_rules": "6e8d90339bd3bb15b84e797812e248017577aaa793a249b6cd25e5968d4cbafe",
    "mdf_marker_reference": "11f28f590b121fc6c3327664154dc2a3b14c9b25ce4ef11bd5eacebfab348661",
    "stage_2_toolbox_pdf_section": "5ed75f53400d17145dde8ac19d214220009d4ed49e883801676b10fe6695ca07",
    "stage_2_toolbox_text_section": "dcb9c6237878e9bcf50893355d5f9d3734d0b842d84b3b03b4db5f293faaab67",
}

EXPECTED_PROMPT_ORDER = list(EXPECTED_PROMPT_SHA256)


def _write_manifest(path: Path, entries: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def test_default_prompt_source_is_manifest_with_external_templates() -> None:
    manifest_path = default_prompts_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path.name == "manifest.json"
    assert manifest_path.parent.name == "prompts"
    assert set(manifest) == set(EXPECTED_PROMPT_SHA256)
    for entry in manifest.values():
        assert "file" in entry
        assert "prompt" not in entry
        assert (manifest_path.parent / entry["file"]).is_file()


def test_manifest_groups_primary_stage_prompts_before_supporting_blocks() -> None:
    manifest = json.loads(default_prompts_path().read_text(encoding="utf-8"))

    assert list(manifest) == EXPECTED_PROMPT_ORDER


def test_manifest_templates_preserve_existing_prompt_text() -> None:
    store = get_prompt_store()

    actual = {
        prompt_id: hashlib.sha256(store.get(prompt_id).encode()).hexdigest()
        for prompt_id in store.prompt_ids()
    }

    assert actual == EXPECTED_PROMPT_SHA256


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
    assert PromptStore(manifest_path).get("stage_1_user_closing")
    assert prompt_store_module._materialize_zip_resource_prompts() == manifest_path
