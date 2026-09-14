"""Focused fake-transport tests for Claude research-only subscription routing."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import gzip
import hashlib
import json
import threading
import zlib
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

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
from mudidi.llm.subscriptions.oauth import OAuthCallback
from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.types import SubscriptionCredential
from mudidi.llm.subscriptions import claude_research
from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend

_ACCESS_TOKEN = "claude-access-secret"
_REFRESH_TOKEN = "claude-refresh-secret"
_ROTATED_TOKEN = "claude-rotated-secret"


class _Response:
    def __init__(
        self,
        payload: bytes | str | dict[str, Any],
        *,
        status: int = 200,
        url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self.headers = dict(headers or {})
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        self.payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def close(self) -> None:
        return None


class _Store:
    def __init__(self, credential: SubscriptionCredential | None = None) -> None:
        self.credential = credential
        self.saved: list[SubscriptionCredential] = []
        self.deleted = False

    def save(self, provider, credential) -> None:  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.CLAUDE
        self.credential = credential
        self.saved.append(credential)

    def load(self, provider):  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.CLAUDE
        return self.credential

    def delete(self, provider) -> None:  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.CLAUDE
        self.credential = None
        self.deleted = True


def _credential(*, expires_at: datetime | None = None) -> SubscriptionCredential:
    return SubscriptionCredential(
        provider=SubscriptionProvider.CLAUDE,
        account_label="research@example.test",
        account_id="account-123",
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
        expires_at=expires_at,
    )


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
        "account": {"uuid": "account-123", "email_address": "research@example.test"},
        "organization": {"uuid": "org-123", "name": "Research workspace"},
        "scope": "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload",
    }


class _OAuth:
    def __init__(
        self,
        exchange: dict[str, Any] | None = None,
        refresh: dict[str, Any] | None = None,
    ) -> None:
        self.exchange_payload = exchange or _token_payload()
        self.refresh_payload = refresh or _token_payload(access_token=_ROTATED_TOKEN)
        self.authorization: tuple[str, dict[str, Any]] | None = None
        self.json_calls: list[dict[str, Any]] = []
        self.exchange_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []

    def request_json_token(
        self,
        endpoint: str,
        parameters: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.json_calls.append(
            {"endpoint": endpoint, "parameters": dict(parameters), **kwargs}
        )
        if parameters.get("grant_type") == "refresh_token":
            return self.refresh_payload
        return self.exchange_payload

    def build_authorization_url(self, endpoint: str, parameters: dict[str, Any]) -> str:
        self.authorization = (endpoint, parameters)
        from urllib.parse import urlencode

        return f"{endpoint}?{urlencode(parameters)}"

    def exchange_code(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.exchange_calls.append({"endpoint": endpoint, **kwargs})
        return self.exchange_payload

    def refresh(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.refresh_calls.append({"endpoint": endpoint, **kwargs})
        return self.refresh_payload


class _FormOnlyOAuth:
    def exchange_code(self, _endpoint: str, **_kwargs: Any) -> dict[str, Any]:
        return _token_payload()

    def refresh(self, _endpoint: str, **_kwargs: Any) -> dict[str, Any]:
        return _token_payload(access_token=_ROTATED_TOKEN)


class _Receiver:
    def __init__(self, callback: OAuthCallback) -> None:
        self.redirect_uri = "http://127.0.0.1:43123/callback"
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

        backend = ClaudeResearchBackend(
            store=store,
            oauth_client=oauth,
            receiver_factory=receiver_factory,
        )
        operation = backend.login
        if complete:
            transaction = backend.begin_login("http://127.0.0.1:43123/callback")

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
            target=run, args=(backend.refresh, refresh_done)
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


def _receiver_factory(box: list[_Receiver]):
    def factory(*, expected_state: str, **_kwargs: Any) -> _Receiver:
        receiver = _Receiver(
            OAuthCallback(code="authorization-code", state=expected_state)
        )
        box.append(receiver)
        return receiver

    return factory


class _RaisingReceiver:
    redirect_uri = "http://127.0.0.1:43123/callback"

    def receive(self, _authorization_url: str, **_kwargs: Any) -> OAuthCallback:
        raise SubscriptionAuthError("shared callback failure")

    def close(self) -> None:
        return None


def _message_response(
    text: str = "hello",
    *,
    usage: dict[str, Any] | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-sonnet-research",
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 4, "output_tokens": 3},
    }


def test_status_is_available_without_setup_gate() -> None:
    backend = ClaudeResearchBackend(store=_Store())

    status = backend.status()

    assert status.provider is SubscriptionProvider.CLAUDE
    assert status.authenticated is False
    assert status.metadata["subscription"] is True
    assert "opt_in_enabled" not in status.metadata
    assert "policy_warning" not in status.model_dump()


def test_login_builds_claude_authorization_request_and_exchanges_pkce_code() -> None:
    store = _Store()
    oauth = _OAuth()
    opened: list[str] = []
    receivers: list[_Receiver] = []
    backend = ClaudeResearchBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=_receiver_factory(receivers),
        opener=opened.append,
    )

    credential = backend.login()

    assert credential.provider is SubscriptionProvider.CLAUDE
    assert credential.account_id == "account-123"
    assert oauth.authorization is not None
    endpoint, parameters = oauth.authorization
    assert endpoint == "https://claude.ai/oauth/authorize"
    assert parameters["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert (
        parameters["scope"]
        == "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
    )
    assert parameters["code"] == "true"
    assert parameters["response_type"] == "code"
    assert parameters["redirect_uri"].startswith("http://127.0.0.1:")
    assert parameters["redirect_uri"].endswith("/callback")
    assert parameters["code_challenge_method"] == "S256"
    assert parameters["code_challenge"]
    assert parameters["state"]
    assert "code_verifier" not in parameters
    assert oauth.json_calls[0]["endpoint"] == "https://api.anthropic.com/v1/oauth/token"
    assert oauth.json_calls[0]["parameters"]["code"] == "authorization-code"
    assert oauth.json_calls[0]["parameters"]["code_verifier"]
    assert store.credential is credential
    assert opened == [receivers[0].authorization_url]
    assert receivers[0].closed is True


def test_login_maps_claude_callback_listener_failure_to_transport_error() -> None:
    def failing_receiver(**_kwargs: Any) -> Any:
        raise OSError("address already in use")

    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=_OAuth(),
        receiver_factory=failing_receiver,
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.login()

    assert raised.value.metadata["reason"] == "callback_listener_failed"


def test_two_step_complete_login_uses_claude_json_token_contract() -> None:
    store = _Store()
    oauth = _OAuth()
    backend = ClaudeResearchBackend(
        store=store,
        oauth_client=oauth,
    )

    transaction = backend.begin_login("http://127.0.0.1:43123/callback")
    credential = backend.complete_login(
        "authorization-code",
        transaction.state,
        transaction,
    )

    assert credential.provider is SubscriptionProvider.CLAUDE
    assert oauth.exchange_calls == []
    assert oauth.json_calls[-1]["parameters"] == {
        "grant_type": "authorization_code",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "code": "authorization-code",
        "state": transaction.state,
        "redirect_uri": "http://127.0.0.1:43123/callback",
        "code_verifier": transaction.verifier.get_secret_value(),
    }
    assert store.credential is credential


def test_login_rejects_callback_state_before_token_exchange() -> None:
    oauth = _OAuth()

    def wrong_receiver(*_args: Any, **_kwargs: Any) -> _Receiver:
        return _Receiver(OAuthCallback(code="authorization-code", state="wrong-state"))

    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=oauth,
        receiver_factory=wrong_receiver,
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()
    assert raised.value.metadata["reason"] == "state_mismatch"
    assert oauth.json_calls == []


def test_login_rejects_oauth_client_without_json_token_method() -> None:
    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=_FormOnlyOAuth(),
        receiver_factory=_receiver_factory([]),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.login()

    assert raised.value.metadata["reason"] == "token_exchange_unavailable"
    assert raised.value.provider is SubscriptionProvider.CLAUDE
    assert "policy_warning" not in raised.value.metadata


def test_refresh_rejects_oauth_client_without_json_token_method() -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        oauth_client=_FormOnlyOAuth(),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.refresh()

    assert raised.value.metadata["reason"] == "token_exchange_unavailable"
    assert raised.value.provider is SubscriptionProvider.CLAUDE
    assert "policy_warning" not in raised.value.metadata


def test_login_normalizes_shared_receiver_errors_to_claude_policy_metadata() -> None:
    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=_OAuth(),
        receiver_factory=lambda **_kwargs: _RaisingReceiver(),
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert raised.value.provider is SubscriptionProvider.CLAUDE
    assert "policy_warning" not in raised.value.metadata
    assert raised.value.metadata["reason"] == "callback_error"


def test_login_maps_token_endpoint_403_to_claude_policy_error() -> None:
    def fetch(_request: Any, **_kwargs: Any) -> _Response:
        return _Response({"error": {"message": "denied"}}, status=403)

    backend = ClaudeResearchBackend(
        store=_Store(),
        fetch=fetch,
        receiver_factory=_receiver_factory([]),
    )

    with pytest.raises(SubscriptionPolicyError) as raised:
        backend.login()

    assert raised.value.provider is SubscriptionProvider.CLAUDE
    assert raised.value.status == 403


def test_refresh_maps_token_endpoint_403_to_claude_policy_error() -> None:
    def fetch(_request: Any, **_kwargs: Any) -> _Response:
        return _Response({"error": {"message": "denied"}}, status=403)

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )

    with pytest.raises(SubscriptionPolicyError) as raised:
        backend.refresh()

    assert raised.value.provider is SubscriptionProvider.CLAUDE
    assert raised.value.status == 403
    assert "policy_warning" not in raised.value.metadata


def test_shared_oauth_client_exchanges_claude_json_and_refresh_rotates_token() -> None:
    requests: list[Any] = []
    payloads = [
        _token_payload(),
        _token_payload(access_token=_ROTATED_TOKEN, refresh_token=None),
    ]

    def fetch(request, **_kwargs: Any) -> _Response:
        requests.append(request)
        assert request.full_url == "https://api.anthropic.com/v1/oauth/token"
        return _Response(payloads.pop(0))

    receiver_box: list[_Receiver] = []
    backend = ClaudeResearchBackend(
        store=_Store(),
        fetch=fetch,
        receiver_factory=_receiver_factory(receiver_box),
    )
    logged_in = backend.login()
    refreshed = backend.refresh()

    assert logged_in.refresh_token is not None
    assert refreshed.access_token.get_secret_value() == _ROTATED_TOKEN
    assert refreshed.refresh_token is not None
    assert refreshed.refresh_token.get_secret_value() == _REFRESH_TOKEN
    exchange_body = json.loads(requests[0].data)
    refresh_body = json.loads(requests[1].data)
    assert exchange_body["grant_type"] == "authorization_code"
    assert exchange_body["state"]
    assert refresh_body["grant_type"] == "refresh_token"
    assert requests[0].headers["Content-type"] == "application/json"
    assert (
        requests[0].headers["User-agent"]
        == "anthropic-sdk-typescript/0.112.1 userOAuthProvider"
    )
    assert (
        requests[1].headers["User-agent"]
        == "anthropic-sdk-typescript/0.112.1 userOAuthProvider"
    )
    assert requests[1].headers["Content-type"] == "application/json"
    assert backend.status().authenticated is True


def test_logout_deletes_only_claude_credential() -> None:
    store = _Store(_credential())
    backend = ClaudeResearchBackend(store=store)

    backend.logout()

    assert store.deleted is True
    assert store.credential is None


def test_complete_translates_messages_images_thinking_and_usage() -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(
            _message_response(
                "hello",
                usage={
                    "input_tokens": 12,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 2,
                    "output_tokens_details": {"thinking_tokens": 3},
                },
            )
        )

    backend = ClaudeResearchBackend(store=_Store(_credential()), fetch=fetch)
    result = backend.complete(
        CompletionRequest(
            model="anthropic/claude-sonnet-research",
            messages=[
                {"role": "system", "content": "Follow the policy."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZS1pbWFnZQ==",
                            },
                        },
                    ],
                },
                {"role": "assistant", "content": "Prior answer"},
            ],
            reasoning="high",
            max_tokens=10000,
        )
    )

    request = captured[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages?beta=true"
    assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert request.headers["User-agent"] == "claude-cli/2.1.257 (external, cli)"
    assert request.headers["Anthropic-beta"] == (
        "claude-code-20250219,oauth-2025-04-20,"
        "interleaved-thinking-2025-05-14,thinking-token-count-2026-05-13,"
        "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
        "mid-conversation-system-2026-04-07,effort-2025-11-24,"
        "fallback-credit-2026-06-01"
    )
    UUID(request.headers["X-claude-code-session-id"])
    assert request.headers["Connection"] == "keep-alive"
    assert request.headers["Accept-encoding"] == "gzip, deflate"
    assert all(key.lower() != "x-api-key" for key in request.headers)
    body = json.loads(request.data)
    assert body["model"] == "claude-sonnet-research"
    assert body["stream"] is True
    billing = body["system"][0]["text"]
    assert billing.startswith("x-anthropic-billing-header: cc_version=2.1.257.")
    assert "cch=00000" not in billing
    attestation = billing.split("cch=", maxsplit=1)[1].split(";", maxsplit=1)[0]
    assert len(attestation) == 5
    assert all(character in "0123456789abcdef" for character in attestation)
    assert body["system"][1] == {
        "type": "text",
        "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
    assert body["system"][2] == {"type": "text", "text": "Follow the policy."}
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this."},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "ZmFrZS1pbWFnZQ==",
                    },
                },
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Prior answer"}]},
    ]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert "temperature" not in body
    assert body["max_tokens"] == 10000
    assert result.text == "hello"
    assert result.finish_reason == "end_turn"
    assert result.usage == {
        "input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 19,
        "cached_tokens": 2,
        "reasoning_tokens": 3,
    }
    assert result.model == "claude-sonnet-research"
    assert result.provider is SubscriptionProvider.CLAUDE
    assert result.billing_mode == "subscription"


def test_complete_uses_stage_two_safe_default_timeout() -> None:
    observed_timeouts: list[float] = []

    def fetch(_request: Any, **kwargs: Any) -> _Response:
        observed_timeouts.append(kwargs["timeout"])
        return _Response(_message_response())

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Discover MDF parse rules."}],
        )
    )

    assert observed_timeouts == [120.0]


def test_developer_messages_append_to_claude_system_blocks() -> None:
    captured: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_message_response())

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[
                {"role": "developer", "content": "Developer policy"},
                {"role": "user", "content": "Hi"},
            ],
        )
    )

    body = json.loads(captured[0].data)
    assert body["system"][1:] == [
        {
            "type": "text",
            "text": claude_research._IDENTITY_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {"type": "text", "text": "Developer policy"},
    ]


@pytest.mark.parametrize("mime_type", ["text/plain", "application/octet-stream"])
def test_non_image_base64_mime_types_fail_before_transport(mime_type: str) -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: calls.append(request),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        f"data:{mime_type};base64,ZmFrZS1pbWFnZQ=="
                                    ),
                                },
                            }
                        ],
                    }
                ],
            )
        )

    assert raised.value.metadata["reason"] == "image_input_unsupported"
    assert calls == []


def test_https_image_url_is_translated_to_messages_image_source() -> None:
    captured: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_message_response())

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://images.example.test/picture.png"
                            },
                        }
                    ],
                }
            ],
        )
    )

    body = json.loads(captured[0].data)
    assert body["messages"][0]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "url",
                "url": "https://images.example.test/picture.png",
            },
        }
    ]


def test_data_uri_mime_cannot_be_overridden_by_explicit_image_mime() -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: (
            calls.append(request) or _Response(_message_response())
        ),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:text/plain;base64,ZmFrZS1pbWFnZQ==",
                                    "mime_type": "image/png",
                                },
                            }
                        ],
                    }
                ],
            )
        )

    assert raised.value.metadata["reason"] == "image_input_unsupported"
    assert calls == []


@pytest.mark.parametrize(
    ("reasoning", "max_tokens", "expected_budget"),
    [
        ("low", 2048, 1024),
        ("medium", 8192, 4096),
        ("high", 10000, 8192),
    ],
)
def test_thinking_budgets_fit_explicit_output_limits(
    reasoning: str,
    max_tokens: int,
    expected_budget: int,
) -> None:
    captured: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_message_response())

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Think"}],
            reasoning=reasoning,
            max_tokens=max_tokens,
        )
    )

    body = json.loads(captured[0].data)
    assert body["thinking"] == {
        "type": "enabled",
        "budget_tokens": expected_budget,
    }
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


def test_default_thinking_budget_leaves_visible_output_room() -> None:
    captured: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_message_response())

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=fetch,
    )
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Think"}],
            reasoning="high",
        )
    )

    body = json.loads(captured[0].data)
    assert body["max_tokens"] == 16_384
    assert body["thinking"]["budget_tokens"] == 8192
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


@pytest.mark.parametrize(
    ("reasoning", "max_tokens"),
    [("low", 1024), ("medium", 4096), ("high", 8192)],
)
def test_explicit_thinking_budget_equal_to_output_limit_fails_before_transport(
    reasoning: str,
    max_tokens: int,
) -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: calls.append(request),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Think"}],
                reasoning=reasoning,
                max_tokens=max_tokens,
            )
        )

    assert raised.value.metadata["reason"] == "invalid_reasoning_budget"
    assert calls == []


def test_complete_structured_translates_schema_and_parses_json_response() -> None:
    captured: list[Any] = []
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_message_response('{"answer":"yes","count":2}'))

    backend = ClaudeResearchBackend(store=_Store(_credential()), fetch=fetch)
    result = backend.complete_structured(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Return JSON"}],
            schema=schema,
        )
    )

    body = json.loads(captured[0].data)
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }
    assert result.structured_json == {"answer": "yes", "count": 2}


def test_one_401_refresh_retry_uses_rotated_oauth_token() -> None:
    calls: list[Any] = []
    responses = [_Response({}, status=401), _Response(_message_response("recovered"))]
    oauth = _OAuth(refresh=_token_payload(access_token=_ROTATED_TOKEN))

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return responses.pop(0)

    store = _Store(_credential())
    backend = ClaudeResearchBackend(store=store, oauth_client=oauth, fetch=fetch)

    result = backend.complete(
        CompletionRequest(
            model="claude-sonnet-research", messages=[{"role": "user", "content": "Hi"}]
        )
    )

    assert result.text == "recovered"
    assert len(calls) == 2
    assert len(oauth.json_calls) == 1
    assert calls[0].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert calls[1].headers["Authorization"] == f"Bearer {_ROTATED_TOKEN}"


def test_second_401_is_expired_without_second_refresh() -> None:
    calls: list[Any] = []
    oauth = _OAuth(refresh=_token_payload(access_token=_ROTATED_TOKEN))

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response({}, status=401)

    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        oauth_client=oauth,
        fetch=fetch,
    )

    with pytest.raises(SubscriptionTokenExpired) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )

    assert raised.value.status == 401
    assert len(calls) == 2
    assert len(oauth.json_calls) == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (403, SubscriptionPolicyError),
        (429, SubscriptionTransportError),
        (500, SubscriptionTransportError),
    ],
)
def test_provider_errors_are_typed_secret_safe_and_do_not_fallback(
    status: int,
    error_type: type[Exception],
) -> None:
    calls: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response({"error": {"message": _ACCESS_TOKEN}}, status=status)

    backend = ClaudeResearchBackend(store=_Store(_credential()), fetch=fetch)

    with pytest.raises(error_type) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )

    assert _ACCESS_TOKEN not in repr(raised.value)
    assert _REFRESH_TOKEN not in repr(raised.value)
    assert "api.openai.com" not in repr(raised.value)
    assert calls


def test_200_policy_error_payload_fails_closed() -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response(
            {"error": {"type": "permission_error", "message": "blocked"}}
        ),
    )

    with pytest.raises(SubscriptionPolicyError) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )
    assert raised.value.metadata["reason"] == "policy_rejected"


def test_unsupported_shapes_and_schema_fail_before_transport() -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[
                    {"role": "user", "content": [{"type": "audio", "data": "abc"}]}
                ],
            )
        )
    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete_structured(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "properties": {}, "pattern": "unsupported"},
            )
        )
    assert calls == []


@pytest.mark.parametrize("tool_key", ["tool_calls", "tool_call", "function_call"])
def test_rejects_mixed_text_and_tool_call_message(tool_key: str) -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[
                    {"role": "user", "content": "Continue"},
                    {
                        "role": "assistant",
                        "content": "Prior answer",
                        tool_key: {"name": "lookup"},
                    },
                ],
            )
        )

    assert raised.value.metadata["reason"] == "tool_input_unsupported"
    assert calls == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {
            "type": "object",
            "properties": {
                "nested": {"type": "object", "properties": {}},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
            "additionalProperties": False,
        },
    ],
)
def test_structured_schema_requires_false_additional_properties(
    schema: dict[str, Any],
) -> None:
    calls: list[Any] = []
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: calls.append(request),
    )

    with pytest.raises(SubscriptionUnsupportedRequest) as raised:
        backend.complete_structured(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema=schema,
            )
        )

    assert raised.value.metadata["reason"] == "unsupported_schema_keyword"
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"content": [{"type": "text", "text": "content-only"}]},
        {
            "type": "message",
            "role": "user",
            "model": "claude-sonnet-research",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "wrong role"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "model": "",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "missing model"}],
        },
    ],
)
def test_messages_success_envelope_is_validated_before_content(
    payload: dict[str, Any],
) -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: _Response(payload),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )

    assert raised.value.metadata["reason"] == "malformed_response"


def test_thought_only_messages_response_is_rejected() -> None:
    payload = _message_response()
    payload["content"] = [{"type": "thinking", "thinking": "hidden"}]
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: _Response(payload),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )

    assert raised.value.metadata["reason"] == "missing_visible_text"


@pytest.mark.parametrize(
    ("schema", "response_text"),
    [
        ({"type": "integer", "enum": [1]}, "true"),
        ({"type": "boolean", "const": True}, "1"),
    ],
)
def test_structured_schema_enum_and_const_use_strict_json_types(
    schema: dict[str, Any],
    response_text: str,
) -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: _Response(_message_response(response_text)),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete_structured(
            CompletionRequest(
                model="claude-sonnet-research",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema=schema,
            )
        )

    assert raised.value.metadata["reason"] == "structured_schema_mismatch"


@pytest.mark.parametrize(
    ("schema", "response_text"),
    [
        (
            {
                "type": "object",
                "const": {"answer": "ok"},
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
            '{"answer":"ok"}',
        ),
        (
            {
                "type": "object",
                "enum": [{"answer": "ok"}],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
            '{"answer":"ok"}',
        ),
        ({"type": "array", "const": [1, {"answer": "ok"}]}, '[1,{"answer":"ok"}]'),
        ({"type": "array", "enum": [[1, 2]]}, "[1,2]"),
    ],
)
def test_structured_schema_matches_json_container_values(
    schema: dict[str, Any],
    response_text: str,
) -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: _Response(_message_response(response_text)),
    )

    result = backend.complete_structured(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Return JSON"}],
            schema=schema,
        )
    )

    assert result.structured_json == json.loads(response_text)


def test_token_material_and_api_key_environment_values_are_not_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-sentinel")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key-sentinel")
    tracked_names = (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    )
    before = {name: claude_research.os.environ.get(name) for name in tracked_names}
    lookups: list[str] = []
    writes: list[tuple[str, str]] = []
    original_getenv = claude_research.os.getenv

    def getenv(name: str, *args: Any) -> str | None:
        lookups.append(name)
        return original_getenv(name, *args)

    def putenv(name: str, value: str) -> None:
        writes.append(("set", name))

    def unsetenv(name: str) -> None:
        writes.append(("unset", name))

    monkeypatch.setattr(claude_research.os, "getenv", getenv)
    monkeypatch.setattr(claude_research.os, "putenv", putenv)
    monkeypatch.setattr(claude_research.os, "unsetenv", unsetenv)
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda request, **_kwargs: _Response(_message_response()),
    )

    status = backend.status()
    backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Hi"}],
        )
    )
    after = {name: claude_research.os.environ.get(name) for name in tracked_names}

    assert "api-key-sentinel" not in status.model_dump_json()
    assert "openai-key-sentinel" not in status.model_dump_json()
    assert _ACCESS_TOKEN not in status.model_dump_json()
    assert _REFRESH_TOKEN not in repr(backend)
    assert "ANTHROPIC_API_KEY" not in lookups
    assert "OPENAI_API_KEY" not in lookups
    assert lookups == []
    assert writes == []
    assert after == before


def test_pkce_callback_request_contains_valid_challenge() -> None:
    backend = ClaudeResearchBackend(store=_Store())
    challenge = PkceChallenge.generate()
    url = backend.build_authorization_url(challenge, "http://127.0.0.1:43123/callback")
    query = parse_qs(urlsplit(url).query)

    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge.code_challenge]
    assert query["state"] == [challenge.state]
    assert "code_verifier" not in query


def test_status_redacts_secret_bearing_identity_fields() -> None:
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.CLAUDE,
        account_label=_ACCESS_TOKEN,
        account_id=_REFRESH_TOKEN,
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )
    backend = ClaudeResearchBackend(
        store=_Store(credential),
    )

    status = backend.status()

    assert status.account_label == "Claude.ai subscription"
    assert status.metadata["account_label"] == "Claude.ai subscription"
    assert status.metadata["account_id"] is None
    assert _ACCESS_TOKEN not in status.model_dump_json()
    assert _REFRESH_TOKEN not in status.model_dump_json()


def test_refresh_redacts_previous_identity_secrets_after_rotation() -> None:
    old_access = "old-access-secret"
    old_refresh = "old-refresh-secret"
    current = SubscriptionCredential(
        provider=SubscriptionProvider.CLAUDE,
        account_label=old_access,
        account_id=old_refresh,
        access_token=old_access,
        refresh_token=old_refresh,
        metadata={"email": old_access, "name": old_refresh},
    )
    rotated = _token_payload(
        access_token=_ROTATED_TOKEN,
        refresh_token="rotated-refresh-secret",
    )
    rotated.pop("account")
    rotated.pop("scope")
    backend = ClaudeResearchBackend(
        store=_Store(current),
        oauth_client=_OAuth(refresh=rotated),
    )

    backend.refresh()
    status = backend.status()

    assert status.account_label == "Claude.ai subscription"
    assert old_access not in status.model_dump_json()
    assert old_refresh not in status.model_dump_json()


def test_claude_exposes_registered_login_redirect() -> None:
    assert ClaudeResearchBackend.login_redirect_uri == "http://localhost:54545/callback"


def test_claude_code_xxhash64_matches_reference_vector() -> None:
    assert claude_research._xxhash64(b"") == 0xEF46DB3751D8E999


def test_billing_fingerprint_samples_javascript_utf16_code_units() -> None:
    expected_suffix = hashlib.sha256(b"59cf53e54c78cfs2.1.257").hexdigest()[:3]

    header = claude_research._create_billing_header(
        "\N{GRINNING FACE}abcdefghijklmnopqrstuvwxyz"
    )

    assert f"cc_version=2.1.257.{expected_suffix};" in header


def test_billing_fingerprint_uses_first_user_text_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def billing_header(first_user_text: str) -> str:
        observed.append(first_user_text)
        return "x-anthropic-billing-header: test; cch=00000;"

    monkeypatch.setattr(claude_research, "_create_billing_header", billing_header)
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
    )

    backend._build_request_body(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "text", "text": "second"},
                    ],
                }
            ],
        ),
        structured=False,
    )

    assert observed == ["first"]


def test_token_expiry_reserves_refresh_skew() -> None:
    earliest = datetime.now(UTC) + timedelta(seconds=3_299)

    expires_at = ClaudeResearchBackend._token_expiry(
        {"expires_in": 3_600},
        previous=None,
    )

    latest = datetime.now(UTC) + timedelta(seconds=3_301)
    assert expires_at is not None
    assert earliest <= expires_at <= latest


def test_sse_requires_terminal_message_stop() -> None:
    payload = b"\n".join(
        [
            b"event: message_start",
            b'data: {"type":"message_start","message":{"type":"message","role":"assistant","model":"claude-sonnet-research","content":[],"stop_reason":null,"usage":{}}}',
            b"",
            b"event: content_block_start",
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"partial"}}',
            b"",
        ]
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        claude_research._decode_sse_message(payload)

    assert raised.value.metadata["reason"] == "malformed_response"


def test_complete_aggregates_anthropic_sse_events() -> None:
    payload = b"\n".join(
        [
            b"event: message_start",
            b'data: {"type":"message_start","message":{"type":"message","role":"assistant","model":"claude-sonnet-research","content":[],"stop_reason":null,"usage":{"input_tokens":4,"output_tokens":1}}}',
            b"",
            b"event: content_block_start",
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            b"",
            b"event: content_block_delta",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hel"}}',
            b"",
            b"event: content_block_delta",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}',
            b"",
            b"event: content_block_stop",
            b'data: {"type":"content_block_stop","index":0}',
            b"",
            b"event: message_delta",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
            b"",
            b"event: message_stop",
            b'data: {"type":"message_stop"}',
            b"",
        ]
    )
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response(payload),
    )

    result = backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Hi"}],
        )
    )

    assert result.text == "hello"
    assert result.finish_reason == "end_turn"
    assert result.usage == {
        "input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 7,
    }


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
    ],
)
def test_complete_decodes_advertised_transport_compression(
    encoding: str,
    encode: Any,
) -> None:
    payload = json.dumps(_message_response("compressed")).encode()
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response(
            encode(payload),
            headers={"Content-Encoding": encoding},
        ),
    )

    result = backend.complete(
        CompletionRequest(
            model="claude-sonnet-research",
            messages=[{"role": "user", "content": "Hi"}],
        )
    )

    assert result.text == "compressed"


def test_compressed_response_limit_is_enforced_during_decompression() -> None:
    backend = ClaudeResearchBackend(
        store=_Store(_credential()),
        max_response_bytes=128,
    )
    response = _Response(
        gzip.compress(b"x" * 129),
        headers={"Content-Encoding": "gzip"},
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend._decode_transport_body(response, response.payload)

    assert raised.value.metadata["reason"] == "response_too_large"


def test_login_bootstraps_missing_anthropic_account_identity() -> None:
    token_payload = _token_payload()
    token_payload.pop("account")
    token_payload.pop("organization")
    requests: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        return _Response(
            {
                "oauth_account": {
                    "account_uuid": "bootstrap-account",
                    "account_email": "bootstrap@example.test",
                    "organization_uuid": "bootstrap-org",
                    "organization_name": "Research workspace",
                }
            }
        )

    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=_OAuth(exchange=token_payload),
        fetch=fetch,
    )
    transaction = backend.begin_login("http://127.0.0.1:43123/callback")

    credential = backend.complete_login(
        "authorization-code",
        transaction.state,
        transaction,
    )

    assert len(requests) == 1
    assert requests[0].full_url == (
        "https://api.anthropic.com/api/claude_cli/bootstrap"
        "?entrypoint=cli&model=claude-opus-5"
    )
    assert requests[0].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert requests[0].headers["Anthropic-beta"] == "oauth-2025-04-20"
    assert credential.account_id == "bootstrap-account"
    assert credential.account_label == "bootstrap@example.test"
    assert credential.metadata["email"] == "bootstrap@example.test"
    assert credential.metadata["organization_id"] == "bootstrap-org"
    assert credential.metadata["organization_name"] == "Research workspace"


def test_login_fills_missing_identity_without_replacing_token_fields() -> None:
    token_payload = _token_payload()
    token_payload["account"] = {
        "uuid": "token-account",
        "name": "Token account",
    }
    token_payload["organization"] = {"uuid": "token-org"}

    backend = ClaudeResearchBackend(
        store=_Store(),
        oauth_client=_OAuth(exchange=token_payload),
        fetch=lambda *_args, **_kwargs: _Response(
            {
                "oauth_account": {
                    "account_uuid": "bootstrap-account",
                    "account_email": "bootstrap@example.test",
                    "organization_uuid": "bootstrap-org",
                    "organization_name": "Bootstrap organization",
                }
            }
        ),
    )
    transaction = backend.begin_login("http://127.0.0.1:43123/callback")

    credential = backend.complete_login(
        "authorization-code",
        transaction.state,
        transaction,
    )

    assert credential.account_id == "token-account"
    assert credential.metadata["email"] == "bootstrap@example.test"
    assert credential.metadata["name"] == "Token account"
    assert credential.metadata["organization_id"] == "token-org"
    assert credential.metadata["organization_name"] == "Bootstrap organization"


def test_refresh_preserves_identity_without_bootstrap_request() -> None:
    previous = SubscriptionCredential(
        provider=SubscriptionProvider.CLAUDE,
        account_label="previous@example.test",
        account_id="previous-account",
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
        metadata={
            "email": "previous@example.test",
            "organization_id": "previous-org",
            "organization_name": "Previous organization",
        },
    )
    refresh_payload = {
        "access_token": _ROTATED_TOKEN,
        "refresh_token": _REFRESH_TOKEN,
        "token_type": "Bearer",
        "expires_in": 3_600,
    }
    bootstrap_requests: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        bootstrap_requests.append(request)
        return _Response({})

    store = _Store(previous)
    backend = ClaudeResearchBackend(
        store=store,
        oauth_client=_OAuth(refresh=refresh_payload),
        fetch=fetch,
    )

    credential = backend.refresh()

    assert bootstrap_requests == []
    assert credential.account_id == "previous-account"
    assert credential.account_label == "previous@example.test"
    assert credential.metadata["email"] == "previous@example.test"
    assert credential.metadata["organization_id"] == "previous-org"
    assert credential.metadata["organization_name"] == "Previous organization"


def test_authenticated_model_discovery_returns_structured_newest_metadata() -> None:
    requests: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        return _Response(
            {
                "data": [
                    {
                        "id": "claude-opus-5",
                        "display_name": "Claude Opus 5",
                        "created_at": "2026-09-10T00:00:00Z",
                    },
                    {
                        "id": "claude-sonnet-4-6",
                        "display_name": "Claude Sonnet 4.6",
                        "created_at": "2026-02-17T00:00:00Z",
                    },
                ],
                "has_more": False,
                "last_id": "claude-sonnet-4-6",
            }
        )

    backend = ClaudeResearchBackend(store=_Store(_credential()), fetch=fetch)

    models = backend.list_models()

    assert [model.model_id for model in models] == [
        "claude-opus-5",
        "claude-sonnet-4-6",
    ]
    assert models[0].reasoning.efforts == ("low", "medium", "high", "xhigh", "max")
    assert models[0].release_at == datetime(2026, 9, 10, tzinfo=UTC)
    assert requests[0].get_method() == "GET"
    assert requests[0].full_url == (
        "https://api.anthropic.com/v1/models?limit=1000&beta=true"
    )
    assert requests[0].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
