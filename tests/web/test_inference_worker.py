"""Tests for production inference phase decomposition and secret handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mudidi.config.yaml_config import InferenceConfig
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet, MarkerLine
from mudidi.web.inference_worker import (
    InferencePhase,
    apply_credential_message,
    phase_configs,
    run_inference_phase,
)


@pytest.fixture
def config(tmp_path: Path) -> InferenceConfig:
    pages = tmp_path / "pages"
    pages.mkdir()
    return InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": tmp_path / "output"},
            "pipeline": {"stage": "all"},
        }
    )


def _approved() -> DictionaryMarkerCheatsheet:
    return DictionaryMarkerCheatsheet(
        dictionary_name="Approved",
        markers=[MarkerLine(marker="lx", description="Headword")],
    )


def test_complete_web_run_is_decomposed_before_human_review(
    config: InferenceConfig,
) -> None:
    phases = phase_configs(config, InferencePhase.STAGE1_THEN_PASS1)

    assert [phase.pipeline.stage for phase in phases] == ["1", "2-pass-1"]
    assert all(phase.pipeline.stage != "all" for phase in phases)
    assert all(phase.pipeline.stage != "2-pass-2" for phase in phases)


def test_user_guide_run_skips_pass1_and_review(config: InferenceConfig) -> None:
    executed: list[tuple[str, object]] = []

    def execute(
        phase: InferenceConfig, *, approved_parse_rules: object = None
    ) -> int:
        executed.append((phase.pipeline.stage, approved_parse_rules))
        return 0

    result = run_inference_phase(
        config,
        InferencePhase.USER_GUIDE,
        execute=execute,
        approved_rules=None,
    )

    assert result.return_code == 0
    assert result.parse_rules_path is None
    assert executed == [("1", None), ("2", None)]


def test_stage2_pass2_requires_loaded_approved_rules(config: InferenceConfig) -> None:
    with pytest.raises(ValueError, match="approved"):
        run_inference_phase(
            config,
            InferencePhase.PASS2,
            execute=lambda *_args, **_kwargs: 0,
            approved_rules=None,
        )


def test_pass2_forwards_same_loaded_rule_object(config: InferenceConfig) -> None:
    approved = _approved()
    captured: list[object] = []

    def execute(
        _config: InferenceConfig, *, approved_parse_rules: object = None
    ) -> int:
        captured.append(approved_parse_rules)
        return 0

    result = run_inference_phase(
        config,
        InferencePhase.PASS2,
        execute=execute,
        approved_rules=approved,
    )

    assert result.return_code == 0
    assert captured == [approved]
    assert result.parse_rules_path is None


def test_pass1_reports_managed_output_parse_rules_path(
    config: InferenceConfig,
    tmp_path: Path,
) -> None:
    executed: list[str] = []

    def execute(phase: InferenceConfig, *, approved_parse_rules: object = None) -> int:
        del approved_parse_rules
        executed.append(phase.pipeline.stage)
        path = phase.output.directory / "stage-2" / "parse-rules.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return 0

    result = run_inference_phase(
        config,
        InferencePhase.PASS1,
        execute=execute,
        approved_rules=None,
    )

    assert result.return_code == 0
    assert executed == ["2-pass-1"]
    assert result.parse_rules_path == tmp_path / "output/stage-2/parse-rules.json"


def test_credential_message_sets_only_selected_environment_variable() -> None:
    environ: dict[str, str] = {}
    message = json.dumps(
        {"environment_name": "ANTHROPIC_API_KEY", "api_key": "sk-ant-secret"}
    )

    apply_credential_message(message, environ=environ)

    assert environ == {"ANTHROPIC_API_KEY": "sk-ant-secret"}


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        json.dumps({"environment_name": "PATH", "api_key": "secret"}),
        json.dumps({"environment_name": "OPENAI_API_KEY", "api_key": ""}),
    ],
)
def test_credential_message_rejects_malformed_or_unapproved_keys(message: str) -> None:
    with pytest.raises(ValueError, match="credential"):
        apply_credential_message(message, environ={})
