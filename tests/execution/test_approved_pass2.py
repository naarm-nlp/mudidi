"""Tests proving web Pass 2 consumes loaded approved rules, not a path."""

from __future__ import annotations

from pathlib import Path

from mudidi.cli.run import execute_extraction_config
from mudidi.config.yaml_config import InferenceConfig
from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet, MarkerLine


def _approved_rules() -> DictionaryMarkerCheatsheet:
    return DictionaryMarkerCheatsheet(
        markers=[MarkerLine(marker="lx", description="Headword")],
        rules=["Use only approved rules."],
    )


def test_strategy_prefers_loaded_approved_rules_over_mutable_paths(tmp_path: Path) -> None:
    untrusted = tmp_path / "untrusted.json"
    untrusted.write_text("not valid JSON", encoding="utf-8")
    approved = _approved_rules()
    strategy = TwoStageLLMExtraction(
        stage2_experiment_dir=str(tmp_path),
        parse_rules_file=str(untrusted),
        approved_parse_rules=approved,
    )

    field_map, usage = strategy._ensure_field_map("", "", run_stage="2-pass-2")

    assert field_map is approved
    assert usage == {}


def test_typed_execution_boundary_forwards_loaded_rules_without_serializing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    captured: dict[str, object] = {}

    def fake_extract(*, resolved_args: object) -> int:
        captured["approved"] = getattr(resolved_args, "approved_parse_rules")
        return 0

    monkeypatch.setattr("mudidi.cli.extract.main", fake_extract)
    monkeypatch.setattr("mudidi.cli.run._write_resolved_config", lambda _config: None)
    approved = _approved_rules()
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": tmp_path / "output"},
            "pipeline": {"stage": "2-pass-2"},
        }
    )

    result = execute_extraction_config(config, approved_parse_rules=approved)

    assert result == 0
    assert captured["approved"] is approved
