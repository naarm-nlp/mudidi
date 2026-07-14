"""HTTP integration tests for offline run, progress, and history screens."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.runs import RunStatus


def _event(
    run_id: str,
    sequence: int,
    event_type: str,
    stage: str,
    **details: object,
) -> dict[str, object]:
    return {
        "version": 1,
        "type": event_type,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime(2026, 7, 14, tzinfo=UTC).isoformat(),
        "stage": stage,
        **details,
    }


def test_offline_demo_runs_to_completion_and_appears_in_history(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    started = client.post(
        "/runs/demo",
        data={"page_count": "2"},
        follow_redirects=False,
    )

    assert started.status_code == 303
    location = started.headers["location"]
    run_id = location.rsplit("/", 1)[-1]
    app.state.job_controller.wait(run_id, timeout=5)

    detail = client.get(location)
    history = client.get("/history")
    assert detail.status_code == 200
    assert "Completed" in detail.text
    assert "2 of 2 pages" in detail.text
    assert history.status_code == 200
    assert run_id in history.text
    assert "Completed" in history.text


def test_event_stream_replays_persisted_events_as_sse(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    started = client.post("/runs/demo", data={"page_count": "1"})
    run_id = started.url.path.rsplit("/", 1)[-1]
    app.state.job_controller.wait(run_id, timeout=5)

    response = client.get(f"/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: stage.started" in response.text
    assert "event: run.completed" in response.text
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert json.loads(data_lines[-1])["type"] == "run.completed"


def test_event_stream_can_start_after_the_latest_persisted_event(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    started = client.post("/runs/demo", data={"page_count": "1"})
    run_id = started.url.path.rsplit("/", 1)[-1]
    app.state.job_controller.wait(run_id, timeout=5)
    latest = app.state.run_store.list_events(run_id)[-1]["sequence"]

    response = client.get(f"/runs/{run_id}/events?after={latest}")

    assert response.status_code == 200
    assert response.text == ""


def test_active_page_links_to_running_job_and_cancel_route(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    started = client.post(
        "/runs/demo",
        data={"page_count": "100", "delay_seconds": "0.05"},
    )
    run_id = started.url.path.rsplit("/", 1)[-1]

    active = client.get("/active")
    cancelled = client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
    app.state.job_controller.wait(run_id, timeout=5)

    assert active.status_code == 200
    assert run_id in active.text
    assert cancelled.status_code == 303
    assert app.state.run_store.get_run(run_id).status is RunStatus.CANCELLED


def test_run_overview_names_pipeline_phases_and_current_page(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    run_id = "progress-details"
    store = app.state.run_store
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.start_uploaded_guide_stage2(run_id)
    store.append_event(run_id, _event(run_id, 1, "stage.started", "stage1", total_pages=2))
    store.append_event(run_id, _event(run_id, 2, "page.completed", "stage1", page=1))
    store.append_event(run_id, _event(run_id, 3, "page.completed", "stage1", page=2))
    store.append_event(run_id, _event(run_id, 4, "stage.started", "stage2_pass1"))
    store.append_event(
        run_id,
        _event(
            run_id,
            5,
            "parse_rules.generated",
            "stage2_pass1",
            artifact_path="/tmp/mdf_parsing_guide.json",
        ),
    )
    store.append_event(
        run_id, _event(run_id, 6, "stage.started", "stage2_pass2", total_pages=2)
    )
    store.append_event(run_id, _event(run_id, 7, "page.started", "stage2_pass2", page=2))

    response = TestClient(app).get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Stage 2 — MDF conversion" in response.text
    assert "Currently processing: Page 2 of 2" in response.text
    assert "Stage 1 — Transcription" in response.text
    assert "MDF parsing guide discovery" in response.text


def test_run_overview_recovers_missing_total_and_uses_singular_page(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path)
    run_id = "single-page-progress"
    store = app.state.run_store
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)
    store.append_event(
        run_id,
        _event(run_id, 1, "stage.started", "stage1", total_pages=0),
    )
    store.append_event(
        run_id,
        _event(run_id, 2, "page.completed", "stage1", page=1),
    )
    store.append_event(
        run_id,
        _event(run_id, 3, "stage.started", "stage2_pass1"),
    )

    response = TestClient(app).get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "1 of 1 page complete" in response.text
    assert "1 of 0 pages complete" not in response.text


def test_run_overview_counts_resumed_skipped_stage_as_complete(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    run_id = "resumed-stage-progress"
    store = app.state.run_store
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)
    store.append_event(
        run_id,
        _event(run_id, 1, "stage.started", "stage1", total_pages=1),
    )
    store.append_event(
        run_id,
        _event(run_id, 2, "stage.started", "stage2_pass1", total_pages=1),
    )

    response = TestClient(app).get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "1 of 1 page complete" in response.text
    assert "0 of 1 page complete" not in response.text


def test_run_overview_uses_one_timeline_with_inline_review_action(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path)
    run_id = "review-progress"
    store = app.state.run_store
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)
    app.state.parse_rule_reviews.create_generated(
        run_id,
        {
            "markers": [{"marker": "lx", "description": "Headword"}],
            "rules": ["Begin each entry with a headword."],
            "abbreviations": {},
        },
        sample_pages=["1"],
    )

    response = TestClient(app).get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert 'class="event-list"' not in response.text
    assert 'class="checkpoint"' not in response.text
    assert "<h2>Review parsing guide</h2>" in response.text
    active_step = response.text.split('class="pipeline-step running"', 1)[1]
    active_step = active_step.split("</article>", 1)[0]
    assert "Review MDF parsing guide" in active_step
    assert f'href="/runs/{run_id}/parse-rules"' in active_step


def test_unknown_run_returns_404(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/runs/not-a-run")

    assert response.status_code == 404


def test_history_filters_by_query_status_and_provider(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    app.state.run_store.create_run("alpha-dictionary", provider="anthropic")
    app.state.run_store.transition("alpha-dictionary", RunStatus.VALIDATED)
    app.state.run_store.create_run("beta-dictionary", provider="openai")
    app.state.run_store.transition("beta-dictionary", RunStatus.VALIDATED)
    app.state.run_store.transition("beta-dictionary", RunStatus.CANCELLED)
    client = TestClient(app)

    response = client.get(
        "/history",
        params={"q": "alpha", "status": "validated", "provider": "anthropic"},
    )

    assert response.status_code == 200
    assert "alpha-dictionary" in response.text
    assert "beta-dictionary" not in response.text
    assert 'name="status"' in response.text
    assert 'name="provider"' in response.text
    assert 'action="/runs/alpha-dictionary/delete"' in response.text
    assert "Remove" in response.text
    assert 'action="/history/delete-all"' in response.text


def test_terminal_run_deletion_removes_local_metadata_but_not_outputs(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    run_id = "delete-terminal"
    app.state.run_store.create_run(run_id)
    app.state.run_store.transition(run_id, RunStatus.CANCELLED)
    bundle = app.state.inputs.bundle(run_id)
    bundle.mkdir(parents=True)
    (bundle / "input.txt").write_text("managed", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.txt").write_text("keep", encoding="utf-8")

    response = TestClient(app).post(
        f"/runs/{run_id}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    with pytest.raises(KeyError):
        app.state.run_store.get_run(run_id)
    assert not bundle.parent.exists()
    assert (output / "result.txt").read_text(encoding="utf-8") == "keep"


def test_validated_run_can_be_deleted_from_history(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    app.state.run_store.create_run("keep-validated")
    app.state.run_store.transition("keep-validated", RunStatus.VALIDATED)
    bundle = app.state.inputs.bundle("keep-validated")
    bundle.mkdir(parents=True)

    response = TestClient(app).post(
        "/runs/keep-validated/delete", follow_redirects=False
    )

    assert response.status_code == 303
    with pytest.raises(KeyError):
        app.state.run_store.get_run("keep-validated")
    assert not bundle.parent.exists()


def test_delete_all_history_removes_inactive_runs_but_keeps_active_run(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    store = app.state.run_store
    store.create_run("remove-validated")
    store.transition("remove-validated", RunStatus.VALIDATED)
    store.create_run("remove-failed")
    store.transition("remove-failed", RunStatus.CANCELLED)
    store.create_run("keep-active")
    store.transition("keep-active", RunStatus.VALIDATED)
    store.transition("keep-active", RunStatus.QUEUED)
    store.transition("keep-active", RunStatus.RUNNING_STAGE1)
    for run_id in ("remove-validated", "remove-failed", "keep-active"):
        app.state.inputs.bundle(run_id).mkdir(parents=True)

    response = TestClient(app).post(
        "/history/delete-all",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    assert [run.run_id for run in store.list_runs()] == ["keep-active"]
    assert not app.state.inputs.bundle("remove-validated").parent.exists()
    assert not app.state.inputs.bundle("remove-failed").parent.exists()
    assert app.state.inputs.bundle("keep-active").parent.exists()


def test_active_run_has_no_history_remove_action_and_rejects_deletion(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path)
    store = app.state.run_store
    store.create_run("active-run")
    store.transition("active-run", RunStatus.VALIDATED)
    store.transition("active-run", RunStatus.QUEUED)
    store.transition("active-run", RunStatus.RUNNING_STAGE1)
    client = TestClient(app)

    history = client.get("/history")
    deletion = client.post("/runs/active-run/delete")

    assert 'action="/runs/active-run/delete"' not in history.text
    assert deletion.status_code == 409
    assert store.get_run("active-run").status is RunStatus.RUNNING_STAGE1
