"""Tests for durable local-web run state and authorization metadata."""

from __future__ import annotations

from pathlib import Path

import pytest

from mudidi.web.runs import (
    ActiveRunExistsError,
    InvalidRunTransition,
    RunStatus,
    RunStore,
)


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "mudidi-web.sqlite3")


def test_generic_transition_cannot_bypass_parse_rule_review(store: RunStore) -> None:
    store.create_run("run-1")
    store.transition("run-1", RunStatus.VALIDATED)
    store.transition("run-1", RunStatus.QUEUED)

    with pytest.raises(InvalidRunTransition):
        store.transition("run-1", RunStatus.RUNNING_STAGE2)


def test_pass2_start_requires_review_state_and_records_approval(store: RunStore) -> None:
    store.create_run("run-1")
    store.transition("run-1", RunStatus.VALIDATED)
    store.transition("run-1", RunStatus.QUEUED)
    store.transition("run-1", RunStatus.DISCOVERING_PARSE_RULES)
    store.transition("run-1", RunStatus.AWAITING_PARSE_RULES_REVIEW)

    run = store.authorize_pass2(
        "run-1",
        review_id="review-3",
        approval_digest="a" * 64,
    )

    assert run.status is RunStatus.RUNNING_STAGE2
    assert run.review_id == "review-3"
    assert run.approval_digest == "a" * 64
    assert run.resume_phase == "stage2_pass2"


def test_pass2_authorization_is_rejected_from_queued_state(store: RunStore) -> None:
    store.create_run("run-1")
    store.transition("run-1", RunStatus.VALIDATED)
    store.transition("run-1", RunStatus.QUEUED)

    with pytest.raises(InvalidRunTransition):
        store.authorize_pass2(
            "run-1",
            review_id="review-1",
            approval_digest="b" * 64,
        )


def test_interrupted_preapproval_run_resumes_to_review(store: RunStore) -> None:
    store.create_run("run-1")
    store.transition("run-1", RunStatus.VALIDATED)
    store.transition("run-1", RunStatus.QUEUED)
    store.transition("run-1", RunStatus.DISCOVERING_PARSE_RULES)
    store.interrupt("run-1")

    run = store.resume("run-1", credentials_available=True)

    assert run.status is RunStatus.AWAITING_PARSE_RULES_REVIEW


def test_resume_requires_temporary_credentials(store: RunStore) -> None:
    store.create_run("run-1", provider="anthropic")
    store.transition("run-1", RunStatus.VALIDATED)
    store.transition("run-1", RunStatus.QUEUED)
    store.transition("run-1", RunStatus.RUNNING_STAGE1)
    store.interrupt("run-1")

    run = store.resume("run-1", credentials_available=False)

    assert run.status is RunStatus.CREDENTIALS_REQUIRED
    assert run.provider == "anthropic"


def test_database_enforces_one_active_run(store: RunStore) -> None:
    for run_id in ("run-1", "run-2"):
        store.create_run(run_id)
        store.transition(run_id, RunStatus.VALIDATED)
        store.transition(run_id, RunStatus.QUEUED)

    store.transition("run-1", RunStatus.RUNNING_STAGE1)

    with pytest.raises(ActiveRunExistsError):
        store.transition("run-2", RunStatus.RUNNING_STAGE1)


def test_api_keys_are_not_columns_or_serialized_values(store: RunStore) -> None:
    store.create_run("run-1", provider="openai")

    columns = store.schema_columns("runs")
    run = store.get_run("run-1")

    assert not any("key" in column or "secret" in column for column in columns)
    assert "sk-test-secret" not in repr(run)
