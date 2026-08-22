"""Repository-level contracts for canonical assets and supported tooling."""

from __future__ import annotations

from pathlib import Path
import tomllib

from mudidi.llm.prompt_store import default_prompts_path


ROOT = Path(__file__).resolve().parents[2]


def _project_config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def _dependency_names(dependencies: list[str]) -> set[str]:
    return {
        dependency.split("[", 1)[0]
        .split(">", 1)[0]
        .split("=", 1)[0]
        .split("<", 1)[0]
        .lower()
        for dependency in dependencies
    }


def test_packaged_prompt_is_the_only_canonical_prompt_asset() -> None:
    canonical = ROOT / "src" / "mudidi" / "assets" / "prompts" / "manifest.json"

    assert canonical.is_file()
    assert not (ROOT / "src" / "mudidi" / "assets" / "PROMPT.json").exists()
    assert not (ROOT / "assets" / "PROMPT.json").exists()
    assert default_prompts_path().resolve() == canonical.resolve()


def test_retired_root_artifacts_are_absent() -> None:
    assert not (ROOT / "requirements.txt").exists()
    assert not (ROOT / "subs.txt").exists()


def test_retired_mkdocs_material_assets_are_absent() -> None:
    retired = (
        ROOT / "docs" / "javascripts",
        ROOT / "docs" / "stylesheets",
        ROOT / "docs" / "overrides",
    )

    assert all(not path.exists() for path in retired)


def test_dependencies_match_runtime_and_documentation_contracts() -> None:
    config = _project_config()
    runtime = _dependency_names(config["project"]["dependencies"])
    docs = _dependency_names(config["project"]["optional-dependencies"]["docs"])

    assert "numpy" in runtime
    assert runtime.isdisjoint({"pandas", "scikit-learn", "sacrebleu"})
    assert {"mkdocs", "mkdocstrings", "pymdown-extensions"} <= docs
    assert "mkdocs-material" not in docs


def test_annotation_tests_are_part_of_the_default_suite() -> None:
    config = _project_config()
    pytest_config = config["tool"]["pytest"]["ini_options"]

    assert pytest_config["testpaths"] == [
        "tests",
        "annotation/tests",
    ]
    assert "--import-mode=importlib" in pytest_config["addopts"]


def test_evaluation_provenance_is_documented() -> None:
    readme = ROOT / "evaluations" / "README.md"

    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "agentic" in text.lower()
    assert "stage1_flat_per_lang_script_eval" in text
