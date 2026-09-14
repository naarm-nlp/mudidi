"""Offline integration tests for the real staged production worker path."""

from __future__ import annotations

from collections.abc import Iterator
import base64
import io
import json
import os
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

from pydantic import SecretStr
import pytest

from mudidi.config.yaml_config import AuthConfig, InferenceConfig
from mudidi.llm.client import resolve_subscription_runtime
from mudidi.llm.subscriptions import SubscriptionCredential, SubscriptionProvider
from mudidi.llm.subscriptions.storage import SubscriptionStore
from mudidi.web import production_worker
from mudidi.web.credentials import CredentialVault
from mudidi.web.jobs import JobController, _OwnedWorker
from mudidi.web.models import Provider
from mudidi.web.parse_rules import ParseRuleReviewService, ReviewStatus
from mudidi.web.runs import RunStatus, RunStore


_ONE_BY_ONE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _config(
    tmp_path: Path,
    *,
    stage: str = "all",
    auth: AuthConfig | None = None,
    model: str = "anthropic/claude-sonnet-5",
) -> InferenceConfig:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    (pages / "page_1.png").write_bytes(base64.b64decode(_ONE_BY_ONE_PNG))
    values: dict[str, object] = {
        "input": {"pages": pages},
        "output": {"directory": tmp_path / "output"},
        "pipeline": {
            "stage": stage,
            "parse_rules_pages": ["1"],
        },
        "models": {"default": model},
    }
    if auth is not None:
        values["auth"] = auth.model_dump(mode="json")
    return InferenceConfig.model_validate(values)


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


def test_stage_two_pass_one_start_marks_discovery_in_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _reviews, controller = _controller(tmp_path)
    run_id = "discovery-progress"
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    statuses_during_append: list[RunStatus] = []
    append_event = store.append_event

    def record_status_then_append(
        observed_run_id: str,
        event: dict[str, object],
    ) -> None:
        statuses_during_append.append(store.get_run(observed_run_id).status)
        append_event(observed_run_id, event)

    monkeypatch.setattr(store, "append_event", record_status_then_append)
    store.transition(run_id, RunStatus.RUNNING_STAGE1)
    stage_event_processed = Event()
    release_worker = Event()

    def output_lines() -> Iterator[str]:
        yield json.dumps(
            {
                "version": 1,
                "type": "stage.started",
                "run_id": run_id,
                "sequence": 1,
                "occurred_at": "2026-09-12T00:00:00Z",
                "stage": "stage2_pass1",
            }
        )
        stage_event_processed.set()
        release_worker.wait(timeout=2)

    class GateProcess:
        stdout = output_lines()

        @staticmethod
        def wait() -> int:
            return 0

    worker = _OwnedWorker(
        process=cast(Any, GateProcess()),
        command=("fake-worker",),
    )
    monitor = Thread(target=controller._monitor, args=(run_id, worker))
    monitor.start()

    assert stage_event_processed.wait(timeout=2)
    assert store.get_run(run_id).status is RunStatus.DISCOVERING_PARSE_RULES
    assert statuses_during_append == [RunStatus.DISCOVERING_PARSE_RULES]

    release_worker.set()
    monitor.join(timeout=2)
    assert not monitor.is_alive()
    assert store.get_run(run_id).status is RunStatus.FAILED


def test_pdf_page_count_uses_selected_dictionary_range(tmp_path: Path) -> None:
    pdf = tmp_path / "dictionary.pdf"
    pdf.touch()

    assert production_worker._page_count(pdf, "10-12,15") == 4


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
        credentials=(_credential(),),
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
        credentials=(_credential(),),
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
        credentials=(_credential(),),
        offline_executor=True,
    )
    controller.wait("stage1-only", timeout=10)

    assert store.get_run("stage1-only").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-1/page_1/page_1_stage1_flat.txt").is_file()


