"""Focused fake-transport tests for the OpenAI Codex subscription adapter."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from mudidi.llm.subscriptions import (
    CompletionRequest,
    SubscriptionAuthError,
    SubscriptionPolicyError,
    SubscriptionProvider,
    SubscriptionTokenExpired,
    SubscriptionTransportError,
    SubscriptionUnsupportedRequest,
)
from mudidi.llm.subscriptions.oauth import LoopbackOAuthReceiver, OAuthCallback
from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.types import SubscriptionCredential
from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend


_ACCESS_TOKEN = "access-secret-value"
_REFRESH_TOKEN = "refresh-secret-value"


class _Response:
    def __init__(
        self, payload: bytes | str | dict[str, Any], *, status: int = 200
    ) -> None:
        self.status = status
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        self.payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]

    def close(self) -> None:
        return None


class _Store:
    def __init__(self, credential: SubscriptionCredential | None = None) -> None:
        self.credential = credential
        self.saved: list[SubscriptionCredential] = []

    def save(self, provider, credential) -> None:  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.OPENAI
        self.credential = credential
        self.saved.append(credential)

    def load(self, provider):  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.OPENAI
        return self.credential

    def delete(self, provider) -> None:  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.OPENAI
        self.credential = None


def _jwt_with_account(
    account_id: str = "acct_123",
    *,
    email: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id}
    }
    if email is not None:
        payload["https://api.openai.com/profile"] = {"email": email}
    encoded = urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def _token_payload(
    *,
    access_token: str = _ACCESS_TOKEN,
    refresh_token: str | None = _REFRESH_TOKEN,
    expires_in: int = 3600,
) -> dict[str, Any]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }


def _credential(*, expires_at: datetime | None = None) -> SubscriptionCredential:
    return SubscriptionCredential(
        provider=SubscriptionProvider.OPENAI,
        account_label="OpenAI Codex",
        account_id="acct_123",
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
        expires_at=expires_at,
    )


class _OAuth:
    def __init__(
        self,
        exchange: dict[str, Any] | None = None,
        refresh: dict[str, Any] | None = None,
    ) -> None:
        self.exchange_payload = exchange or _token_payload(
            access_token=_jwt_with_account()
        )
        self.refresh_payload = refresh or _token_payload(access_token="new-access")
        self.authorization: tuple[str, dict[str, str]] | None = None
        self.exchange_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []

    def build_authorization_url(self, endpoint: str, parameters: dict[str, str]) -> str:
        self.authorization = (endpoint, parameters)
        from urllib.parse import urlencode

        return f"{endpoint}?{urlencode(parameters)}"

    def exchange_code(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.exchange_calls.append({"endpoint": endpoint, **kwargs})
        return self.exchange_payload

    def refresh(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.refresh_calls.append({"endpoint": endpoint, **kwargs})
        return self.refresh_payload


class _Receiver:
    def __init__(self, callback: OAuthCallback) -> None:
        self.redirect_uri = "http://localhost:1455/auth/callback"
        self.callback = callback
        self.authorization_url: str | None = None
        self.closed = False

    def receive(self, authorization_url: str, **kwargs: Any) -> OAuthCallback:
        self.authorization_url = authorization_url
        opener = kwargs.get("opener")
        if callable(opener):
            opener(authorization_url)
        return self.callback

    def close(self) -> None:
        self.closed = True


class _BlockingProviderLock:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.contender = threading.Event()
        self.release = threading.Event()
        self._calls = 0
        self._calls_lock = threading.Lock()

    @contextmanager
    def __call__(self, _provider):  # type: ignore[no-untyped-def]
        with self._calls_lock:
            self._calls += 1
            call_number = self._calls
        (self.entered if call_number == 1 else self.contender).set()
        if not self.release.wait(timeout=2):
            raise AssertionError("provider lock was not released")
        yield


def test_login_persistence_waits_for_provider_lock() -> None:
    for complete in (False, True):
        lock = _BlockingProviderLock()
        store = _Store(_credential())
        store.provider_lock = lock
        oauth = _OAuth()

        def receiver_factory(*, expected_state: str, **_kwargs: Any) -> _Receiver:
            return _Receiver(
                OAuthCallback(code="authorization-code", state=expected_state)
            )

        backend = OpenAICodexBackend(
            store=store,
            oauth_client=oauth,
            receiver_factory=receiver_factory,
        )
        operation = backend.login
        if complete:
            transaction = backend.begin_login("http://127.0.0.1:43123/auth/callback")

            def complete_login() -> SubscriptionCredential:
                return backend.complete_login(
                    "authorization-code",
                    transaction.state,
                    transaction,
                )

            operation = complete_login

        refresh_done = threading.Event()
        login_done = threading.Event()
        errors: list[BaseException] = []

        def run(operation, done: threading.Event) -> None:  # type: ignore[no-untyped-def]
            try:
                operation()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        refresh_thread = threading.Thread(
            target=run,
            args=(backend.refresh, refresh_done),
        )
        refresh_thread.start()
        assert lock.entered.wait(timeout=1)

        login_thread = threading.Thread(target=run, args=(operation, login_done))
        login_thread.start()
        contender_seen = lock.contender.wait(timeout=1)
        saved_while_locked = list(store.saved)
        login_waiting = login_thread.is_alive() and not login_done.is_set()

        lock.release.set()
        refresh_thread.join(timeout=2)
        login_thread.join(timeout=2)

        assert contender_seen
        assert saved_while_locked == []
        assert login_waiting
        assert refresh_done.is_set()
        assert login_done.is_set()
        assert errors == []
        assert len(store.saved) == 2


def test_login_builds_pi_codex_authorization_url_and_exchanges_pkce_code() -> None:
    store = _Store()
    oauth = _OAuth()
    opened: list[str] = []
    receiver_box: list[_Receiver] = []

    def receiver_factory(
        *, expected_state: str, pkce: PkceChallenge, **_kwargs: Any
    ) -> _Receiver:
        assert expected_state == pkce.state
        receiver = _Receiver(OAuthCallback(code="authorization-code", state=pkce.state))
        receiver_box.append(receiver)
        return receiver

    backend = OpenAICodexBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        opener=opened.append,
    )
    credential = backend.login()
    assert receiver_box[0].authorization_url is not None
    assert receiver_box[0].authorization_url.startswith(
        "https://auth.openai.com/oauth/authorize?"
    )
    assert opened == [receiver_box[0].authorization_url]
    assert credential.provider is SubscriptionProvider.OPENAI
    assert oauth.authorization is not None
    endpoint, parameters = oauth.authorization
    assert endpoint == "https://auth.openai.com/oauth/authorize"
    assert parameters["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert {
        "scope": parameters["scope"],
        "originator": parameters["originator"],
    } == {
        "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
        "originator": "codex_cli_rs",
    }
    assert parameters["response_type"] == "code"
    assert parameters["code_challenge_method"] == "S256"
    assert parameters["redirect_uri"] == "http://localhost:1455/auth/callback"
    assert parameters["state"]
    assert parameters["code_challenge"]
    assert "code_verifier" not in parameters
    assert oauth.exchange_calls[0]["code"] == "authorization-code"
    assert isinstance(oauth.exchange_calls[0]["code_verifier"], str)
    assert len(oauth.exchange_calls[0]["code_verifier"]) >= 43
    assert oauth.exchange_calls[0]["code_verifier"] != receiver_box[0].callback.state
    assert store.credential is credential
    assert receiver_box[0].closed is True


def test_token_profile_prefers_namespaced_email_over_account_id() -> None:
    backend = OpenAICodexBackend(store=_Store(), oauth_client=_OAuth())

    credential = backend._credential_from_token_response(  # type: ignore[attr-defined]
        _token_payload(
            access_token=_jwt_with_account(
                "acct_email",
                email=" Person@Example.COM ",
            )
        )
    )

    assert credential.account_id == "acct_email"
    assert credential.account_label == "person@example.com"
    assert credential.metadata["email"] == "person@example.com"


def test_token_profile_uses_id_token_email_when_access_token_omits_it() -> None:
    backend = OpenAICodexBackend(store=_Store(), oauth_client=_OAuth())
    payload = _token_payload(access_token=_jwt_with_account("acct_id_token"))
    payload["id_token"] = _jwt_with_account(
        "acct_id_token",
        email="id-token@example.com",
    )

    credential = backend._credential_from_token_response(  # type: ignore[attr-defined]
        payload
    )

    assert credential.account_id == "acct_id_token"
    assert credential.account_label == "id-token@example.com"
    assert credential.metadata["email"] == "id-token@example.com"


def test_status_derives_email_for_existing_account_id_credential() -> None:
    access_token = _jwt_with_account(
        "acct_existing",
        email="existing@example.com",
    )
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.OPENAI,
        account_label="acct_existing",
        account_id="acct_existing",
        access_token=access_token,
        refresh_token=_REFRESH_TOKEN,
    )
    store = _Store(credential)
    backend = OpenAICodexBackend(store=store, oauth_client=_OAuth())

    status = backend.status()

    assert status.account_label == "existing@example.com"
    assert store.credential is credential
    assert access_token not in repr(status)


def test_login_uses_shared_loopback_receiver_and_oauth_http_client() -> None:
    store = _Store()
    opened: list[str] = []
    receiver_box: list[LoopbackOAuthReceiver] = []
    token_requests: list[tuple[str, dict[str, list[str]]]] = []

    def fetch(request, timeout: float) -> _Response:  # type: ignore[no-untyped-def]
        assert timeout > 0
        token_requests.append(
            (
                request.full_url,
                parse_qs(bytes(request.data).decode(), keep_blank_values=True),
            )
        )
        return _Response(_token_payload(access_token=_jwt_with_account("acct_shared")))

    def receiver_factory(
        *, expected_state: str, pkce: PkceChallenge, **_kwargs: Any
    ) -> LoopbackOAuthReceiver:
        def listener(redirect_uri: str, callback) -> None:  # type: ignore[no-untyped-def]
            parsed = urlsplit(redirect_uri)
            assert parsed.hostname == "127.0.0.1"
            assert parsed.port is not None and parsed.port > 0
            assert parsed.path == "/auth/callback"
            callback(f"{redirect_uri}?code=shared-code&state={expected_state}")

        receiver = LoopbackOAuthReceiver(
            expected_state=expected_state,
            pkce=pkce,
            listener=listener,
            path="/auth/callback",
            opener=opened.append,
        )
        receiver_box.append(receiver)
        return receiver

    backend = OpenAICodexBackend(
        store=store, fetch=fetch, receiver_factory=receiver_factory
    )
    credential = backend.login()

    assert credential.account_id == "acct_shared"
    assert len(receiver_box) == 1
    assert receiver_box[0]._closed is True  # type: ignore[attr-defined]
    assert len(opened) == 1
    auth_query = parse_qs(urlsplit(opened[0]).query)
    assert auth_query["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert auth_query["scope"] == [
        "openid profile email offline_access api.connectors.read api.connectors.invoke"
    ]
    assert auth_query["originator"] == ["codex_cli_rs"]
    assert auth_query["state"]
    assert "code_verifier" not in auth_query
    assert token_requests[0][0] == "https://auth.openai.com/oauth/token"
    assert token_requests[0][1]["grant_type"] == ["authorization_code"]
    assert token_requests[0][1]["code"] == ["shared-code"]
    assert token_requests[0][1]["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert token_requests[0][1]["code_verifier"]


def test_shared_oauth_client_rejects_malformed_codex_token_response() -> None:
    opened: list[str] = []

    def fetch(_request, **_kwargs: Any) -> _Response:  # type: ignore[no-untyped-def]
        return _Response({})

    def receiver_factory(
        *, expected_state: str, pkce: PkceChallenge, **_kwargs: Any
    ) -> LoopbackOAuthReceiver:
        def listener(redirect_uri: str, callback) -> None:  # type: ignore[no-untyped-def]
            callback(f"{redirect_uri}?code=shared-code&state={expected_state}")

        return LoopbackOAuthReceiver(
            expected_state=expected_state,
            pkce=pkce,
            listener=listener,
            path="/auth/callback",
            opener=opened.append,
        )

    backend = OpenAICodexBackend(
        store=_Store(), fetch=fetch, receiver_factory=receiver_factory
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert "access_token" not in str(raised.value)
    assert "shared-code" not in repr(raised.value)


def test_refresh_uses_shared_oauth_http_client_form_exchange() -> None:
    requests: list[dict[str, list[str]]] = []

    def fetch(request, **_kwargs: Any) -> _Response:  # type: ignore[no-untyped-def]
        requests.append(parse_qs(bytes(request.data).decode(), keep_blank_values=True))
        return _Response(_token_payload(access_token="shared-rotated-access"))

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)
    refreshed = backend.refresh()

    assert refreshed.access_token.get_secret_value() == "shared-rotated-access"
    assert requests[0]["grant_type"] == ["refresh_token"]
    assert requests[0]["refresh_token"] == [_REFRESH_TOKEN]
    assert requests[0]["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]


def test_login_rejects_callback_state_mismatch_before_token_exchange() -> None:
    store = _Store()
    oauth = _OAuth()

    def receiver_factory(**_kwargs: Any) -> _Receiver:
        return _Receiver(OAuthCallback(code="authorization-code", state="wrong-state"))

    backend = OpenAICodexBackend(
        store=store, oauth_client=oauth, receiver_factory=receiver_factory
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert raised.value.metadata["category"] == "authentication"
    assert oauth.exchange_calls == []
    assert store.credential is None


def test_refresh_rotates_access_token_without_exposing_token_material() -> None:
    store = _Store(_credential())
    oauth = _OAuth(refresh=_token_payload(access_token="rotated-access"))
    backend = OpenAICodexBackend(store=store, oauth_client=oauth)

    refreshed = backend.refresh()

    assert refreshed.access_token.get_secret_value() == "rotated-access"
    assert oauth.refresh_calls[0]["refresh_token"] == _REFRESH_TOKEN
    assert store.credential is refreshed
    assert "rotated-access" not in str(backend.status())
    assert "rotated-access" not in repr(backend.status())


def test_malformed_or_expired_token_responses_are_typed_and_secret_safe() -> None:
    malformed = OpenAICodexBackend(store=_Store(), oauth_client=_OAuth(exchange={}))

    with pytest.raises(SubscriptionAuthError) as raised:
        malformed._credential_from_token_response({})  # type: ignore[attr-defined]

    assert "access_token" not in str(raised.value)
    assert "refresh-secret" not in repr(raised.value)

    expired_store = _Store(
        _credential(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    expired = OpenAICodexBackend(store=expired_store, oauth_client=_OAuth())
    status = expired.status()

    assert status.authenticated is False
    assert status.expires_at is not None
    assert _ACCESS_TOKEN not in repr(status)
    assert _REFRESH_TOKEN not in status.model_dump_json()


def test_logout_deletes_only_the_codex_subscription_record() -> None:
    store = _Store(_credential())
    backend = OpenAICodexBackend(store=store, oauth_client=_OAuth())

    backend.logout()

    assert store.credential is None
    assert backend.status().authenticated is False


def test_list_models_uses_authenticated_codex_catalog_without_curating() -> None:
    captured: dict[str, Any] = {}

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.headers)
        return _Response(
            {
                "models": [
                    {
                        "slug": "gpt-hidden",
                        "display_name": "Hidden",
                        "visibility": "hide",
                    },
                    {
                        "slug": "gpt-account-second",
                        "display_name": "Account Second",
                        "visibility": "list",
                        "supported_reasoning_levels": [
                            {"effort": "low"},
                            {"effort": "xhigh"},
                            {"effort": "ultra"},
                        ],
                        "default_reasoning_level": "xhigh",
                        "priority": 7,
                    },
                    {
                        "slug": "gpt-account-first",
                        "display_name": "Account First",
                        "visibility": "list",
                    },
                    {
                        "slug": "gpt-account-first",
                        "display_name": "Duplicate",
                        "visibility": "list",
                    },
                ]
            }
        )

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)

    models = backend.list_models()

    assert [model.model_id for model in models] == [
        "gpt-account-second",
        "gpt-account-first",
    ]
    assert models[0].reasoning.efforts == ("low", "xhigh")
    assert models[0].reasoning.default == "xhigh"
    assert models[0].provider_order == 7
    assert captured["url"] == (
        "https://chatgpt.com/backend-api/codex/models?client_version=0.0.0"
    )
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert (
        next(
            value
            for key, value in captured["headers"].items()
            if key.lower() == "chatgpt-account-id"
        )
        == "acct_123"
    )


def test_list_models_rejects_malformed_catalog_without_fallback() -> None:
    backend = OpenAICodexBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response({"unexpected": []}),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.list_models()

    assert raised.value.metadata["reason"] == "malformed_model_catalog"


def test_complete_translates_responses_request_and_normalizes_result() -> None:
    captured: dict[str, Any] = {}

    def fetch(request, timeout: float):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(bytes(request.data).decode())
        captured["timeout"] = timeout
        return _Response(
            {
                "id": "resp_123",
                "model": "gpt-5.1-codex",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Hello from Codex"}
                        ],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
            }
        )

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)
    result = backend.complete(
        CompletionRequest(
            model="openai/gpt-5.1-codex",
            messages=[
                {"role": "system", "content": "Be concise"},
                {"role": "user", "content": "Say hello"},
            ],
            reasoning="high",
            max_tokens=32,
            cache_key="cache-1",
        )
    )
    assert (
        next(
            value
            for key, value in captured["headers"].items()
            if key.lower() == "chatgpt-account-id"
        )
        == "acct_123"
    )

    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["headers"]["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert all(
        _ACCESS_TOKEN not in str(value)
        for key, value in captured["headers"].items()
        if key.lower() != "authorization"
    )
    assert _ACCESS_TOKEN not in json.dumps(captured["body"])
    assert captured["body"]["model"] == "gpt-5.1-codex"
    assert captured["body"]["instructions"] == "Be concise"
    assert captured["body"]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Say hello"}]}
    ]
    assert captured["body"]["reasoning"] == {"effort": "high"}
    assert "temperature" not in captured["body"]
    assert "max_output_tokens" not in captured["body"]
    assert captured["body"]["prompt_cache_key"] == "cache-1"
    assert result.text == "Hello from Codex"
    assert result.finish_reason == "stop"
    assert result.usage == {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15}
    assert result.provider is SubscriptionProvider.OPENAI
    assert result.billing_mode == "subscription"


def test_assistant_history_uses_input_text_items() -> None:
    captured: dict[str, Any] = {}

    def fetch(request, **_kwargs: Any) -> _Response:
        captured["body"] = json.loads(bytes(request.data).decode())
        return _Response({"status": "completed", "output_text": "follow-up"})

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)
    backend.complete(
        CompletionRequest(
            model="gpt-5.1-codex",
            messages=[
                {"role": "user", "content": "First turn"},
                {"role": "assistant", "content": "Prior answer"},
                {"role": "user", "content": "Continue"},
            ],
        )
    )

    assert captured["body"]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "First turn"}]},
        {
            "role": "assistant",
            "content": [{"type": "input_text", "text": "Prior answer"}],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_complete_structured_requests_json_schema_and_validates_json_text() -> None:
    captured: dict[str, Any] = {}

    def fetch(request, **_kwargs: Any) -> _Response:
        captured["body"] = json.loads(bytes(request.data).decode())
        return _Response(
            {
                "status": "completed",
                "output_text": '{"answer":"yes","count":2}',
                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            }
        )

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }
    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)

    result = backend.complete_structured(
        CompletionRequest(
            model="gpt-5.1-codex",
            messages=[{"role": "user", "content": "Return JSON"}],
            schema=schema,
        )
    )

    assert captured["body"]["text"]["format"] == {
        "type": "json_schema",
        "name": "mudidi_response",
        "schema": schema,
        "strict": True,
    }
    assert result.structured_json == {"answer": "yes", "count": 2}
    assert result.text == '{"answer":"yes","count":2}'


@pytest.mark.parametrize(
    "unsupported_keyword",
    ["pattern", "minLength", "minimum", "minItems", "oneOf"],
)
def test_structured_completion_rejects_unimplemented_schema_keywords(
    unsupported_keyword: str,
) -> None:
    calls: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response({"status": "completed", "output_text": "{}"})

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)
    schema: dict[str, Any] = {"type": "object"}
    if unsupported_keyword in {"pattern", "minLength"}:
        schema["properties"] = {
            "answer": {
                "type": "string",
                unsupported_keyword: "x" if unsupported_keyword == "pattern" else 1,
            }
        }
    elif unsupported_keyword in {"minimum"}:
        schema["properties"] = {"count": {"type": "number", unsupported_keyword: 1}}
    elif unsupported_keyword == "minItems":
        schema = {"type": "array", unsupported_keyword: 1}
    else:
        schema = {unsupported_keyword: [{"type": "object"}, {"type": "array"}]}

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete_structured(
            CompletionRequest(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema=schema,
            )
        )

    assert calls == []


def test_schema_false_additional_properties_rejects_unlisted_output_keys() -> None:
    calls: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response(
            {"status": "completed", "output_text": '{"unexpected":"value"}'}
        )

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)

    with pytest.raises(SubscriptionTransportError):
        backend.complete_structured(
            CompletionRequest(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "additionalProperties": False},
            )
        )

    assert len(calls) == 1


def test_schema_valued_additional_properties_fails_closed_before_transport() -> None:
    calls: list[Any] = []
    backend = OpenAICodexBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: (
            calls.append(request)
            or _Response({"status": "completed", "output_text": "{}"})
        ),
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete_structured(
            CompletionRequest(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "additionalProperties": {"type": "string"}},
            )
        )

    assert calls == []


def test_complete_rejects_unsupported_schema_before_transport() -> None:
    calls: list[Any] = []
    backend = OpenAICodexBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: (
            calls.append(request)
            or _Response({"status": "completed", "output_text": "{}"})
        ),
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(
            CompletionRequest(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string", "pattern": "yes"}},
                },
            )
        )

    assert calls == []


def test_unsupported_image_and_tool_shapes_fail_closed() -> None:
    backend = OpenAICodexBackend(
        store=_Store(_credential()), fetch=lambda *_args, **_kwargs: _Response({})
    )

    image_request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "data:image/png;base64,abc"}
                ],
            }
        ],
    )
    tool_request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[{"role": "assistant", "content": "x", "tool_calls": []}],
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(image_request)
    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(tool_request)


class _SequenceTransport:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request, **_kwargs: Any) -> _Response:
        self.calls.append(
            {"headers": dict(request.headers), "body": bytes(request.data)}
        )
        return self.responses.pop(0)


def test_401_refreshes_once_then_retries_with_rotated_bearer() -> None:
    transport = _SequenceTransport(
        [
            _Response({"error": "expired"}, status=401),
            _Response({"status": "completed", "output_text": "retried"}),
        ]
    )
    store = _Store(_credential())
    oauth = _OAuth(refresh=_token_payload(access_token="rotated-access"))
    backend = OpenAICodexBackend(store=store, oauth_client=oauth, fetch=transport)

    result = backend.complete(
        CompletionRequest(
            model="gpt-5.1-codex", messages=[{"role": "user", "content": "Hi"}]
        )
    )

    assert result.text == "retried"
    assert len(transport.calls) == 2
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer rotated-access"
    assert len(oauth.refresh_calls) == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (403, SubscriptionPolicyError),
        (429, SubscriptionTransportError),
        (500, SubscriptionTransportError),
        (502, SubscriptionTransportError),
    ],
)
def test_provider_http_failures_are_typed_without_fallback(
    status: int, error_type: type[Exception]
) -> None:
    calls: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response({"error": "provider failure"}, status=status)

    backend = OpenAICodexBackend(store=_Store(_credential()), fetch=fetch)

    with pytest.raises(error_type) as raised:
        backend.complete(
            CompletionRequest(
                model="gpt-5.1-codex", messages=[{"role": "user", "content": "Hi"}]
            )
        )

    assert raised.value.status == status
    assert calls
    assert _ACCESS_TOKEN not in str(raised.value)
    assert _REFRESH_TOKEN not in repr(raised.value)


def test_401_without_refresh_token_maps_to_expired_session() -> None:
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.OPENAI,
        account_label="OpenAI Codex",
        access_token=_ACCESS_TOKEN,
        refresh_token=None,
    )
    backend = OpenAICodexBackend(
        store=_Store(credential),
        fetch=lambda *_args, **_kwargs: _Response({}, status=401),
    )

    with pytest.raises(SubscriptionTokenExpired) as raised:
        backend.complete(
            CompletionRequest(
                model="gpt-5.1-codex", messages=[{"role": "user", "content": "Hi"}]
            )
        )

    assert raised.value.status == 401
    assert "refresh" not in str(raised.value).lower()


def test_sse_response_text_and_usage_are_normalized() -> None:
    sse = (
        "\n\n".join(
            [
                'data: {"type":"response.output_text.delta","delta":"Hello"}',
                'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}',
                "data: [DONE]",
            ]
        )
        + "\n\n"
    )
    backend = OpenAICodexBackend(
        store=_Store(_credential()), fetch=lambda *_args, **_kwargs: _Response(sse)
    )

    result = backend.complete(
        CompletionRequest(
            model="gpt-5.1-codex", messages=[{"role": "user", "content": "Hi"}]
        )
    )

    assert result.text == "Hello"
    assert result.finish_reason == "stop"
    assert result.usage["total_tokens"] == 3


def test_invalid_structured_json_is_secret_safe_transport_error() -> None:
    backend = OpenAICodexBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response(
            {"status": "completed", "output_text": "not-json"}
        ),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete_structured(
            CompletionRequest(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "additionalProperties": False},
            )
        )

    assert "not-json" not in str(raised.value)
    assert _ACCESS_TOKEN not in repr(raised.value)


def test_codex_translates_multimodal_image_content_for_responses() -> None:
    backend = OpenAICodexBackend(store=_Store(_credential()))
    data_uri = "data:image/png;base64,aGVsbG8="
    request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this"},
                    {"type": "image_url", "image_url": {"url": f"  {data_uri}  "}},
                ],
            }
        ],
    )

    body = backend._build_request_body(request, structured=False)

    assert backend.capabilities.image_input is True
    assert body["input"][0]["content"] == [
        {"type": "input_text", "text": "Read this"},
        {"type": "input_image", "image_url": data_uri},
    ]


@pytest.mark.parametrize(
    "image_part",
    [
        {
            "type": "image",
            "source": {"media_type": "image/png", "data": "%%%not-base64%%%"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64x,AA=="},
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/tiff;base64,AA=="},
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "https://example.test/page.tiff",
                "mime_type": "image/tiff",
            },
        },
    ],
)
def test_codex_rejects_malformed_or_unsupported_inline_images(
    image_part: dict[str, Any],
) -> None:
    backend = OpenAICodexBackend(store=_Store(_credential()))
    request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[{"role": "user", "content": [image_part]}],
    )

    with pytest.raises(SubscriptionUnsupportedRequest, match="image"):
        backend._build_request_body(request, structured=False)


@pytest.mark.parametrize("mime_type", ["", 0, False, None])
def test_codex_rejects_falsey_explicit_https_image_mime(
    mime_type: Any,
) -> None:
    backend = OpenAICodexBackend(store=_Store(_credential()))
    request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.test/image",
                            "mime_type": mime_type,
                        },
                    }
                ],
            }
        ],
    )

    with pytest.raises(SubscriptionUnsupportedRequest, match="image"):
        backend._build_request_body(request, structured=False)


@pytest.mark.parametrize(
    "mime_type",
    ["image/png", "image/jpeg", "image/gif", "image/webp"],
)
def test_codex_accepts_supported_explicit_https_image_mimes(mime_type: str) -> None:
    backend = OpenAICodexBackend(store=_Store(_credential()))
    url = "https://example.test/image"
    request = CompletionRequest(
        model="gpt-5.1-codex",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": url, "mime_type": mime_type},
                    }
                ],
            }
        ],
    )

    body = backend._build_request_body(request, structured=False)

    assert body["input"][0]["content"] == [
        {"type": "input_image", "image_url": url},
    ]


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer", "answer"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "required": ["ghost", "ghost"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": None,
            "additionalProperties": False,
        },
        {
            "type": "string",
            "required": None,
        },
    ],
)
def test_codex_rejects_invalid_strict_object_schemas(
    schema: dict[str, Any],
) -> None:
    from mudidi.llm.subscriptions.openai_codex import _require_supported_schema

    with pytest.raises(SubscriptionUnsupportedRequest, match="schema"):
        _require_supported_schema(schema)


def test_openai_exposes_registered_login_redirect() -> None:
    assert (
        OpenAICodexBackend.login_redirect_uri == "http://localhost:1455/auth/callback"
    )


def test_login_closes_receiver_when_authorization_url_build_fails() -> None:
    class FailingOAuth(_OAuth):
        def build_authorization_url(
            self,
            _endpoint: str,
            _parameters: dict[str, str],
        ) -> str:
            raise RuntimeError("authorization endpoint failed")

    receivers: list[_Receiver] = []

    def receiver_factory(*, expected_state: str, **_kwargs: Any) -> _Receiver:
        receiver = _Receiver(
            OAuthCallback(code="authorization-code", state=expected_state)
        )
        receivers.append(receiver)
        return receiver

    backend = OpenAICodexBackend(
        store=_Store(),
        oauth_client=FailingOAuth(),
        receiver_factory=receiver_factory,
    )

    with pytest.raises(SubscriptionTransportError):
        backend.login()
    assert receivers[0].closed is True


def test_login_maps_fixed_receiver_bind_failure_to_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = OpenAICodexBackend(store=_Store(), oauth_client=_OAuth())

    def failing_make_receiver(_challenge: PkceChallenge) -> Any:
        raise OSError("address already in use")

    monkeypatch.setattr(backend, "_make_receiver", failing_make_receiver)

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.login()
    assert raised.value.metadata["reason"] == "callback_listener_failed"
