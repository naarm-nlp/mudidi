"""Shared local contract tests for direct subscription providers.

Every provider adapter is exercised through the same normalized request/result,
refresh, error, redaction, structured-output, and image boundaries.  The fake
transport seams keep this suite deterministic and never perform live login or
network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import json
from typing import Any, Callable
from urllib.parse import urlencode

import pytest

from mudidi.config.yaml_config import AuthConfig
from mudidi.llm import client as llm_client
from mudidi.llm.subscriptions import (
    AuthMode,
    CompletionRequest,
    SubscriptionBackend,
    SubscriptionCredential,
    SubscriptionPolicyError,
    SubscriptionProvider,
    SubscriptionTokenExpired,
    SubscriptionTransportError,
    SubscriptionUnsupportedRequest,
)
from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend
from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend


_IMAGE_DATA = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["answer", "count"],
    "additionalProperties": False,
}
_STRUCTURED_TEXT = '{"answer":"contract","count":2}'


@dataclass(frozen=True)
class _ProviderCase:
    provider: SubscriptionProvider
    qualified_model: str
    native_model: str
    access_token: str
    refresh_token: str
    rotated_token: str


_PROVIDER_CASES = (
    _ProviderCase(
        provider=SubscriptionProvider.OPENAI,
        qualified_model="openai/gpt-5.6-terra",
        native_model="gpt-5.6-terra",
        access_token="contract-openai-access-secret",
        refresh_token="contract-openai-refresh-secret",
        rotated_token="contract-openai-rotated-secret",
    ),
    _ProviderCase(
        provider=SubscriptionProvider.CLAUDE,
        qualified_model="anthropic/claude-sonnet-4-6",
        native_model="claude-sonnet-4-6",
        access_token="contract-claude-access-secret",
        refresh_token="contract-claude-refresh-secret",
        rotated_token="contract-claude-rotated-secret",
    ),
)


class _Response:
    def __init__(
        self, payload: bytes | str | dict[str, Any], *, status: int = 200
    ) -> None:
        self.status = status
        self.url: str | None = None
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]

    def close(self) -> None:
        return None


class _Store:
    def __init__(
        self,
        provider: SubscriptionProvider,
        credential: SubscriptionCredential | None = None,
    ) -> None:
        self.provider = provider
        self.credential = credential
        self.saved: list[SubscriptionCredential] = []

    def save(
        self, provider: SubscriptionProvider, credential: SubscriptionCredential
    ) -> None:
        assert provider is self.provider
        self.credential = credential
        self.saved.append(credential)

    def load(self, provider: SubscriptionProvider) -> SubscriptionCredential | None:
        assert provider is self.provider
        return self.credential

    def delete(self, provider: SubscriptionProvider) -> None:
        assert provider is self.provider
        self.credential = None


class _OAuth:
    def __init__(self, case: _ProviderCase) -> None:
        self.case = case
        self.exchange_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []

    def build_authorization_url(
        self,
        endpoint: str,
        parameters: dict[str, Any],
    ) -> str:
        return f"{endpoint}?{urlencode(parameters)}"

    def exchange_code(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.exchange_calls.append({"endpoint": endpoint, **kwargs})
        return _token_payload(self.case, access_token=self.case.access_token)

    def refresh(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.refresh_calls.append({"endpoint": endpoint, **kwargs})
        return _token_payload(
            self.case,
            access_token=self.case.rotated_token,
            refresh_token=self.case.refresh_token,
        )

    def request_json_token(
        self,
        endpoint: str,
        parameters: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.json_calls.append(
            {"endpoint": endpoint, "parameters": parameters, **kwargs}
        )
        if parameters.get("grant_type") == "refresh_token":
            return _token_payload(
                self.case,
                access_token=self.case.rotated_token,
                refresh_token=self.case.refresh_token,
            )
        return _token_payload(self.case, access_token=self.case.access_token)


def _token_payload(
    case: _ProviderCase,
    *,
    access_token: str,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": case.refresh_token if refresh_token is None else refresh_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    return payload


def _credential(case: _ProviderCase) -> SubscriptionCredential:
    values: dict[str, Any] = {
        "provider": case.provider,
        "account_label": f"{case.provider.value} contract account",
        "account_id": f"{case.provider.value}-account",
        "access_token": case.access_token,
        "refresh_token": case.refresh_token,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "metadata": {
            "display_name": f"{case.provider.value} test account",
            "nested": {"safe": True},
        },
    }
    return SubscriptionCredential(**values)


def _backend(
    case: _ProviderCase,
    *,
    store: _Store,
    oauth: _OAuth | None = None,
    fetch: Callable[..., Any] | None = None,
) -> SubscriptionBackend:
    kwargs: dict[str, Any] = {
        "store": store,
        "oauth_client": oauth or _OAuth(case),
        "fetch": fetch,
    }
    backend_type: type[SubscriptionBackend]
    if case.provider is SubscriptionProvider.OPENAI:
        backend_type = OpenAICodexBackend
    else:
        backend_type = ClaudeResearchBackend
    return backend_type(**kwargs)  # type: ignore[arg-type]


def _success_payload(
    case: _ProviderCase,
    text: str = "contract answer",
) -> dict[str, Any]:
    if case.provider is SubscriptionProvider.OPENAI:
        return {
            "status": "completed",
            "model": case.native_model,
            "output_text": text,
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        }
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": case.native_model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


def _request_body(request: Any) -> dict[str, Any]:
    raw = request.data
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    assert isinstance(raw, (bytes, bytearray))
    value = json.loads(bytes(raw).decode("utf-8"))
    assert isinstance(value, dict)
    return value


def _image_payload(body: dict[str, Any], case: _ProviderCase) -> dict[str, Any]:
    if case.provider is SubscriptionProvider.OPENAI:
        value = body["input"][0]["content"][1]
    else:
        value = body["messages"][0]["content"][1]
    assert isinstance(value, dict)
    return value


def _schema_payload(body: dict[str, Any], case: _ProviderCase) -> dict[str, Any]:
    if case.provider is SubscriptionProvider.OPENAI:
        value = body["text"]["format"]["schema"]
    else:
        value = body["output_config"]["format"]["schema"]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_fake_request_is_normalized(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case))

    backend = _backend(case, store=store, fetch=fetch)
    result = backend.complete(
        CompletionRequest(
            model=case.qualified_model,
            messages=[{"role": "user", "content": "Answer from the fake provider."}],
        )
    )

    assert len(captured) == 1
    body = _request_body(captured[0])
    assert body["model"] == case.native_model
    assert captured[0].headers["Authorization"] == f"Bearer {case.access_token}"
    assert case.access_token not in json.dumps(body)
    assert result.text == "contract answer"
    assert result.provider is case.provider
    assert result.model == case.native_model
    assert result.finish_reason in {"stop", "end_turn"}
    assert result.usage == {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
    assert result.billing_mode == "subscription"


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_refresh_rotates_and_status_redacts(
    case: _ProviderCase,
) -> None:
    store = _Store(case.provider, _credential(case))
    oauth = _OAuth(case)
    backend = _backend(case, store=store, oauth=oauth)

    replacement = backend.refresh()
    status = backend.status()

    assert replacement.access_token.get_secret_value() == case.rotated_token
    assert replacement.refresh_token is not None
    assert replacement.refresh_token.get_secret_value() == case.refresh_token
    assert store.saved[-1] is replacement
    assert oauth.refresh_calls or oauth.json_calls
    refresh_request = (oauth.refresh_calls or oauth.json_calls)[0]
    refresh_text = repr(refresh_request)
    assert case.refresh_token in refresh_text
    public_text = " ".join(
        (
            repr(status),
            status.model_dump_json(),
            repr(status.redacted_metadata),
        )
    )
    assert case.access_token not in public_text
    assert case.refresh_token not in public_text
    assert case.rotated_token not in public_text
    assert status.provider is case.provider
    assert status.authenticated is True


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_provider_error_is_typed_and_secret_safe(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))
    error_body = (
        "provider rejected access_token="
        f"{case.access_token} refresh_token={case.refresh_token}"
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(error_body, status=403)

    backend = _backend(case, store=store, fetch=fetch)
    with pytest.raises(SubscriptionPolicyError) as raised:
        backend.complete(
            CompletionRequest(
                model=case.qualified_model,
                messages=[{"role": "user", "content": "This must fail closed."}],
            )
        )

    error = raised.value
    assert error.provider is case.provider
    assert error.status == 403
    assert error.metadata["reason"] == "policy_rejected"
    assert len(captured) == 1
    error_text = " ".join((str(error), repr(error), repr(error.metadata)))
    assert case.access_token not in error_text
    assert case.refresh_token not in error_text


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_malformed_response_with_secret_fails_closed(
    case: _ProviderCase,
) -> None:
    store = _Store(case.provider, _credential(case))
    malformed = (
        "not-json response authorization=Bearer "
        f"{case.access_token} token={case.refresh_token}"
    )

    def fetch(_request: Any, **_kwargs: Any) -> _Response:
        return _Response(malformed)

    backend = _backend(case, store=store, fetch=fetch)
    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model=case.qualified_model,
                messages=[{"role": "user", "content": "Malformed response."}],
            )
        )

    error = raised.value
    assert error.metadata["reason"] in {"malformed_response", "missing_visible_text"}
    error_text = " ".join((str(error), repr(error), repr(error.metadata)))
    assert case.access_token not in error_text
    assert case.refresh_token not in error_text


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_structured_schema_is_forwarded_and_validated(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case, _STRUCTURED_TEXT))

    backend = _backend(case, store=store, fetch=fetch)
    result = backend.complete_structured(
        CompletionRequest(
            model=case.qualified_model,
            messages=[{"role": "user", "content": "Return the contract schema."}],
            schema=_SCHEMA,
        )
    )

    assert _schema_payload(_request_body(captured[0]), case) == _SCHEMA
    assert result.text == _STRUCTURED_TEXT
    assert result.structured_json == {"answer": "contract", "count": 2}


@pytest.mark.parametrize(
    "invalid_text",
    (
        '{"answer":"structured-type-secret","count":"not-an-integer"}',
        '{"answer":"contract","count":2,"unexpected":"structured-extra-secret"}',
    ),
    ids=("type_mismatch", "additional_property"),
)
@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_structured_schema_mismatch_is_typed_and_secret_safe(
    case: _ProviderCase,
    invalid_text: str,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case, invalid_text))

    backend = _backend(case, store=store, fetch=fetch)
    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete_structured(
            CompletionRequest(
                model=case.qualified_model,
                messages=[{"role": "user", "content": "Reject this schema mismatch."}],
                schema=_SCHEMA,
            )
        )

    assert len(captured) == 1
    assert raised.value.metadata["reason"] == "structured_schema_mismatch"
    error_text = " ".join((str(raised.value), repr(raised.value)))
    for secret in (
        case.access_token,
        case.refresh_token,
        "structured-type-secret",
        "structured-extra-secret",
    ):
        assert secret not in error_text


_STRICT_VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "flag": {"enum": [True]},
        "count": {"const": 1},
        "nested": {
            "type": "object",
            "properties": {"value": {"const": 1}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "items": {"type": "array", "items": {"enum": [True]}},
    },
    "required": ["flag", "count", "nested", "items"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "invalid_text",
    (
        '{"flag":1,"count":1,"nested":{"value":1},"items":[true]}',
        '{"flag":true,"count":true,"nested":{"value":1},"items":[true]}',
        '{"flag":true,"count":1,"nested":{"value":true},"items":[true]}',
        '{"flag":true,"count":1,"nested":{"value":1},"items":[1]}',
    ),
    ids=(
        "number_does_not_match_boolean",
        "boolean_does_not_match_number",
        "nested_boolean_does_not_match_number",
        "list_number_does_not_match_boolean",
    ),
)
@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_structured_enum_and_const_use_strict_json_equality(
    case: _ProviderCase,
    invalid_text: str,
) -> None:
    captured: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case, invalid_text))

    backend = _backend(
        case, store=_Store(case.provider, _credential(case)), fetch=fetch
    )
    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete_structured(
            CompletionRequest(
                model=case.qualified_model,
                messages=[{"role": "user", "content": "Reject bool/number coercion."}],
                schema=_STRICT_VALUE_SCHEMA,
            )
        )

    assert raised.value.metadata["reason"] == "structured_schema_mismatch"
    assert len(captured) == 1


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_image_input_accepts_mudidi_data_uri(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case))

    backend = _backend(case, store=store, fetch=fetch)
    backend.complete(
        CompletionRequest(
            model=case.qualified_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read this image."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{_IMAGE_DATA}",
                            },
                        },
                    ],
                }
            ],
        )
    )

    decoded_image = base64.b64decode(_IMAGE_DATA, validate=True)
    assert decoded_image.startswith(b"\x89PNG\r\n\x1a\n")
    image = _image_payload(_request_body(captured[0]), case)
    if case.provider is SubscriptionProvider.OPENAI:
        assert image == {
            "type": "input_image",
            "image_url": f"data:image/png;base64,{_IMAGE_DATA}",
        }
    else:
        assert image == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _IMAGE_DATA,
            },
        }


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_malformed_image_is_rejected_before_transport(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case))

    backend = _backend(case, store=store, fetch=fetch)
    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete(
            CompletionRequest(
                model=case.qualified_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,not-valid-base64",
                                },
                            }
                        ],
                    }
                ],
            )
        )

    assert raised.value.metadata["reason"] == "image_input_unsupported"
    assert captured == []


def test_claude_runtime_resolution_is_available_without_setup_gate() -> None:
    calls: list[Any] = []
    case = next(
        case for case in _PROVIDER_CASES if case.provider is SubscriptionProvider.CLAUDE
    )
    backend = ClaudeResearchBackend(
        store=_Store(case.provider, _credential(case)),
        fetch=lambda request, **kwargs: calls.append((request, kwargs)),
    )
    auth = AuthConfig(
        mode=AuthMode.SUBSCRIPTION,
        providers=(SubscriptionProvider.CLAUDE,),
    )

    runtime = llm_client.resolve_subscription_runtime(auth, backend=backend)

    assert runtime is not None
    assert runtime.provider is SubscriptionProvider.CLAUDE
    status = runtime.backend.status()
    assert status.authenticated is True
    assert status.metadata["subscription"] is True
    assert "research_only" not in status.metadata
    assert "opt_in_enabled" not in status.metadata
    assert calls == []


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_capabilities_and_missing_credential_are_typed(
    case: _ProviderCase,
) -> None:
    backend = _backend(case, store=_Store(case.provider))

    assert backend.capabilities.image_input is True
    assert backend.capabilities.structured_output is True
    with pytest.raises(SubscriptionTokenExpired) as raised:
        backend.complete(
            CompletionRequest(
                model=case.qualified_model,
                messages=[{"role": "user", "content": "No credential."}],
            )
        )
    assert raised.value.provider is case.provider
    assert raised.value.metadata["reason"] == "missing_credential"
    assert case.access_token not in repr(raised.value)
    assert case.refresh_token not in repr(raised.value)


@pytest.mark.parametrize(
    "case",
    _PROVIDER_CASES,
    ids=lambda case: case.provider.value,
)
def test_provider_contract_auth_header_is_the_only_secret_bearing_request_field(
    case: _ProviderCase,
) -> None:
    captured: list[Any] = []
    store = _Store(case.provider, _credential(case))

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_success_payload(case))

    backend = _backend(case, store=store, fetch=fetch)
    backend.complete(
        CompletionRequest(
            model=case.qualified_model,
            messages=[{"role": "user", "content": "Keep request fields separated."}],
        )
    )

    request = captured[0]
    body = _request_body(request)
    headers = {str(key).lower(): str(value) for key, value in request.headers.items()}
    assert headers["authorization"] == f"Bearer {case.access_token}"
    assert all(
        case.access_token not in value
        for key, value in headers.items()
        if key != "authorization"
    )
    assert case.access_token not in json.dumps(body)
    assert "api-key" not in headers
    assert "x-api-key" not in headers


__all__ = ["_PROVIDER_CASES"]
