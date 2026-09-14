"""Route tests for secret-safe dynamic model catalogs."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialVault
from mudidi.llm.reasoning import resolve_reasoning_profile
from mudidi.llm.subscriptions import (
    SubscriptionModel,
    SubscriptionProvider,
    SubscriptionStatus,
)
from mudidi.web.models import LiveModelOption, ModelDiscoveryError, Provider


class _Discovery:
    def __init__(self) -> None:
        self.calls: list[tuple[Provider, str]] = []
        self.fail = False

    def discover(
        self,
        provider: Provider,
        *,
        api_key: str,
    ) -> tuple[LiveModelOption, ...]:
        self.calls.append((provider, api_key))
        if self.fail:
            raise ModelDiscoveryError(
                f"provider rejected {api_key}: raw-provider-detail"
            )
        model_ids = {
            "first-private-key": "openai/gpt-5.6-terra",
            "second-private-key": "openai/gpt-5.6-sol",
        }
        model_id = model_ids.get(api_key, "openai/gpt-5.6-terra")
        return (
            LiveModelOption(
                model_id=model_id,
                display_name=model_id.rsplit("/", 1)[-1],
                provider=provider,
                image_input=True,
            ),
        )


class _AuthenticatedSubscriptionBackend:
    def __init__(
        self,
        provider: SubscriptionProvider = SubscriptionProvider.CLAUDE,
        *,
        models: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.provider = provider
        reasoning_provider = {
            SubscriptionProvider.OPENAI: "openai-codex",
            SubscriptionProvider.GOOGLE: "google-antigravity",
            SubscriptionProvider.CLAUDE: "claude",
        }[provider]
        self.models = tuple(
            SubscriptionModel(
                model_id=model_id,
                display_name=display_name,
                reasoning=resolve_reasoning_profile(reasoning_provider, model_id),
                provider_order=index,
            )
            for index, (model_id, display_name) in enumerate(models)
        )
        self.list_calls = 0

    def status(self) -> SubscriptionStatus:
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=True,
            account_label=f"{self.provider.value}@example.test",
        )

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        self.list_calls += 1
        return self.models


def test_model_catalog_endpoint_returns_non_secret_grouped_models(
    tmp_path: Path,
) -> None:
    discovery = _Discovery()
    vault = CredentialVault(environ={"OPENAI_API_KEY": "first-private-key"})
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            credential_vault=vault,
            model_discovery=discovery,
        )
    )

    response = client.get(
        "/models/openai",
        params={"stage": "stage1", "auth_mode": "api_key"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["recommended"] == []
    assert response.json()["available"][0] == {
        "model_id": "openai/gpt-5.6-terra",
        "display_name": "gpt-5.6-terra",
        "compatibility": "unverified",
        "reasoning": {
            "efforts": ["low", "medium", "high", "xhigh", "max"],
            "default": "medium",
            "supports_off": False,
            "source": "reviewed",
        },
    }
    assert response.json()["source"] == "live"
    assert "first-private-key" not in response.text


def test_model_catalog_endpoint_requires_api_credential(tmp_path: Path) -> None:
    discovery = _Discovery()
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            credential_vault=CredentialVault(environ={}),
            model_discovery=discovery,
        )
    )

    response = client.get(
        "/models/openai",
        params={"stage": "stage1", "auth_mode": "api_key"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "authentication_required"
    assert response.json()["warning"]["code"] == "authentication_required"
    assert response.json()["recommended"] == []
    assert response.json()["available"] == []
    assert discovery.calls == []


def test_model_catalog_endpoint_redacts_provider_failure(tmp_path: Path) -> None:
    discovery = _Discovery()
    discovery.fail = True
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            credential_vault=CredentialVault(
                environ={"OPENAI_API_KEY": "first-private-key"}
            ),
            model_discovery=discovery,
        )
    )

    response = client.get(
        "/models/openai",
        params={"stage": "stage1", "auth_mode": "api_key"},
    )

    assert response.status_code == 200
    assert response.json()["warning"]["code"] == "provider_unavailable"
    assert "first-private-key" not in response.text
    assert "raw-provider-detail" not in response.text


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/models/unknown", {"stage": "stage1", "auth_mode": "api_key"}),
        ("/models/custom", {"stage": "stage1", "auth_mode": "api_key"}),
        ("/models/openai", {"stage": "unknown", "auth_mode": "api_key"}),
        ("/models/openai", {"stage": "stage1", "auth_mode": "unknown"}),
        (
            "/models/openrouter",
            {"stage": "stage1", "auth_mode": "subscription"},
        ),
    ],
)
def test_model_catalog_endpoint_rejects_invalid_combinations(
    tmp_path: Path,
    path: str,
    params: dict[str, str],
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get(path, params=params)

    assert 400 <= response.status_code < 500


def test_credential_save_and_delete_invalidate_provider_catalog(
    tmp_path: Path,
) -> None:
    discovery = _Discovery()
    client = TestClient(create_app(data_dir=tmp_path, model_discovery=discovery))
    catalog_request = {
        "stage": "stage1",
        "auth_mode": "api_key",
    }

    saved = client.post(
        "/credentials/openai",
        data={"api_key": "first-private-key"},
    )
    first = client.get("/models/openai", params=catalog_request)
    cached = client.get("/models/openai", params=catalog_request)
    replaced = client.post(
        "/credentials/openai",
        data={"api_key": "second-private-key"},
    )
    second = client.get("/models/openai", params=catalog_request)
    deleted = client.post("/credentials/openai/delete")
    fallback = client.get("/models/openai", params=catalog_request)

    assert saved.status_code == 200
    assert first.json()["available"][0]["model_id"] == "openai/gpt-5.6-terra"
    assert cached.json()["source"] == "cached"
    assert replaced.status_code == 200
    assert second.json()["available"][0]["model_id"] == "openai/gpt-5.6-sol"
    assert deleted.status_code == 200
    assert fallback.json()["source"] == "authentication_required"
    assert [key for _provider, key in discovery.calls] == [
        "first-private-key",
        "second-private-key",
    ]


def test_subscription_catalog_requires_authenticated_provider(tmp_path: Path) -> None:
    discovery = _Discovery()
    client = TestClient(create_app(data_dir=tmp_path, model_discovery=discovery))

    response = client.get(
        "/models/anthropic",
        params={"stage": "stage2", "auth_mode": "subscription"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "authentication_required"
    assert response.json()["warning"]["code"] == "authentication_required"
    assert response.json()["recommended"] == []
    assert response.json()["available"] == []
    assert discovery.calls == []


def test_authenticated_openai_subscription_catalog_comes_from_account(
    tmp_path: Path,
) -> None:
    discovery = _Discovery()
    backend = _AuthenticatedSubscriptionBackend(
        SubscriptionProvider.OPENAI,
        models=(
            ("gpt-account-second", "GPT Account Second"),
            ("gpt-account-first", "GPT Account First"),
        ),
    )
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            model_discovery=discovery,
            subscription_backends={SubscriptionProvider.OPENAI: backend},
        )
    )

    response = client.get(
        "/models/openai",
        params={"stage": "stage1", "auth_mode": "subscription"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    assert response.json()["recommended"] == []
    assert [
        (item["model_id"], item["display_name"])
        for item in response.json()["available"]
    ] == [
        ("openai/gpt-account-second", "GPT Account Second"),
        ("openai/gpt-account-first", "GPT Account First"),
    ]
    assert "gpt-5.6-terra" not in response.text
    assert "gpt-5.6-sol" not in response.text
    assert "gpt-5.6-luna" not in response.text
    assert backend.list_calls == 1
    assert discovery.calls == []


def test_authenticated_subscription_catalog_uses_complete_claude_discovery(
    tmp_path: Path,
) -> None:
    discovery = _Discovery()
    backend = _AuthenticatedSubscriptionBackend(
        models=(
            ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            ("claude-opus-5", "Claude Opus 5"),
        )
    )
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            model_discovery=discovery,
            subscription_backends={SubscriptionProvider.CLAUDE: backend},
        )
    )

    response = client.get(
        "/models/anthropic",
        params={"stage": "stage2", "auth_mode": "subscription"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    assert response.json()["recommended"] == []
    assert [item["model_id"] for item in response.json()["available"]] == [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-5",
    ]
    assert backend.list_calls == 1
    assert discovery.calls == []


def test_authenticated_google_subscription_catalog_comes_from_antigravity(
    tmp_path: Path,
) -> None:
    discovery = _Discovery()
    backend = _AuthenticatedSubscriptionBackend(
        SubscriptionProvider.GOOGLE,
        models=(
            ("gemini-3.8-flash", "Gemini 3.8 Flash"),
            ("gemini-3.1-pro", "Gemini 3.1 Pro"),
        ),
    )
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            model_discovery=discovery,
            subscription_backends={SubscriptionProvider.GOOGLE: backend},
        )
    )

    response = client.get(
        "/models/gemini",
        params={"stage": "stage1", "auth_mode": "subscription"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    assert response.json()["recommended"] == []
    assert [
        (item["model_id"], item["display_name"])
        for item in response.json()["available"]
    ] == [
        ("gemini/gemini-3.1-pro", "Gemini 3.1 Pro"),
        ("gemini/gemini-3.8-flash", "Gemini 3.8 Flash"),
    ]
    assert backend.list_calls == 1
    assert discovery.calls == []


def test_authenticated_subscription_catalog_force_refreshes_cached_models(
    tmp_path: Path,
) -> None:
    backend = _AuthenticatedSubscriptionBackend(
        SubscriptionProvider.GOOGLE,
        models=(("gemini-3.7-flash", "Gemini 3.7 Flash"),),
    )
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            subscription_backends={SubscriptionProvider.GOOGLE: backend},
        )
    )
    request = {"stage": "stage1", "auth_mode": "subscription"}

    first = client.get("/models/gemini", params=request)
    backend.models = (
        SubscriptionModel(
            model_id="gemini-3.8-flash",
            display_name="Gemini 3.8 Flash",
            reasoning=resolve_reasoning_profile(
                "google-antigravity", "gemini-3.8-flash"
            ),
        ),
    )
    cached = client.get("/models/gemini", params=request)
    refreshed = client.get(
        "/models/gemini",
        params={**request, "force": "true"},
    )

    assert first.json()["available"][0]["model_id"] == "gemini/gemini-3.7-flash"
    assert cached.json()["available"][0]["model_id"] == "gemini/gemini-3.7-flash"
    assert refreshed.status_code == 200
    assert refreshed.json()["source"] == "live"
    assert refreshed.json()["available"][0]["model_id"] == "gemini/gemini-3.8-flash"
    assert backend.list_calls == 2
