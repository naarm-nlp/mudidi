"""Tests for provider model discovery fallbacks and ephemeral credentials."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from mudidi.web.credentials import CredentialSource, CredentialVault
from mudidi.web.models import (
    ModelDiscovery,
    ModelCatalog,
    Provider,
    normalize_custom_model,
)
from mudidi.web.models import _fetch_json


def test_catalog_contains_current_official_multimodal_families() -> None:
    catalog = ModelCatalog.bundled()

    assert catalog.get("openai/gpt-5.6").image_input is True
    assert catalog.get("anthropic/claude-opus-4-8").image_input is True
    assert catalog.get("anthropic/claude-sonnet-5").image_input is True
    assert catalog.get("gemini/gemini-3.5-flash").image_input is True


@pytest.mark.parametrize("provider", list(Provider))
def test_every_provider_allows_custom_model_entry(provider: Provider) -> None:
    model = normalize_custom_model(provider, " vendor/custom-model ")

    assert model.endswith("vendor/custom-model")


def test_direct_provider_prefix_is_added_to_unqualified_custom_model() -> None:
    assert (
        normalize_custom_model(Provider.OPENAI, "gpt-private") == "openai/gpt-private"
    )
    assert (
        normalize_custom_model(Provider.ANTHROPIC, "claude-private")
        == "anthropic/claude-private"
    )
    assert (
        normalize_custom_model(Provider.GEMINI, "gemini-private")
        == "gemini/gemini-private"
    )


def test_openrouter_keeps_explicit_routing_namespace() -> None:
    assert (
        normalize_custom_model(Provider.OPENROUTER, "anthropic/claude-opus-4-8")
        == "openrouter/anthropic/claude-opus-4-8"
    )


def test_empty_custom_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="model"):
        normalize_custom_model(Provider.OPENAI, "   ")


def test_temporary_credential_is_resolved_without_appearing_in_repr() -> None:
    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-secret")

    resolved = vault.resolve(Provider.ANTHROPIC)

    assert resolved is not None
    assert resolved.source is CredentialSource.TEMPORARY
    assert resolved.get_secret_value() == "sk-ant-secret"
    assert "sk-ant-secret" not in repr(vault)
    assert "sk-ant-secret" not in repr(resolved)


def test_environment_credential_is_detected_without_copying_to_status() -> None:
    environ: Mapping[str, str] = {"OPENAI_API_KEY": "sk-openai-secret"}
    vault = CredentialVault(environ=environ)

    status = vault.status(Provider.OPENAI)

    assert status.available is True
    assert status.source is CredentialSource.ENVIRONMENT
    assert "sk-openai-secret" not in repr(status)


def test_temporary_credential_overrides_environment_and_can_be_cleared() -> None:
    vault = CredentialVault(environ={"GEMINI_API_KEY": "env-secret"})
    vault.set_temporary(Provider.GEMINI, "temporary-secret")
    assert vault.resolve(Provider.GEMINI).get_secret_value() == "temporary-secret"  # type: ignore[union-attr]

    vault.clear_temporary(Provider.GEMINI)

    resolved = vault.resolve(Provider.GEMINI)
    assert resolved is not None
    assert resolved.source is CredentialSource.ENVIRONMENT
    assert resolved.get_secret_value() == "env-secret"


def test_missing_credential_has_non_secret_status() -> None:
    status = CredentialVault(environ={}).status(Provider.OPENROUTER)

    assert status.available is False
    assert status.source is CredentialSource.MISSING


@pytest.mark.parametrize(
    ("provider", "payload", "expected"),
    [
        (Provider.OPENAI, {"data": [{"id": "gpt-5.6"}]}, "openai/gpt-5.6"),
        (
            Provider.ANTHROPIC,
            {"data": [{"id": "claude-sonnet-5", "display_name": "Sonnet 5"}]},
            "anthropic/claude-sonnet-5",
        ),
        (
            Provider.GEMINI,
            {
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
            "gemini/gemini-3.5-flash",
        ),
        (
            Provider.OPENROUTER,
            {
                "data": [
                    {
                        "id": "anthropic/claude-sonnet-5",
                        "name": "Claude Sonnet 5",
                        "architecture": {"input_modalities": ["text", "image"]},
                    }
                ]
            },
            "openrouter/anthropic/claude-sonnet-5",
        ),
    ],
)
def test_live_discovery_normalizes_official_provider_payloads(
    provider: Provider,
    payload: dict[str, object],
    expected: str,
) -> None:
    requests: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> dict[str, object]:
        requests.append((url, headers))
        return payload

    models = ModelDiscovery(fetch=fetch).discover(provider, api_key="private-key")

    assert [model.model_id for model in models] == [expected]
    assert requests
    assert "private-key" not in repr(models)


def test_model_fetch_rejects_non_allowlisted_url_before_network_access() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        _fetch_json("file:///etc/passwd", {})
