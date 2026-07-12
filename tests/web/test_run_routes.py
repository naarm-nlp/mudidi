"""HTTP integration tests for offline run, progress, and history screens."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.runs import RunStatus


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
