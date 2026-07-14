"""End-to-end HTTP journey through staged offline production inference."""

from __future__ import annotations

import base64
import json
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


def _pdf_bytes() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def test_review_page_shows_page_ranges_and_each_stage_model(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    response = TestClient(app).post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "dictionary_pages": "10-12",
            "parse_rules_pages": "10,12",
            "provider": "anthropic",
            "stage1_model": "gemini/gemini-3.1-pro-preview",
            "stage2_pass1_model": "anthropic/claude-opus-4-6",
            "stage2_pass2_model": "openai/gpt-5.4",
            "reasoning": "low",
            "agentic": "false",
        },
        files={"page_files": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 200
    assert "Dictionary Pages" in response.text
    assert "10-12" in response.text
    assert "Parse Rule Pages" in response.text
    assert "10, 12" in response.text
    assert "Stage 1 Model" in response.text
    assert "gemini/gemini-3.1-pro-preview" in response.text
    assert "Stage 2 Pass 1 Model" in response.text
    assert "anthropic/claude-opus-4-6" in response.text
    assert "Stage 2 Pass 2 Model" in response.text
    assert "openai/gpt-5.4" in response.text


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
    assert "Dictionary name" not in review_page.text

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


def test_failed_run_surfaces_error_in_overview_and_logs(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    run_id = _preview(client, tmp_path)
    app.state.run_store.transition(run_id, RunStatus.QUEUED)
    app.state.job_controller.start_fake(
        run_id,
        page_count=1,
        delay_seconds=0,
        fail=True,
    )
    app.state.job_controller.wait(run_id, timeout=10)

    overview = client.get(f"/runs/{run_id}")
    logs = client.get(f"/runs/{run_id}/logs")

    assert overview.status_code == 200
    assert "Failure details" in overview.text
    assert "Offline worker failure requested" in overview.text
    assert 'aria-disabled="true">MDF parsing guide</span>' in overview.text
    assert logs.status_code == 200
    assert "Run failure" in logs.text
    assert "Offline worker failure requested" in logs.text


def test_saved_preset_loads_into_editable_new_run_and_reuses_inputs(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    run_id = _preview(client, tmp_path)
    run_bundle = app.state.inputs.bundle(run_id)
    guide = run_bundle / "mdf_guide" / "saved-guide.json"
    guide.parent.mkdir()
    guide.write_text(
        json.dumps(
            {"markers": [{"marker": "lx", "description": "Headword"}]}
        ),
        encoding="utf-8",
    )
    manual = run_bundle / "mdf_manual" / "saved-manual.pdf"
    manual.parent.mkdir()
    manual.write_bytes(_pdf_bytes())
    run_config = app.state.job_controller.load_inference_config(run_id)
    run_config = run_config.model_copy(
        update={
            "input": run_config.input.model_copy(update={"toolbox_pdf": manual}),
            "pipeline": run_config.pipeline.model_copy(
                update={"parse_rules_file": guide}
            ),
        }
    )
    app.state.job_controller.config_path(run_id).write_text(
        run_config.model_dump_json(indent=2),
        encoding="utf-8",
    )

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
    assert f'href="/?preset={preset.preset_id}"' in page.text
    assert "Load preset" in page.text

    home = client.get("/")
    assert 'name="preset"' in home.text
    assert "My verified setup" in home.text
    assert "Load preset" in home.text

    loaded = client.get(f"/?preset={preset.preset_id}")
    assert loaded.status_code == 200
    assert f'name="preset_id" value="{preset.preset_id}"' in loaded.text
    assert 'id="preset-form-state"' in loaded.text
    assert str(tmp_path / "output") in loaded.text
    assert "Loaded preset: My verified setup" in loaded.text
    assert 'id="page-files"' in loaded.text
    assert re.search(r'id="page-files"[^>]*disabled', loaded.text) is None
    assert re.search(r'name="existing_mdf_guide_file"[^>]*disabled', loaded.text) is None
    assert "page_1.png" in loaded.text
    assert "saved-guide.json" in loaded.text
    assert "saved-manual.pdf" in loaded.text
    assert f'/presets/{preset.preset_id}/files/pages/0' in loaded.text
    assert f'/presets/{preset.preset_id}/files/mdf-guide' in loaded.text
    assert f'/presets/{preset.preset_id}/files/mdf-manual' in loaded.text

    saved_page = client.get(f"/presets/{preset.preset_id}/files/pages/0")
    saved_guide = client.get(f"/presets/{preset.preset_id}/files/mdf-guide")
    saved_manual = client.get(f"/presets/{preset.preset_id}/files/mdf-manual")
    assert saved_page.content == _PNG
    assert saved_guide.json()["markers"][0]["marker"] == "lx"
    assert saved_manual.content.startswith(b"%PDF-")

    # Presets own their input assets and remain valid after the source run is removed.
    shutil.rmtree(tmp_path / "app-data" / "runs" / run_id / "inputs")

    edited_output = tmp_path / "edited-output"
    review = client.post(
        "/runs/preview",
        data={
            "preset_id": preset.preset_id,
            "output_directory": str(edited_output),
            "output_policy": "resume",
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "agentic": "false",
            "parse_rules_pages": "1",
        },
    )

    assert review.status_code == 200
    assert "Review your run" in review.text
    assert str(edited_output) in review.text
    assert len(app.state.run_store.list_runs()) == 2
    cloned = next(run for run in app.state.run_store.list_runs() if run.run_id != run_id)
    cloned_config = app.state.job_controller.load_inference_config(cloned.run_id)
    assert cloned_config.input.pages is not None
    assert cloned_config.input.pages.resolve().is_relative_to(
        (tmp_path / "app-data" / "runs" / cloned.run_id / "inputs").resolve()
    )

    replacement = client.post(
        "/runs/preview",
        data={
            "preset_id": preset.preset_id,
            "output_directory": str(tmp_path / "replacement-output"),
            "output_policy": "resume",
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "agentic": "false",
            "mdf_manual_source": "upload",
        },
        files=[
            ("page_files", ("replacement.png", _PNG, "image/png")),
            (
                "existing_mdf_guide_file",
                (
                    "replacement-guide.json",
                    json.dumps(
                        {"markers": [{"marker": "ge", "description": "Gloss"}]}
                    ),
                    "application/json",
                ),
            ),
            ("custom_mdf_manual", ("replacement.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )

    assert replacement.status_code == 200
    replacement_run = app.state.run_store.list_runs()[0]
    replacement_config = app.state.job_controller.load_inference_config(
        replacement_run.run_id
    )
    assert replacement_config.input.pages.name == "pages"
    assert (replacement_config.input.pages / "replacement.png").is_file()
    assert replacement_config.pipeline.parse_rules_file.name == "replacement-guide.json"
    assert replacement_config.input.toolbox_pdf.name == "replacement.pdf"

    overwritten = client.post(
        f"/runs/{replacement_run.run_id}/presets",
        data={"name": "My verified setup"},
        follow_redirects=False,
    )
    assert overwritten.status_code == 303
    saved_presets = app.state.run_store.list_presets()
    assert len(saved_presets) == 1
    assert saved_presets[0].preset_id != preset.preset_id
    assert not (tmp_path / "app-data" / "presets" / preset.preset_id).exists()
    assert client.get(f"/presets/{preset.preset_id}/files/pages/0").status_code == 404


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
