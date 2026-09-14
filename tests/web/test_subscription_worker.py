"""Security tests for descriptor-only subscription worker handoff."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mudidi.config.yaml_config import InferenceConfig
from mudidi.web.credentials import CredentialVault
from mudidi.web.models import Provider
from mudidi.llm.subscriptions import (
    SubscriptionAuthError,
    SubscriptionProvider,
)
from mudidi.web import production_worker
from mudidi.web.inference_worker import (
    SubscriptionDescriptor,
    apply_credential_message,
)
from mudidi.web.jobs import _worker_environment


def _subscription_config(tmp_path: Path, provider: str = "openai") -> InferenceConfig:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(b"offline image fixture")
    return InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": tmp_path / "output"},
            "pipeline": {"stage": "1"},
            "auth": {"mode": "subscription", "providers": [provider]},
        }
    )


def test_subscription_message_returns_minimal_descriptor_without_touching_environment() -> (
    None
):
    environ: dict[str, str] = {}

    descriptor = apply_credential_message(
        json.dumps({"auth_mode": "subscription", "providers": ["openai", "claude"]}),
        environ=environ,
    )

    assert descriptor == SubscriptionDescriptor(
        auth_mode="subscription",
        providers=(SubscriptionProvider.OPENAI, SubscriptionProvider.CLAUDE),
    )
    assert environ == {}
    assert descriptor.as_message() == {
        "auth_mode": "subscription",
        "providers": ["openai", "claude"],
    }


@pytest.mark.parametrize(
    "message",
    [
        {
            "auth_mode": "subscription",
            "providers": ["openai"],
            "access_token": "access-secret",
        },
        {
            "auth_mode": "subscription",
            "providers": ["openai"],
            "refresh_token": "refresh-secret",
        },
        {"auth_mode": "subscription", "providers": ["openai"], "api_key": "api-secret"},
        {"auth_mode": "subscription", "providers": ["openai"], "unexpected": "value"},
        {"auth_mode": "subscription", "providers": ["not-supported"]},
        {"auth_mode": "subscription", "providers": []},
        {"auth_mode": "subscription", "providers": ["openai", "openai"]},
        {"auth_mode": "api_key", "providers": ["openai"]},
        {"auth_mode": "subscription"},
    ],
)
def test_subscription_message_rejects_tokens_unknown_fields_and_mixed_descriptors(
    message: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="descriptor|subscription"):
        apply_credential_message(json.dumps(message), environ={})


@pytest.mark.parametrize(
    "message",
    [
        '{"auth_mode":"subscription","auth_mode":"subscription","providers":["openai"]}',
        '{"auth_mode":"subscription","providers":[{"access_token":"access-secret"}]}',
        '{"auth_mode":"api_key","credentials":{"OPENAI_API_KEY":"one","OPENAI_API_KEY":"two"}}',
    ],
)
def test_subscription_message_rejects_duplicate_or_nested_credential_values(
    message: str,
) -> None:
    with pytest.raises(ValueError, match="duplicate|descriptor|credential"):
        apply_credential_message(message, environ={})


def test_worker_resolves_subscription_credentials_from_managed_store_without_secret_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _subscription_config(tmp_path)
    config_path = tmp_path / "runs" / "run-1" / "resolved_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    store_path = tmp_path / "subscriptions"
    captured: dict[str, object] = {}

    def fake_phase(*args: object, **kwargs: object) -> object:
        del args, kwargs
        captured["store_path"] = os.environ.get("MUDIDI_SUBSCRIPTION_STORE")
        captured["google_client_id"] = os.environ.get(
            "MUDIDI_GOOGLE_OAUTH_CLIENT_ID"
        )
        captured["api_keys"] = {
            key: value for key, value in os.environ.items() if key.endswith("_API_KEY")
        }
        raise SubscriptionAuthError(
            "subscription failed with Bearer access-secret",
            provider=SubscriptionProvider.OPENAI,
        )

    monkeypatch.setattr(production_worker, "run_inference_phase", fake_phase)
    monkeypatch.setattr(
        production_worker.sys,
        "stdin",
        io.StringIO('{"auth_mode":"subscription","providers":["openai"]}\n'),
    )
    monkeypatch.setenv("MUDIDI_SUBSCRIPTION_STORE", str(tmp_path / "wrong-store"))
    monkeypatch.setenv("OPENAI_API_KEY", "api-secret")
    monkeypatch.setenv("MUDIDI_GOOGLE_OAUTH_CLIENT_ID", "configured-client")

    result = production_worker.main(
        [
            "--run-id",
            "run-1",
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

    assert result == 1
    assert captured == {
        "store_path": str(store_path.resolve()),
        "google_client_id": "configured-client",
        "api_keys": {},
    }
    output = capsys.readouterr().out
    assert "access-secret" not in output
    assert "api-secret" not in output
    assert "openai subscription authentication failed" in output
    assert "access_token" not in output.lower()
    assert "refresh_token" not in output.lower()
    assert "api_key" not in output.lower()
    assert str(store_path) not in output
    assert "access-secret" not in str(production_worker.build_parser())


def test_subscription_popen_environment_excludes_generic_token_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_names = {
        "HF_TOKEN",
        "GITHUB_TOKEN",
        "NPM_TOKEN",
        "AWS_SESSION_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "CUSTOM_SECRET",
    }
    for name in secret_names:
        monkeypatch.setenv(name, f"{name.lower()}-secret")
    child_environment = _worker_environment(subscription=True)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os; print(json.dumps(dict(os.environ)))",
        ],
        env=child_environment,
        capture_output=True,
        text=True,
        check=True,
    )
    observed = json.loads(result.stdout)
    assert secret_names.isdisjoint(observed)
    assert all(value not in result.stdout for value in secret_names)


def test_subscription_worker_passes_only_public_google_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUDIDI_ANTIGRAVITY_CLI", "/configured/bin/agy")
    monkeypatch.setenv("MUDIDI_GOOGLE_OAUTH_CLIENT_ID", "configured-client")
    monkeypatch.setenv("MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET", "configured-secret")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "configured-project")

    child_environment = _worker_environment(subscription=True)

    assert "MUDIDI_ANTIGRAVITY_CLI" not in child_environment
    assert child_environment["MUDIDI_GOOGLE_OAUTH_CLIENT_ID"] == "configured-client"
    assert "MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET" not in child_environment
    assert "GOOGLE_CLOUD_PROJECT" not in child_environment


def test_api_key_worker_environment_overrides_selected_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("CUSTOM_SECRET", "custom-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-parent-key")
    credential = CredentialVault(
        environ={"OPENAI_API_KEY": "selected-api-key"}
    ).resolve(Provider.OPENAI)
    assert credential is not None

    child_environment = _worker_environment((credential,))

    assert child_environment["OPENAI_API_KEY"] == "selected-api-key"
    assert child_environment["HF_TOKEN"] == "hf-secret"
    assert child_environment["GITHUB_TOKEN"] == "github-secret"
    assert child_environment["CUSTOM_SECRET"] == "custom-secret"


def test_custom_api_key_worker_inherits_ambient_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_API_KEY", "ambient-azure-key")
    monkeypatch.setenv("HF_TOKEN", "hf-secret")
    monkeypatch.setenv("CUSTOM_SECRET", "custom-secret")

    child_environment = _worker_environment()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['AZURE_API_KEY'])",
        ],
        env=child_environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "ambient-azure-key"
    subscription_environment = _worker_environment(subscription=True)
    assert "AZURE_API_KEY" not in subscription_environment
    assert "HF_TOKEN" not in subscription_environment
    assert "CUSTOM_SECRET" not in subscription_environment


def test_subscription_descriptor_provider_enum_is_restricted() -> None:
    assert tuple(provider.value for provider in SubscriptionProvider) == (
        "openai",
        "google",
        "claude",
    )
