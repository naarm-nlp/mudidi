"""End-to-end HTTP journey through staged offline production inference."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialVault
from mudidi.web.models import Provider
from mudidi.web.runs import RunStatus


def _preview(client: TestClient, tmp_path: Path) -> str:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(b"offline image")
    response = client.post(
        "/runs/preview",
        data={
            "pages": str(pages),
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "quality": "verified",
            "parse_rules_pages": "1",
        },
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
    assert app.state.run_store.get_run(run_id).status is RunStatus.AWAITING_PARSE_RULES_REVIEW

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

    config_text = app.state.job_controller.config_path(run_id).read_text(encoding="utf-8")

    assert "sk-ant-must-not-persist" not in config_text
    assert "api_key" not in config_text.lower()