def test_subscription_production_worker_uses_descriptor_only_popen_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _reviews, controller = _controller(tmp_path)
    secret_values = {
        "popen-api-secret",
        "popen-token-secret",
    }
    monkeypatch.setenv("OPENAI_API_KEY", "popen-api-secret")
    monkeypatch.setenv("HF_TOKEN", "popen-token-secret")
    config = _config(
        tmp_path,
        stage="1",
        auth=AuthConfig(mode="subscription", providers=("openai",)),
        model="openai/gpt-5.6-terra",
    )
    controller.prepare_inference(
        "subscription-worker",
        config=config,
        provider=Provider.OPENAI,
    )

    controller.start_inference(
        "subscription-worker",
        credentials=(),
        offline_executor=True,
    )
    controller.wait("subscription-worker", timeout=10)

    assert store.get_run("subscription-worker").status is RunStatus.COMPLETED
    assert store.list_events("subscription-worker")[-1]["type"] == "run.completed"
    command = controller.command_for("subscription-worker")
    assert "--subscription-store" in command
    assert all(secret not in command for secret in secret_values)
    event_text = json.dumps(store.list_events("subscription-worker"))
    assert all(secret not in event_text for secret in secret_values)
    log_text = controller.log_path("subscription-worker").read_text(encoding="utf-8")
    assert all(secret not in log_text for secret in secret_values)
    process = controller._workers["subscription-worker"].process
    stderr = process.stderr.read() if process.stderr is not None else ""
    assert all(secret not in stderr for secret in secret_values)


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        (SubscriptionProvider.OPENAI, "openai/gpt-5.6-terra"),
        (SubscriptionProvider.GOOGLE, "gemini/gemini-3.1-pro-low"),
    ],
)
def test_production_worker_resolves_subscription_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: SubscriptionProvider,
    model: str,
) -> None:

    store_path = tmp_path / "subscriptions"
    secret = "seeded-subscription-access-secret"
    SubscriptionStore(store_path).save(
        SubscriptionCredential(
            provider=provider,
            account_label="seeded account",
            access_token=SecretStr(secret),
            project_id="seeded-project"
            if provider is SubscriptionProvider.GOOGLE
            else None,
        )
    )
    monkeypatch.setenv("MUDIDI_ANTIGRAVITY_CLI", "/must-not-be-forwarded")
    config = _config(
        tmp_path,
        stage="1",
        auth=AuthConfig(mode="subscription", providers=(provider.value,)),
        model=model,
    )
    config_path = tmp_path / "runs" / "runtime-boundary" / "resolved_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    observed: dict[str, object] = {}

    def resolve_and_execute(
        phase_config: InferenceConfig,
        *,
        approved_parse_rules: object | None = None,
        progress_callback: object | None = None,
    ) -> int:
        runtime = resolve_subscription_runtime(phase_config.auth)
        assert runtime is not None
        status = runtime.backend.status()
        observed["store_path"] = os.environ["MUDIDI_SUBSCRIPTION_STORE"]
        observed["provider"] = runtime.provider
        observed["authenticated"] = status.authenticated
        observed["antigravity_cli"] = os.environ.get("MUDIDI_ANTIGRAVITY_CLI")
        return production_worker._offline_execute(
            phase_config,
            approved_parse_rules=approved_parse_rules,
            progress_callback=progress_callback,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        production_worker,
        "execute_extraction_config",
        resolve_and_execute,
    )
    monkeypatch.setattr(
        production_worker.sys,
        "stdin",
        io.StringIO(
            json.dumps({"auth_mode": "subscription", "providers": [provider.value]})
            + "\n"
        ),
    )

    result = production_worker.main(
        [
            "--run-id",
            "runtime-boundary",
            "--config",
            str(config_path),
            "--phase",
            "stage1",
            "--log-file",
            str(tmp_path / "worker.log"),
            "--subscription-store",
            str(store_path),
        ]
    )

    assert result == 0
    expected = {
        "store_path": str(store_path),
        "provider": provider,
        "authenticated": True,
        "antigravity_cli": None,
    }
    assert observed == expected
    output = capsys.readouterr().out
    assert secret not in output
    assert secret not in (tmp_path / "worker.log").read_text(encoding="utf-8")
    assert (
        tmp_path / "output" / "stage-1" / "page_1" / "page_1_stage1_flat.txt"
    ).is_file()


def test_uploaded_guide_complete_run_executes_without_review(tmp_path: Path) -> None:
    store, reviews, controller = _controller(tmp_path)
    config = _config(tmp_path)
    guide = tmp_path / "uploaded-guide.json"
    guide.write_text(
        json.dumps(
            {
                "markers": [{"marker": "lx", "description": "Headword"}],
                "rules": [],
                "abbreviations": {},
            }
        ),
        encoding="utf-8",
    )
    config = config.model_copy(
        update={
            "pipeline": config.pipeline.model_copy(update={"parse_rules_file": guide})
        }
    )
    controller.prepare_inference(
        "uploaded-guide",
        config=config,
        provider=Provider.ANTHROPIC,
    )

    controller.start_inference(
        "uploaded-guide",
        credentials=(_credential(),),
        offline_executor=True,
    )
    controller.wait("uploaded-guide", timeout=10)

    assert store.get_run("uploaded-guide").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-1/page_1/page_1_stage1_flat.txt").is_file()
    assert (tmp_path / "output/stage-2/page_1/page_1_mdf.txt").is_file()
    try:
        reviews.get("uploaded-guide")
    except KeyError:
        pass
    else:
        raise AssertionError("user-uploaded guides must not create a review checkpoint")


