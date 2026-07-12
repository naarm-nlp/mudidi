"""End-to-end HTTP journey through staged offline production inference."""

from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialVault
from mudidi.web.models import Provider
from mudidi.web.runs import RunStatus

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _preview(client: TestClient, tmp_path: Path) -> str:
    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "agentic": "true",
            "verify_stage1": "true",
            "verify_stage2": "true",
            "parse_rules_pages": "1",
        },
        files={"page_files": ("page_1.png", _PNG, "image/png")},
    )
    assert response.status_code == 200
    match = re.search(r'action="/runs/([^/]+)/start"', response.text)
    assert match is not None
    return match.group(1)


def test_review_start_pause_approve_and_complete_offline_journey(
    tmp_path: Path,
) -> None:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-offline")
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=vault,
        offline_inference=True,
    )
    client = TestClient(app)
    run_id = _preview(client, tmp_path)

    started = client.post(f"/runs/{run_id}/start", follow_redirects=False)
    assert started.status_code == 303
    app.state.job_controller.wait(run_id, timeout=10)
    assert (
        app.state.run_store.get_run(run_id).status
        is RunStatus.AWAITING_PARSE_RULES_REVIEW
    )

    review_page = client.get(f"/runs/{run_id}/parse-rules")
    assert review_page.status_code == 200
    assert "Offline dictionary" in review_page.text

    approved = client.post(
        f"/runs/{run_id}/parse-rules/approve",
        follow_redirects=False,
    )
    assert approved.status_code == 303
    app.state.job_controller.wait(run_id, timeout=10)

    assert app.state.run_store.get_run(run_id).status is RunStatus.COMPLETED
    assert (tmp_path / "output/stage-2/page_1/page_1_mdf.txt").is_file()


def test_missing_key_moves_prepared_run_to_credentials_required(
    tmp_path: Path,
) -> None:
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=CredentialVault(environ={}),
        offline_inference=True,
    )
    client = TestClient(app)
    run_id = _preview(client, tmp_path)

    response = client.post(f"/runs/{run_id}/start")

    assert response.status_code == 409
    assert "API credential required" in response.text
    assert app.state.run_store.get_run(run_id).status is RunStatus.CREDENTIALS_REQUIRED


def test_prepared_review_snapshot_contains_no_credential(tmp_path: Path) -> None:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-must-not-persist")
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=vault,
        offline_inference=True,
    )
    client = TestClient(app)
    run_id = _preview(client, tmp_path)

    config_text = app.state.job_controller.config_path(run_id).read_text(
        encoding="utf-8"
    )

    assert "sk-ant-must-not-persist" not in config_text
    assert "api_key" not in config_text.lower()


def test_live_log_is_managed_bounded_and_redacts_provider_key(tmp_path: Path) -> None:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-live-log-secret")
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=vault,
        offline_inference=True,
    )
    client = TestClient(app)
    run_id = _preview(client, tmp_path)
    log_path = app.state.job_controller.log_path(run_id)
    log_path.write_text(
        ("old output\n" * 70_000) + "request sk-ant-live-log-secret failed\n",
        encoding="utf-8",
    )

    response = client.get(f"/runs/{run_id}/logs")

    assert response.status_code == 200
    assert "Live Logs" in response.text
    assert "[REDACTED]" in response.text
    assert "sk-ant-live-log-secret" not in response.text
    assert "Older log output was truncated" in response.text


def test_validated_run_can_be_saved_and_reprepared_from_preset(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    run_id = _preview(client, tmp_path)

    saved = client.post(
        f"/runs/{run_id}/presets",
        data={"name": "My verified setup"},
        follow_redirects=False,
    )

    assert saved.status_code == 303
    preset = app.state.run_store.list_presets()[0]
    assert preset.config.input.pages is not None
    assert preset.config.input.pages.resolve().is_relative_to(
        (tmp_path / "app-data" / "presets" / preset.preset_id / "inputs").resolve()
    )
    page = client.get("/presets")
    assert page.status_code == 200
    assert "My verified setup" in page.text

    # Presets own their input assets and remain valid after the source run is removed.
    shutil.rmtree(tmp_path / "app-data" / "runs" / run_id / "inputs")

    prepared = client.post(
        f"/presets/{preset.preset_id}/prepare",
        follow_redirects=False,
    )

    assert prepared.status_code == 200
    assert "Review your run" in prepared.text
    assert len(app.state.run_store.list_runs()) == 2
    cloned = next(run for run in app.state.run_store.list_runs() if run.run_id != run_id)
    cloned_config = app.state.job_controller.load_inference_config(cloned.run_id)
    assert cloned_config.input.pages is not None
    assert cloned_config.input.pages.resolve().is_relative_to(
        (tmp_path / "app-data" / "runs" / cloned.run_id / "inputs").resolve()
    )


def test_interrupted_stage1_run_can_resume_from_run_detail(tmp_path: Path) -> None:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-resume")
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=vault,
        offline_inference=True,
    )
    client = TestClient(app)
    run_id = _preview(client, tmp_path)
    app.state.run_store.transition(run_id, RunStatus.QUEUED)
    app.state.run_store.transition(run_id, RunStatus.RUNNING_STAGE1)
    app.state.run_store.interrupt(run_id)

    detail = client.get(f"/runs/{run_id}")
    assert "Resume run" in detail.text

    response = client.post(f"/runs/{run_id}/resume", follow_redirects=False)

    assert response.status_code == 303
    app.state.job_controller.wait(run_id, timeout=10)
    assert (
        app.state.run_store.get_run(run_id).status
        is RunStatus.AWAITING_PARSE_RULES_REVIEW
    )
