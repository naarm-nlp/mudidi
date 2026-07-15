"""Integration tests for the local web application shell."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mudidi.web.app import _validation_errors, create_app
from mudidi.web.models import Provider

def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def test_home_page_exposes_primary_local_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "New run" in response.text
    assert "Active run" in response.text
    assert "Run history" in response.text
    assert "Input" in response.text
    assert "Pipeline" in response.text
    assert "MDF parsing guide" in response.text
    assert 'name="dictionary_pages"' in response.text
    assert 'name="stage1_model"' in response.text
    assert 'name="stage2_pass1_model"' in response.text
    assert 'name="stage2_pass2_model"' in response.text
    for provider in ("gemini", "openai", "anthropic", "openrouter"):
        assert f'id="credential-{provider}"' in response.text
        assert f'data-save-key data-provider="{provider}"' in response.text
    assert 'href="/providers"' not in response.text
    assert response.text.count('type="password"') >= 4
    assert response.text.count('class="eye-icon eye-show"') == 4
    assert response.text.count('class="eye-icon eye-hide"') == 4
    assert "◉" not in response.text
    assert 'name="stage1_reasoning"' in response.text
    assert 'name="stage2_pass1_reasoning"' in response.text
    assert 'name="stage2_pass2_reasoning"' in response.text
    assert 'name="reasoning"' not in response.text
    assert 'name="stage1_custom_model"' in response.text
    assert 'name="openrouter_provider"' in response.text
    assert 'data-model-provider="openai"' in response.text
    assert 'data-model-provider="anthropic"' in response.text
    assert 'data-model-provider="gemini"' in response.text
    assert "OpenRouter Provider" in response.text
    assert "qwen/qwen3-235b-a22b" in response.text
    assert 'data-pipeline-stages="stage1"' in response.text
    assert "GPT-5.6 Sol" in response.text
    assert "GPT-5.6 Terra" in response.text
    assert "GPT-5.6 Luna" in response.text
    assert "Claude Fable 5" in response.text
    assert "Claude Opus 4.8" in response.text
    assert "Claude Sonnet 5" in response.text
    assert "Claude Haiku 4.5" in response.text
    assert "Gemini 3.1 Pro" in response.text
    assert "Gemini 3.5 Flash" in response.text
    assert "Gemini 3.1 Flash-Lite" in response.text
    assert "Other / advanced provider" in response.text
    assert "Stage and model overrides" not in response.text
    assert 'name="verify_stage1"' in response.text
    assert 'name="evaluator_provider"' in response.text
    assert 'name="evaluator_model"' in response.text
    assert 'name="evaluator_custom_model"' in response.text
    assert 'name="rewriter_provider"' in response.text
    assert 'name="rewriter_model"' in response.text
    assert 'name="rewriter_custom_model"' in response.text
    assert 'data-agentic-model-group="evaluator"' in response.text
    assert 'data-agentic-model-group="rewriter"' in response.text
    assert 'list="model-catalog"' not in response.text
    assert (
        'name="evaluator_reasoning"><option value="">Use default</option>'
        '<option value="none">None</option><option value="low">Low</option>'
        '<option value="medium">Medium</option><option value="high" selected>High</option>'
        in response.text
    )
    assert (
        'name="rewriter_reasoning"><option value="">Use default</option>'
        '<option value="none">None</option><option value="low" selected>Low</option>'
        in response.text
    )
    assert 'name="page_limit"' not in response.text
    assert 'name="media_reference"' not in response.text
    assert 'name="prompt_cache"' not in response.text
    assert response.text.index('name="temperature"') < response.text.index(
        'name="batch_size"'
    )
    assert response.text.index('name="batch_size"') < response.text.index(
        "Agentic verification"
    )
    assert 'aria-label="About temperature"' in response.text
    assert 'aria-label="About Stage 1 model reasoning"' in response.text
    assert 'aria-label="About Stage 2 Pass 1 model reasoning"' in response.text
    assert 'aria-label="About Stage 2 Pass 2 model reasoning"' in response.text
    assert "requests sent in parallel to your model provider" in response.text
    assert "Check the rate limits for your provider and selected model" in response.text
    assert 'name="strategy"' not in response.text
    assert 'name="vlm_model"' not in response.text
    assert 'name="mathpix_max_wait_seconds"' not in response.text
    assert 'name="output_policy"' in response.text
    assert "Require a new or empty directory" not in response.text
    assert (
            '<input type="radio" name="output_policy" value="resume" checked required>'
        in response.text
    )
    assert "Resume compatible existing artifacts" in response.text
    assert (
        '<input type="radio" name="output_policy" value="overwrite">'
        in response.text
    )
    assert "Overwrite existing artifacts" in response.text
    assert 'aria-label="About resuming existing artifacts"' in response.text
    assert 'aria-label="About overwriting existing artifacts"' in response.text
    assert "after an interrupted run" in response.text
    assert "inputs, models, instructions, or settings changed" in response.text
    assert '<select name="output_policy">' not in response.text
    assert "Dictionary Profile (optional)" in response.text
    assert "can improve extraction accuracy" in response.text
    assert 'name="profile_headword_language"' in response.text
    assert 'name="profile_target_languages"' in response.text
    assert 'name="profile_headword_script"' in response.text
    assert 'name="profile_page_layout"' in response.text
    assert 'name="profile_information_types"' in response.text
    assert 'name="profile_other_information_types"' in response.text
    assert 'class="profile-other-information"' in response.text
    assert (
        "1–2. What language are the dictionary headwords written in, and what script do they use?"
        in response.text
    )
    assert (
        "3–4. Which languages are used for translations, glosses, or definitions, and which script does each use?"
        in response.text
    )
    assert "5. How is information arranged on the page?" in response.text
    assert 'class="profile-layout-question"' in response.text
    assert (
        "There are two columns; each column contains independent dictionary entries."
        in response.text
    )
    assert "6. Which information types appear in an entry?" in response.text
    assert 'name="dictionary_languages"' not in response.text
    assert 'name="stage1_typography"' not in response.text
    assert "/static/app.js?v=dashboard-11" in response.text


def test_home_prefills_gemini_flash_for_each_stage(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert '<option value="gemini" selected>Google Gemini</option>' in response.text
    assert response.text.count(
        'value="gemini/gemini-3.5-flash" data-model-provider="gemini" selected'
    ) == 3


def test_home_explains_each_pipeline_model_role(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert 'aria-label="About Stage 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 2 model"' in response.text
    assert "transcribes the selected dictionary pages into faithful flat text" in response.text
    assert "infers the dictionary-specific MDF parsing guide" in response.text
    assert "applies the approved MDF parsing guide" in response.text


def test_health_endpoint_is_small_and_versioned(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 1}


def test_untrusted_host_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0:8000",
        "host.docker.internal:8000",
        "docker.for.mac.localhost:8000",
    ],
)
def test_container_mode_accepts_docker_loopback_hosts(
    tmp_path: Path,
    host: str,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path, container_mode=True))

    response = client.get("/", headers={"host": host})

    assert response.status_code == 200


def test_container_mode_still_rejects_untrusted_hosts(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path, container_mode=True))

    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


def test_static_assets_are_served_locally(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--color-accent" in response.text
    assert "[hidden]" in response.text
    assert "display: none !important" in response.text
    assert ".profile-layout-question" in response.text
    assert "grid-template-columns: minmax(0, 1fr)" in response.text
    assert ".profile-layout-question textarea" in response.text
    assert ".profile-other-information" in response.text
    assert ".profile-other-information textarea" in response.text
    assert ".preset-loader {" in response.text
    assert "margin-bottom: 24px" in response.text
    assert "padding: 20px 24px" in response.text
    assert ".preset-loader > label" in response.text
    assert ".preset-loader .primary" in response.text


def test_new_run_form_previews_typed_configuration(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert "Review your run" in response.text
    assert "Human approval required" in response.text
    assert "anthropic/claude-sonnet-4-6" in response.text


def test_new_run_saves_selected_dashboard_credential_immediately(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)
    expected_value = "dashboard-dummy-provider-value"

    response = client.post(
        "/credentials/gemini",
        data={"api_key": expected_value},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "provider": "gemini"}
    resolved = app.state.credential_vault.resolve(Provider.GEMINI)
    assert resolved is not None
    assert resolved.get_secret_value() == expected_value
    assert app.state.run_store.list_runs() == []
    assert expected_value not in response.text


def test_preview_error_identifies_the_invalid_field(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "temperature": "-1",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert "Temperature" in response.text
    assert "greater than or equal to 0" in response.text
    assert "Submitted values are not echoed" not in response.text


def test_preview_ignores_retired_controls_from_a_stale_browser_tab(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "page_limit": "12",
            "media_reference": "inline",
            "prompt_cache": "off",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert "Review your run" in response.text


def test_new_run_accepts_provider_aware_stage_models_without_legacy_model(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "transcription",
            "provider": "openrouter",
            "openrouter_provider": "anthropic",
            "stage1_model": "openrouter/anthropic/claude-sonnet-5",
            "temperature": "0.1",
            "reasoning": "none",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.models.stage1 == "openrouter/anthropic/claude-sonnet-5"
    assert config.models.stage1_reasoning == "low"
    assert config.models.openrouter_provider == "anthropic"


def test_new_run_collects_optional_dictionary_profile_questions(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "profile_headword_language": "Chukchi",
            "profile_headword_script": "Cyrillic",
            "profile_target_languages": ["Russian", "English"],
            "profile_target_scripts": ["Cyrillic", "Latin"],
            "profile_page_layout": "There are two columns with independent entries.",
            "profile_information_types": ["translation", "other"],
            "profile_other_information_types": "dialect labels, semantic domains",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.input.dictionary_profile is not None
    assert config.input.dictionary_profile.headword.language == "Chukchi"
    assert [target.script for target in config.input.dictionary_profile.targets] == [
        "Cyrillic",
        "Latin",
    ]
    assert config.pipeline.stage1_typography is False
    assert (
        config.input.dictionary_profile.other_information_types
        == "dialect labels, semantic domains"
    )


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
        },
    )

    assert response.status_code == 422
    assert "Check the highlighted configuration" in response.text
    assert "sk-do-not-render" not in response.text


def test_empty_pydantic_error_still_has_a_user_facing_validation_issue() -> None:
    error = ValidationError.from_exception_data("configuration", [])

    assert _validation_errors(error) == [
        {
            "key": "configuration",
            "field": "Configuration",
            "message": "The submitted configuration could not be validated",
        }
    ]


def test_separate_provider_page_is_removed(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/providers")

    assert response.status_code == 404


def test_provider_key_is_encrypted_revealable_and_persistent(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/credentials/anthropic",
        data={"api_key": "sk-ant-browser-secret"},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "provider": "anthropic"}
    assert "sk-ant-browser-secret" not in response.text
    assert b"sk-ant-browser-secret" not in (tmp_path / "mudidi-web.sqlite3").read_bytes()

    restarted = TestClient(create_app(data_dir=tmp_path))
    home_page = restarted.get("/")
    revealed = restarted.post("/credentials/anthropic/reveal")

    assert home_page.status_code == 200
    assert "1 provider key saved" in home_page.text
    assert "Saved key — leave blank to keep it" in home_page.text
    assert "sk-ant-browser-secret" not in home_page.text
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "no-store"
    assert revealed.json() == {"api_key": "sk-ant-browser-secret"}

    deleted = restarted.post("/credentials/anthropic/delete")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "provider": "anthropic"}
    assert restarted.post("/credentials/anthropic/reveal").status_code == 404


def test_invalid_provider_key_submission_is_not_reflected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/credentials/openai",
        data={"api_key": "   "},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "API key cannot be empty"}


def test_new_run_accepts_uploaded_dictionary_pdf_into_managed_input(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "dictionary_pages": "1-2",
        },
        files={
            "dictionary_pdf": (
                "dictionary.pdf",
                _pdf_bytes(2),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200
    runs = app.state.run_store.list_runs()
    assert len(runs) == 1
    config = app.state.job_controller.load_inference_config(runs[0].run_id)
    assert config.input.pages is not None
    assert (
        config.input.pages.parent.parent
        == (tmp_path / "app-data" / "runs" / runs[0].run_id / "inputs").resolve()
    )
    assert config.input.pages.name == "dictionary.pdf"
    assert config.input.pages.is_file()


def test_upload_rejects_unsafe_filename_without_creating_run(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "transcription",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("../escape.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert app.state.run_store.list_runs() == []
    assert not (tmp_path / "app-data" / "escape.png").exists()
