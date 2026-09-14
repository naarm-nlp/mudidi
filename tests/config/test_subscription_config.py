from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.cli.run import execution_namespace_from_config
from mudidi.config.yaml_config import (
    BenchmarkRunConfig,
    InferenceConfig,
    redacted_config_dict,
)
from mudidi.llm.subscriptions import AuthMode, SubscriptionProvider


def _inference_data(
    tmp_path: Path, *, auth: dict[str, object] | None = None
) -> dict[str, object]:
    data: dict[str, object] = {
        "kind": "inference",
        "input": {"pages": tmp_path / "pages"},
        "output": {"directory": tmp_path / "output"},
    }
    if auth is not None:
        data["auth"] = auth
    return data


def test_auth_defaults_to_api_key_without_subscription_providers(
    tmp_path: Path,
) -> None:
    config = InferenceConfig.model_validate(_inference_data(tmp_path))

    assert config.auth.mode is AuthMode.API_KEY
    assert config.auth.providers == ()
    assert config.model_dump(mode="json")["auth"] == {
        "mode": "api_key",
        "providers": [],
    }


def test_subscription_auth_requires_supported_provider_set(tmp_path: Path) -> None:
    config = InferenceConfig.model_validate(
        {
            **_inference_data(
                tmp_path,
                auth={
                    "mode": "subscription",
                    "providers": ["google", "claude", "google"],
                },
            ),
            "models": {
                "default": "gemini/gemini-2.5-pro",
                "stage1": "gemini/gemini-2.5-pro",
                "stage2_pass1": "anthropic/claude-sonnet-4-6",
                "stage2_pass2": "anthropic/claude-sonnet-4-6",
            },
        }
    )

    assert config.auth.mode is AuthMode.SUBSCRIPTION
    assert config.auth.providers == (
        SubscriptionProvider.CLAUDE,
        SubscriptionProvider.GOOGLE,
    )

    with pytest.raises(ValidationError, match="providers"):
        InferenceConfig.model_validate(
            _inference_data(tmp_path, auth={"mode": "subscription"})
        )

    with pytest.raises(ValidationError, match="openai|google|claude"):
        InferenceConfig.model_validate(
            _inference_data(
                tmp_path,
                auth={"mode": "subscription", "providers": ["unsupported"]},
            )
        )


@pytest.mark.parametrize(
    ("provider", "expected_default"),
    [
        ("openai", "openai/gpt-5.6-terra"),
        ("google", "gemini/gemini-3.1-pro-low"),
        ("claude", "anthropic/claude-sonnet-4-6"),
    ],
)
def test_subscription_default_model_follows_selected_provider(
    tmp_path: Path,
    provider: str,
    expected_default: str,
) -> None:
    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path,
            auth={"mode": "subscription", "providers": [provider]},
        )
    )

    assert config.models.default == expected_default


def test_subscription_models_reject_active_mismatch_but_ignore_inactive_stage(
    tmp_path: Path,
) -> None:
    config = InferenceConfig.model_validate(
        {
            **_inference_data(
                tmp_path,
                auth={"mode": "subscription", "providers": ["openai"]},
            ),
            "pipeline": {"stage": "1"},
            "models": {"stage2_pass1": "gemini/gemini-ignored"},
        }
    )
    assert config.models.stage2_pass1 == "gemini/gemini-ignored"

    with pytest.raises(ValidationError, match="models.stage1|provider"):
        InferenceConfig.model_validate(
            {
                **_inference_data(
                    tmp_path,
                    auth={"mode": "subscription", "providers": ["openai"]},
                ),
                "pipeline": {"stage": "1"},
                "models": {"stage1": "gemini/gemini-wrong"},
            }
        )


def test_subscription_models_reject_active_agentic_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="agentic.evaluator_model|provider"):
        InferenceConfig.model_validate(
            {
                **_inference_data(
                    tmp_path,
                    auth={"mode": "subscription", "providers": ["claude"]},
                ),
                "agentic": {
                    "stage1": True,
                    "evaluator_model": "openai/gpt-wrong",
                },
            }
        )