def test_interrupted_uploaded_guide_stage2_resumes_without_approval(
    tmp_path: Path,
) -> None:
    store, _reviews, controller = _controller(tmp_path)
    config = _config(tmp_path, stage="2")
    guide = tmp_path / "uploaded-guide.json"
    guide.write_text(
        '{"markers":[{"marker":"lx","description":"Headword"}]}',
        encoding="utf-8",
    )
    config = config.model_copy(
        update={
            "pipeline": config.pipeline.model_copy(update={"parse_rules_file": guide})
        }
    )
    controller.prepare_inference(
        "resume-uploaded-guide",
        config=config,
        provider=Provider.ANTHROPIC,
    )
    store.transition("resume-uploaded-guide", RunStatus.QUEUED)
    store.start_uploaded_guide_stage2("resume-uploaded-guide")
    store.interrupt("resume-uploaded-guide")

    controller.resume_inference(
        "resume-uploaded-guide",
        credentials=(_credential(),),
        offline_executor=True,
    )
    controller.wait("resume-uploaded-guide", timeout=10)

    assert store.get_run("resume-uploaded-guide").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-2/page_1/page_1_mdf.txt").is_file()


def test_reconciled_parse_rule_discovery_resumes_pass1_without_restarting_stage1(
    tmp_path: Path,
) -> None:
    store, _reviews, controller = _controller(tmp_path)
    controller.prepare_inference(
        "resume-parse-review",
        config=_config(tmp_path),
        provider=Provider.ANTHROPIC,
    )
    store.transition("resume-parse-review", RunStatus.QUEUED)
    store.transition("resume-parse-review", RunStatus.DISCOVERING_PARSE_RULES)
    store.append_event(
        "resume-parse-review",
        {
            "version": 1,
            "type": "stage.started",
            "run_id": "resume-parse-review",
            "sequence": 1,
            "occurred_at": "2026-07-14T00:00:00+00:00",
            "stage": "stage1",
        },
    )
    store.append_event(
        "resume-parse-review",
        {
            "version": 1,
            "type": "stage.started",
            "run_id": "resume-parse-review",
            "sequence": 2,
            "occurred_at": "2026-07-14T00:00:01+00:00",
            "stage": "stage2_pass1",
        },
    )
    assert controller.reconcile_startup() == ["resume-parse-review"]

    controller.resume_inference(
        "resume-parse-review",
        credentials=(_credential(),),
        offline_executor=True,
    )

    controller.wait("resume-parse-review", timeout=10)
    events = store.list_events("resume-parse-review")
    command = controller.command_for("resume-parse-review")

    assert (
        store.get_run("resume-parse-review").status
        is RunStatus.AWAITING_PARSE_RULES_REVIEW
    )
    assert command[command.index("--phase") + 1] == "pass1"
    assert (
        sum(
            event["type"] == "stage.started" and event["stage"] == "stage1"
            for event in events
        )
        == 1
    )
    assert any(event["type"] == "parse_rules.generated" for event in events)


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
        credentials=(_credential(),),
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


def test_interrupted_approved_pass2_resumes_from_authenticated_snapshot(
    tmp_path: Path,
) -> None:
    store, reviews, controller = _controller(tmp_path)
    controller.prepare_inference(
        "resume-pass2",
        config=_config(tmp_path),
        provider=Provider.ANTHROPIC,
    )
    controller.start_inference(
        "resume-pass2",
        credentials=(_credential(),),
        offline_executor=True,
    )
    controller.wait("resume-pass2", timeout=10)
    reviews.approve("resume-pass2")
    stage1_events_before = [
        event
        for event in store.list_events("resume-pass2")
        if event.get("stage") == "stage1"
    ]
    store.interrupt("resume-pass2")

    controller.resume_inference(
        "resume-pass2",
        credentials=(_credential(),),
        offline_executor=True,
    )
    controller.wait("resume-pass2", timeout=10)

    assert store.get_run("resume-pass2").status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-2/page_1/page_1_mdf.txt").is_file()
    stage1_events_after = [
        event
        for event in store.list_events("resume-pass2")
        if event.get("stage") == "stage1"
    ]
    assert stage1_events_after == stage1_events_before


def test_production_failure_uses_sequence_after_phase_setup(
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
    assert [event["type"] for event in events] == ["run.failed"]
    assert [event["sequence"] for event in events] == [8]
