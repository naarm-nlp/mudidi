"""Offline integration tests for the real staged production worker path."""

from __future__ import annotations

from pathlib import Path
import json

from mudidi.config.yaml_config import InferenceConfig
from mudidi.web.credentials import CredentialVault
from mudidi.web.jobs import JobController
from mudidi.web.models import Provider
from mudidi.web.parse_rules import ParseRuleReviewService, ReviewStatus
from mudidi.web.runs import RunStatus, RunStore
from mudidi.web import production_worker


def _config(tmp_path: Path, *, stage: str = "all") -> InferenceConfig:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    (pages / "page_1.png").write_bytes(b"offline image fixture")
    return InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": tmp_path / "output"},
            "pipeline": {
                "stage": stage,
                "parse_rules_pages": ["1"],
            },
            "models": {"default": "anthropic/claude-sonnet-5"},
        }
    )


def _controller(
    tmp_path: Path,
) -> tuple[RunStore, ParseRuleReviewService, JobController]:
    store = RunStore(tmp_path / "mudidi-web.sqlite3")
    reviews = ParseRuleReviewService(store=store, data_dir=tmp_path)
    controller = JobController(
        store=store,
        data_dir=tmp_path,
        parse_rule_reviews=reviews,
    )
    return store, reviews, controller


def _credential() -> object:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-never-persist")
    return vault.resolve(Provider.ANTHROPIC)


def test_complete_production_path_pauses_then_resumes_approved_pass2(
    tmp_path: Path,
) -> None:
    store, reviews, controller = _controller(tmp_path)
    config = _config(tmp_path)
    controller.prepare_inference(
        "production-1",
        config=config,
        provider=Provider.ANTHROPIC,
    )

    controller.start_inference(
        "production-1",
        credential=_credential(),
        offline_executor=True,
    )
    controller.wait("production-1", timeout=10)

    review = reviews.get("production-1")
    assert review.status is ReviewStatus.AWAITING_REVIEW
    assert store.get_run("production-1").status is RunStatus.AWAITING_PARSE_RULES_REVIEW

    approval = reviews.approve("production-1")
    controller.start_pass2(
        "production-1",
        approval=approval,
        credential=_credential(),
        offline_executor=True,
    )
    controller.wait("production-1", timeout=10)

    assert store.get_run("production-1").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-2/page_1/page_1_mdf.txt").is_file()
    assert store.list_events("production-1")[-1]["type"] == "run.completed"


def test_stage1_only_production_path_completes_without_review(tmp_path: Path) -> None:
    store, reviews, controller = _controller(tmp_path)
    del reviews
    controller.prepare_inference(
        "stage1-only",
        config=_config(tmp_path, stage="1"),
        provider=Provider.ANTHROPIC,
    )

    controller.start_inference(
        "stage1-only",
        credential=_credential(),
        offline_executor=True,
    )
    controller.wait("stage1-only", timeout=10)

    assert store.get_run("stage1-only").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-1/page_1/page_1_stage1_flat.txt").is_file()


def test_prepared_config_and_worker_command_never_contain_temporary_key(
    tmp_path: Path,
) -> None:
    _store, _reviews, controller = _controller(tmp_path)
    controller.prepare_inference(
        "secret-check",
        config=_config(tmp_path, stage="1"),
        provider=Provider.ANTHROPIC,
    )
    controller.start_inference(
        "secret-check",
        credential=_credential(),
        offline_executor=True,
    )
    command = controller.command_for("secret-check")
    controller.wait("secret-check", timeout=10)

    config_text = controller.config_path("secret-check").read_text(encoding="utf-8")
    assert "sk-ant-never-persist" not in config_text
    assert "sk-ant-never-persist" not in " ".join(command)
    assert "api_key" not in config_text.lower()


def test_direct_pass2_cannot_be_prepared_from_browser_config(tmp_path: Path) -> None:
    _store, _reviews, controller = _controller(tmp_path)

    try:
        controller.prepare_inference(
            "unsafe-pass2",
            config=_config(tmp_path, stage="2-pass-2"),
            provider=Provider.ANTHROPIC,
        )
    except ValueError as exc:
        assert "Pass 2" in str(exc)
    else:
        raise AssertionError("direct web Pass 2 preparation must be rejected")


def test_production_failure_uses_sequence_after_stage_started(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        _config(tmp_path, stage="1").model_dump_json(), encoding="utf-8"
    )

    def fail_phase(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("offline failure")

    monkeypatch.setattr(production_worker, "run_inference_phase", fail_phase)  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdin.readline", lambda: "{}\n")  # type: ignore[attr-defined]

    result = production_worker.main(
        [
            "--run-id",
            "failed-production",
            "--config",
            str(config_path),
            "--phase",
            "stage1",
            "--sequence-start",
            "7",
            "--log-file",
            str(tmp_path / "worker.log"),
        ]
    )

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]  # type: ignore[attr-defined]
    assert result == 1
    assert [event["type"] for event in events] == ["stage.started", "run.failed"]
    assert [event["sequence"] for event in events] == [8, 9]
