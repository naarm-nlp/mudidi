"""Integration tests for the single-worker subprocess job controller."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mudidi.web.jobs import JobController
from mudidi.web.runs import RunStatus, RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "mudidi-web.sqlite3")


def _queued_run(store: RunStore, run_id: str) -> None:
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)


def test_fake_subprocess_persists_progress_and_completes(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "run-1")
    controller = JobController(store=store, data_dir=tmp_path)

    controller.start_fake("run-1", page_count=3, delay_seconds=0)
    controller.wait("run-1", timeout=5)

    assert store.get_run("run-1").status is RunStatus.COMPLETED
    events = store.list_events("run-1")
    assert [event["type"] for event in events] == [
        "stage.started",
        "page.started",
        "page.completed",
        "page.started",
        "page.completed",
        "page.started",
        "page.completed",
        "run.completed",
    ]
    assert [event["sequence"] for event in events] == list(range(1, 9))


def test_worker_failure_records_terminal_event_and_failed_state(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "run-fail")
    controller = JobController(store=store, data_dir=tmp_path)

    controller.start_fake("run-fail", page_count=1, fail=True, delay_seconds=0)
    controller.wait("run-fail", timeout=5)

    assert store.get_run("run-fail").status is RunStatus.FAILED
    assert store.list_events("run-fail")[-1]["type"] == "run.failed"


def test_cancel_terminates_owned_worker_and_preserves_cancelled_state(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "run-cancel")
    controller = JobController(store=store, data_dir=tmp_path)
    controller.start_fake("run-cancel", page_count=100, delay_seconds=0.05)
    _wait_for_status(store, "run-cancel", RunStatus.RUNNING_STAGE1)

    controller.cancel("run-cancel")
    controller.wait("run-cancel", timeout=5)

    assert store.get_run("run-cancel").status is RunStatus.CANCELLED


def test_second_worker_is_rejected_while_first_is_active(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "run-1")
    _queued_run(store, "run-2")
    controller = JobController(store=store, data_dir=tmp_path)
    controller.start_fake("run-1", page_count=100, delay_seconds=0.05)
    _wait_for_status(store, "run-1", RunStatus.RUNNING_STAGE1)

    with pytest.raises(RuntimeError, match="active"):
        controller.start_fake("run-2", page_count=1, delay_seconds=0)

    controller.cancel("run-1")
    controller.wait("run-1", timeout=5)


def test_worker_command_never_contains_api_credentials(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "run-1")
    controller = JobController(store=store, data_dir=tmp_path)

    controller.start_fake("run-1", page_count=1, delay_seconds=0)
    command = controller.command_for("run-1")
    controller.wait("run-1", timeout=5)

    assert "API_KEY" not in " ".join(command)
    assert "secret" not in " ".join(command).lower()


def test_startup_reconciliation_interrupts_unowned_active_run(
    store: RunStore,
    tmp_path: Path,
) -> None:
    _queued_run(store, "orphan")
    store.transition("orphan", RunStatus.RUNNING_STAGE1)
    controller = JobController(store=store, data_dir=tmp_path)

    reconciled = controller.reconcile_startup()

    assert reconciled == ["orphan"]
    assert store.get_run("orphan").status is RunStatus.INTERRUPTED


def _wait_for_status(
    store: RunStore,
    run_id: str,
    expected: RunStatus,
    timeout: float = 3,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get_run(run_id).status is expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected}")
