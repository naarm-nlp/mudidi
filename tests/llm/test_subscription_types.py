"""Tests for provider-neutral direct subscription contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from mudidi.llm.reasoning import ReasoningProfile

from mudidi.llm.subscriptions import (
    AuthMode,
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    SubscriptionModel,
    SubscriptionAuthError,
    SubscriptionBackend,
    SubscriptionCredential,
    SubscriptionPolicyError,
    SubscriptionProvider,
    SubscriptionStatus,
    SubscriptionTokenExpired,
    SubscriptionTransportError,
    SubscriptionUnsupportedRequest,
)


class _NotJsonSerializable:
    pass


def test_subscription_provider_and_auth_mode_values_are_stable() -> None:
    assert [provider.value for provider in SubscriptionProvider] == [
        "openai",
        "google",
        "claude",
    ]
    assert [mode.value for mode in AuthMode] == ["api_key", "subscription"]
    assert SubscriptionProvider("openai") is SubscriptionProvider.OPENAI
    assert AuthMode("subscription") is AuthMode.SUBSCRIPTION


def test_completion_request_is_frozen_and_carries_provider_neutral_fields() -> None:
    request = CompletionRequest(
        model="subscription-model",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=128,
        reasoning="high",
        schema={"type": "object"},
        cache_key="cache-key",
    )

    assert request.model == "subscription-model"
    assert request.messages == [{"role": "user", "content": "Hello"}]
    assert "temperature" not in request.model_dump()
    assert request.max_tokens == 128
    assert request.reasoning == "high"
    assert request.schema == {"type": "object"}
    assert request.cache_key == "cache-key"

    with pytest.raises(ValidationError):
        request.model = "other-model"  # type: ignore[misc]


def test_completion_request_rejects_removed_temperature_field() -> None:
    with pytest.raises(ValidationError, match="temperature"):
        CompletionRequest.model_validate(
            {
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.1,
            }
        )


def test_completion_result_defaults_to_subscription_billing_and_is_frozen() -> None:
    result = CompletionResult(
        text="visible answer",
        structured_json={"answer": "visible answer"},
        usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        finish_reason="stop",
        provider=SubscriptionProvider.OPENAI,
        model="subscription-model",
    )

    assert result.text == "visible answer"
    assert result.structured_json == {"answer": "visible answer"}
    assert result.usage["total_tokens"] == 6
    assert result.finish_reason == "stop"
    assert result.billing_mode == "subscription"
    assert result.provider is SubscriptionProvider.OPENAI

    with pytest.raises(ValidationError):
        result.billing_mode = "api_key"  # type: ignore[misc]


def test_backend_capabilities_are_frozen_value_model() -> None:
    capabilities = BackendCapabilities(
        image_input=True,
        structured_output=True,
        streaming=False,
        cancellation=True,
        usage=True,
        reasoning=True,
        model_discovery=False,
    )

    assert capabilities.image_input is True
    assert capabilities.structured_output is True
    assert capabilities.cancellation is True

    with pytest.raises(ValidationError):
        capabilities.usage = False  # type: ignore[misc]


def test_subscription_credential_validates_and_redacts_tokens_everywhere() -> None:
    access_token = "access-token-secret"
    refresh_token = "refresh-token-secret"
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="research account",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        project_id="project-123",
        account_id="account-456",
        metadata={
            "email": "research@example.test",
            "headers": {"Authorization": f"Bearer {access_token}"},
            "attempts": [
                {"authorization": f"Bearer {refresh_token}"},
                "Authorization: Bearer nested-opaque-secret",
            ],
            "access_token": access_token,
        },
    )

    rendered = repr(credential)
    dumped = credential.model_dump()
    metadata = credential.redacted_metadata
    assert access_token not in rendered
    assert refresh_token not in rendered
    assert access_token not in repr(dumped)
    assert refresh_token not in repr(dumped)
    assert access_token not in repr(metadata)
    assert refresh_token not in repr(metadata)
    for rendered_surface in (rendered, repr(dumped), repr(metadata)):
        assert "access_token" not in rendered_surface
        assert "refresh_token" not in rendered_surface
        assert "nested-opaque-secret" not in rendered_surface
    assert dumped["provider"] == SubscriptionProvider.GOOGLE
    assert metadata["provider"] == "google"
    assert metadata["account_label"] == "research account"
    assert metadata["project_id"] == "project-123"
    assert metadata["account_id"] == "account-456"
    assert metadata["email"] == "research@example.test"
    assert credential.access_token.get_secret_value() == access_token
    assert credential.refresh_token is not None
    assert credential.refresh_token.get_secret_value() == refresh_token


def test_subscription_status_redacts_nested_metadata_on_all_surfaces() -> None:
    secret = "status-opaque-secret"
    status = SubscriptionStatus(
        provider=SubscriptionProvider.OPENAI,
        authenticated=True,
        account_label="status account",
        metadata={
            "headers": {"Authorization": f"Bearer {secret}"},
            "history": [
                {"authorization": f"Bearer {secret}"},
                "Authorization: Bearer nested-status-secret",
            ],
            "access_token": secret,
        },
    )

    rendered = repr(status)
    dumped = status.model_dump()
    metadata = status.redacted_metadata
    for rendered_surface in (rendered, repr(dumped), repr(metadata)):
        assert secret not in rendered_surface
        assert "access_token" not in rendered_surface
        assert "refresh_token" not in rendered_surface
        assert "nested-status-secret" not in rendered_surface


def test_subscription_status_rejects_removed_warning_and_filters_legacy_metadata() -> (
    None
):
    with pytest.raises(ValidationError):
        SubscriptionStatus(
            provider=SubscriptionProvider.CLAUDE,
            policy_warning="legacy warning",
        )

    status = SubscriptionStatus(
        provider=SubscriptionProvider.CLAUDE,
        metadata={"policy_warning": "legacy warning", "opt_in_enabled": False},
    )

    assert "policy_warning" not in status.metadata
    assert "policy_warning" not in status.redacted_metadata


def test_subscription_status_default_metadata_is_frozen_and_safe() -> None:
    status = SubscriptionStatus(provider=SubscriptionProvider.GOOGLE)

    with pytest.raises(TypeError):
        status.metadata["access_token"] = "status-secret"
    for rendered_surface in (
        repr(status),
        repr(status.model_dump()),
        repr(status.redacted_metadata),
    ):
        assert "access_token" not in rendered_surface
        assert "status-secret" not in rendered_surface


def test_nested_request_and_result_values_are_immutable() -> None:
    request = CompletionRequest(
        model="subscription-model",
        messages=[{"role": "user", "content": {"parts": ["Hello"]}}],
        schema={"properties": {"answer": {"type": "string"}}},
    )
    with pytest.raises(TypeError):
        request.messages[0]["content"] = "changed"
    with pytest.raises(TypeError):
        request.messages[0]["content"]["parts"].append("changed")  # type: ignore[index]
    with pytest.raises(TypeError):
        request.messages.append({"role": "assistant", "content": "changed"})
    with pytest.raises(TypeError):
        request.schema["properties"]["answer"]["type"] = "integer"  # type: ignore[index]

    result = CompletionResult(
        text="visible answer",
        structured_json={"answer": {"parts": ["visible answer"]}},
        usage={"input_tokens": 4, "output_tokens": 2},
    )
    with pytest.raises(TypeError):
        result.structured_json["answer"]["parts"].append("changed")  # type: ignore[index]
    with pytest.raises(TypeError):
        result.usage["input_tokens"] = 99


def test_completion_result_default_usage_is_frozen() -> None:
    result = CompletionResult(text="visible answer")

    with pytest.raises(TypeError):
        result.usage["input_tokens"] = 1


def test_completion_payloads_accept_json_values_only() -> None:
    CompletionRequest(
        model="subscription-model",
        messages=[{"role": "user", "content": ["Hello", 1, None]}],
        schema={"type": "object", "required": ["answer"]},
    )
    CompletionResult(
        text="visible answer",
        structured_json={"answer": ["visible answer", True]},
        usage={"input_tokens": 4, "cost_usd": 0.01},
    )

    with pytest.raises(ValidationError):
        CompletionRequest(
            model="subscription-model",
            messages=[{"role": "user", "content": _NotJsonSerializable()}],
        )
    with pytest.raises(ValidationError):
        CompletionResult(
            text="visible answer",
            structured_json=_NotJsonSerializable(),
        )
    with pytest.raises(ValidationError):
        CompletionResult(
            text="visible answer",
            usage={"input_tokens": _NotJsonSerializable()},
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "openai", "account_label": "", "access_token": "x"},
        {"provider": "openai", "account_label": "account", "access_token": ""},
        {"provider": "unknown", "account_label": "account", "access_token": "x"},
    ],
)
def test_subscription_credential_rejects_invalid_required_values(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        SubscriptionCredential(**payload)


def test_subscription_model_normalizes_release_time_and_validates_native_id() -> None:
    model = SubscriptionModel(
        model_id="claude-sonnet-5",
        display_name="Claude Sonnet 5",
        reasoning=ReasoningProfile(efforts=("low", "high"), default="high"),
        release_at=datetime(2026, 9, 14),
        provider_order=0,
    )

    assert model.release_at == datetime(2026, 9, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="native identifier"):
        SubscriptionModel(
            model_id="anthropic/claude-sonnet-5",
            display_name="Claude Sonnet 5",
            reasoning=model.reasoning,
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        SubscriptionModel(
            model_id="claude-sonnet-5",
            display_name="Claude Sonnet 5",
            reasoning=model.reasoning,
            provider_order=True,
        )


def test_subscription_backend_protocol_exposes_the_full_contract() -> None:
    class FakeBackend:
        provider = SubscriptionProvider.OPENAI
        capabilities = BackendCapabilities()

        def login(self) -> SubscriptionCredential:
            raise NotImplementedError

        def refresh(self) -> SubscriptionCredential:
            raise NotImplementedError

        def logout(self) -> None:
            raise NotImplementedError

        def status(self) -> dict[str, Any]:
            raise NotImplementedError

        def list_models(self) -> tuple[SubscriptionModel, ...]:
            return ()

        def complete(self, request: CompletionRequest) -> CompletionResult:
            raise NotImplementedError

        def complete_structured(self, request: CompletionRequest) -> CompletionResult:
            raise NotImplementedError

    assert isinstance(FakeBackend(), SubscriptionBackend)
    for method in (
        "login",
        "refresh",
        "logout",
        "status",
        "list_models",
        "complete",
        "complete_structured",
    ):
        assert callable(getattr(FakeBackend(), method))


@pytest.mark.parametrize(
    ("error_type", "category"),
    [
        (SubscriptionAuthError, "authentication"),
        (SubscriptionTokenExpired, "expired_session"),
        (SubscriptionTransportError, "transport"),
        (SubscriptionPolicyError, "policy"),
        (SubscriptionUnsupportedRequest, "unsupported_capability"),
    ],
)
def test_subscription_errors_have_safe_normalized_metadata(
    error_type: type[Exception], category: str
) -> None:
    secret = "do-not-leak-this-token"
    error = error_type(
        f"provider failed with access_token={secret}",
        provider=SubscriptionProvider.CLAUDE,
        status=403,
    )

    assert error.category == category
    assert error.provider is SubscriptionProvider.CLAUDE
    assert error.status == 403
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(error.metadata)
    assert error.metadata == {
        "provider": "claude",
        "category": category,
        "status": 403,
    }


def test_subscription_errors_redact_authorization_and_quoted_json_values() -> None:
    bearer_secret = "opaque-secret"
    json_secret = "json-opaque-secret"
    error = SubscriptionTransportError(
        (
            f"Authorization: Bearer {bearer_secret}; "
            f'body={{"access_token":"{json_secret}",'
            f'"refresh_token": "{bearer_secret}"}}'
        ),
        provider=SubscriptionProvider.OPENAI,
        status=401,
        metadata={
            "details": [
                f"Authorization: Bearer {bearer_secret}",
                {"body": f'{{"access_token":"{json_secret}"}}'},
            ]
        },
    )

    for rendered_surface in (str(error), repr(error), repr(error.metadata)):
        assert bearer_secret not in rendered_surface
        assert json_secret not in rendered_surface


def test_subscription_error_secret_values_are_redacted_from_metadata() -> None:
    secret = "opaque-metadata-secret"
    error = SubscriptionTransportError(
        "transport failed",
        provider=SubscriptionProvider.OPENAI,
        metadata={
            "detail": secret,
            "nested": [secret, {"detail": secret}],
        },
        secret_values=(secret,),
    )

    assert secret not in repr(error.metadata)


def test_subscription_public_exports_retain_existing_names() -> None:
    package_exports: dict[str, object] = {}
    exec("from mudidi.llm.subscriptions import *", package_exports)
    assert "SubscriptionStatus" in package_exports
    assert "SubscriptionRuntime" in package_exports
    assert "AuthMode" in package_exports

    type_exports: dict[str, object] = {}
    exec("from mudidi.llm.subscriptions.types import *", type_exports)
    assert "AuthMode" in type_exports
    assert "SubscriptionRuntime" in type_exports