def test_benchmark_configs_reject_subscription_auth(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="benchmark|api_key"):
        BenchmarkRunConfig.model_validate(
            {
                "kind": "benchmark_run",
                "input": {"pages": tmp_path / "pages"},
                "output": {"directory": tmp_path / "output"},
                "auth": {"mode": "subscription", "providers": ["openai"]},
            }
        )


def test_redacted_config_keeps_auth_selection_but_no_token_material(
    tmp_path: Path,
) -> None:
    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path, auth={"mode": "subscription", "providers": ["openai"]}
        )
    )

    redacted = redacted_config_dict(config)

    assert redacted["auth"] == {"mode": "subscription", "providers": ["openai"]}
    assert all("token" not in key.lower() for key in redacted["auth"])
    assert "access-secret" not in repr(redacted)


def test_execution_namespace_maps_auth_selection(tmp_path: Path) -> None:
    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path, auth={"mode": "subscription", "providers": ["claude"]}
        )
    )

    namespace = execution_namespace_from_config(config)

    assert namespace.auth_mode == "subscription"
    assert namespace.auth_providers == ["claude"]
    assert not hasattr(namespace, "access_token")
    assert not hasattr(namespace, "refresh_token")


def test_api_key_namespace_mapping_remains_explicit(tmp_path: Path) -> None:
    config = InferenceConfig.model_validate(_inference_data(tmp_path))
    namespace = execution_namespace_from_config(config)

    assert namespace.auth_mode == "api_key"
    assert namespace.auth_providers == []
    assert isinstance(namespace, argparse.Namespace)


def test_subscription_runtime_missing_credentials_fails_before_page_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mudidi.cli import extract
    from mudidi.cli import run as run_module
    from mudidi.llm.subscriptions import SubscriptionAuthError

    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path, auth={"mode": "subscription", "providers": ["openai"]}
        )
    )
    monkeypatch.setattr(
        run_module,
        "resolve_subscription_runtime",
        lambda _auth: (_ for _ in ()).throw(
            SubscriptionAuthError(
                "missing subscription credential",
                provider=SubscriptionProvider.OPENAI,
            )
        ),
    )
    monkeypatch.setattr(
        extract,
        "main",
        lambda **_kwargs: pytest.fail("page extraction must not start"),
    )

    with pytest.raises(SubscriptionAuthError):
        run_module.execute_extraction_config(config)


def test_runtime_refreshes_expired_stored_credential_before_rejecting(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from mudidi.cli.run import resolve_subscription_runtime

    class _ExpiringBackend:
        provider = SubscriptionProvider.OPENAI

        def __init__(self) -> None:
            self.status_calls = 0
            self.refresh_calls = 0

        def status(self):
            self.status_calls += 1
            if self.status_calls == 1:
                return SimpleNamespace(
                    authenticated=False,
                    expires_at=datetime.now(UTC) - timedelta(minutes=1),
                    metadata={},
                )
            return SimpleNamespace(
                authenticated=True,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                metadata={},
            )

        def refresh(self):
            self.refresh_calls += 1

    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path, auth={"mode": "subscription", "providers": ["openai"]}
        )
    )
    backend = _ExpiringBackend()

    runtime = resolve_subscription_runtime(config.auth, backend=backend)

    assert runtime is not None
    assert runtime.backend is backend
    assert backend.status_calls == 2
    assert backend.refresh_calls == 1


def test_google_runtime_factory_uses_injected_encrypted_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mudidi.cli.run import resolve_subscription_runtime
    from mudidi.llm.subscriptions import SubscriptionStatus
    from mudidi.llm.subscriptions.google_antigravity import (
        GoogleAntigravityBackend,
    )

    store = object()
    monkeypatch.setattr(
        GoogleAntigravityBackend,
        "status",
        lambda self: SubscriptionStatus(
            provider=SubscriptionProvider.GOOGLE,
            authenticated=True,
            account_label="Google account",
        ),
    )
    config = InferenceConfig.model_validate(
        _inference_data(
            tmp_path, auth={"mode": "subscription", "providers": ["google"]}
        )
    )
    runtime = resolve_subscription_runtime(config.auth, store=store)

    assert runtime is not None
    assert isinstance(runtime.backend, GoogleAntigravityBackend)
    assert runtime.backend._store is store
