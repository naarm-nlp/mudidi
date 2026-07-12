"""Integration tests for the local web application shell."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialSource, CredentialVault
from mudidi.web.models import Provider


def test_home_page_exposes_primary_local_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "New run" in response.text
    assert "Active run" in response.text
    assert "Run history" in response.text
    assert "Input" in response.text
    assert "Pipeline" in response.text
    assert "Parse Rules" in response.text


def test_health_endpoint_is_small_and_versioned(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 1}


def test_untrusted_host_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


def test_static_assets_are_served_locally(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--color-accent" in response.text


def test_new_run_form_previews_typed_configuration(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "pages": str(pages),
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "quality": "verified",
        },
    )

    assert response.status_code == 200
    assert "Review your run" in response.text
    assert "Human approval required" in response.text
    assert "anthropic/claude-sonnet-4-6" in response.text


def test_new_run_form_renders_validation_errors_without_echoing_secret(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/runs/preview",
        data={
            "pages": str(tmp_path / "missing"),
            "output_directory": str(tmp_path / "output"),
            "pipeline": "2-pass-2",
            "provider": "anthropic",
            "model": "sk-do-not-render",
            "reasoning": "low",
            "quality": "verified",
        },
    )

    assert response.status_code == 422
    assert "Check the highlighted configuration" in response.text
    assert "sk-do-not-render" not in response.text


def test_provider_page_lists_bundled_and_custom_models(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/providers")

    assert response.status_code == 200
    assert "GPT-5.6 Sol" in response.text
    assert "Claude Opus 4.8" in response.text
    assert "Gemini 3.5 Flash" in response.text
    assert "Custom LiteLLM model" in response.text


def test_temporary_provider_key_is_kept_in_injected_vault(tmp_path: Path) -> None:
    vault = CredentialVault(environ={})
    client = TestClient(create_app(data_dir=tmp_path, credential_vault=vault))

    response = client.post(
        "/providers/anthropic/credential",
        data={"api_key": "sk-ant-browser-secret"},
    )

    assert response.status_code == 200
    assert "Temporary key ready" in response.text
    assert "sk-ant-browser-secret" not in response.text
    assert vault.status(Provider.ANTHROPIC).source is CredentialSource.TEMPORARY


def test_invalid_provider_key_submission_is_not_reflected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/providers/openai/credential",
        data={"api_key": "   "},
    )

    assert response.status_code == 422
    assert "API key cannot be empty" in response.text
    assert "value=" not in response.text
