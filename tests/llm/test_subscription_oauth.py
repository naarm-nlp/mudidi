"""Focused tests for loopback OAuth and bounded stdlib HTTP transport."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, urlopen

import pytest

from mudidi.llm.subscriptions import (
    SubscriptionAuthError,
    SubscriptionTransportError,
)
from mudidi.llm.subscriptions import (
    claude_research as claude_module,
    oauth as oauth_module,
    openai_codex as openai_module,
)
from mudidi.llm.subscriptions.oauth import LoopbackOAuthReceiver, OAuthHttpClient
from mudidi.llm.subscriptions.pkce import PkceChallenge
from typing import Any


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self, limit: int = -1) -> bytes:
        if limit >= 0:
            return self.payload[:limit]
        return self.payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_loopback_receiver_binds_127_0_0_1_on_an_ephemeral_port() -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value")
    parsed = urlsplit(receiver.redirect_uri)

    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    assert parsed.port > 0
    assert receiver.redirect_uri.startswith("http://127.0.0.1:")
    receiver.close()


def test_loopback_receiver_injected_listener_and_opener_complete_without_browser() -> (
    None
):
    challenge = PkceChallenge.generate()
    opened: list[str] = []

    def opener(url: str) -> None:
        opened.append(url)

    def listener(redirect_uri: str, callback) -> None:  # type: ignore[no-untyped-def]
        callback(f"{redirect_uri}?code=authorization-code&state={challenge.state}")

    receiver = LoopbackOAuthReceiver(
        expected_state=challenge.state,
        listener=listener,
        opener=opener,
    )
    result = receiver.receive("https://provider.example/authorize")

    assert result.code == "authorization-code"
    assert result.state == challenge.state
    assert opened == ["https://provider.example/authorize"]


@pytest.mark.parametrize(
    "query",
    [
        "code=authorization-code&state=wrong-state",
        "state=state-value",
    ],
)
def test_loopback_receiver_rejects_wrong_state_or_missing_code(query: str) -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value")

    with pytest.raises(SubscriptionAuthError):
        receiver.handle_callback(f"{receiver.redirect_uri}?{query}")
    receiver.close()


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(oauth_module, id="oauth"),
        pytest.param(openai_module, id="openai"),
        pytest.param(claude_module, id="claude"),
    ],
)
def test_default_subscription_openers_use_verified_ca_context(module: Any) -> None:
    https_handlers = [
        handler
        for handler in module._NO_REDIRECT_OPENER.handlers
        if isinstance(handler, HTTPSHandler)
    ]

    assert len(https_handlers) == 1
    context = https_handlers[0]._context
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()


def test_oauth_http_client_exchanges_code_with_bounded_https_request() -> None:
    captured: dict[str, object] = {}

    def fetch(request, timeout: float):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        return _Response(
            json.dumps(
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            ).encode()
        )

    challenge = PkceChallenge.generate()
    client = OAuthHttpClient(fetch=fetch, timeout=2.5)
    result = client.exchange_code(
        "https://provider.example/token",
        code="authorization-code",
        redirect_uri="http://127.0.0.1:43123/callback",
        client_id="client-id",
        code_verifier=challenge.verifier,
    )

    assert result["access_token"] == "access-secret"
    assert result["refresh_token"] == "refresh-secret"
    assert captured["url"] == "https://provider.example/token"
    assert captured["timeout"] == 2.5
    assert "code=authorization-code" in bytes(captured["body"]).decode()
    assert "code_verifier=" in bytes(captured["body"]).decode()


def test_oauth_http_client_posts_json_token_requests_with_bounded_validation() -> None:
    captured: list[dict[str, object]] = []

    def fetch(request, timeout: float):  # type: ignore[no-untyped-def]
        captured.append(
            {
                "url": request.full_url,
                "body": request.data,
                "timeout": timeout,
                "headers": dict(request.headers),
            }
        )
        return _Response(
            json.dumps(
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            ).encode()
        )

    client = OAuthHttpClient(fetch=fetch, timeout=2.5)
    result = client.request_json_token(
        "https://provider.example/token",
        {
            "grant_type": "authorization_code",
            "client_id": "client-id",
            "code": "authorization-code",
            "state": "state-value",
            "redirect_uri": "http://127.0.0.1:43123/callback",
            "code_verifier": "verifier-value",
        },
    )

    assert result["access_token"] == "access-secret"
    assert captured[0]["url"] == "https://provider.example/token"
    assert captured[0]["timeout"] == 2.5
    assert dict(captured[0]["headers"])["Content-type"] == "application/json"
    assert "User-agent" not in dict(captured[0]["headers"])
    assert json.loads(bytes(captured[0]["body"]))["grant_type"] == "authorization_code"
    assert json.loads(bytes(captured[0]["body"]))["state"] == "state-value"


@pytest.mark.parametrize("user_agent", ["", "claude-cli/2.1.260\nInjected: value"])
def test_oauth_http_client_rejects_invalid_user_agents(user_agent: str) -> None:
    with pytest.raises(ValueError, match="user_agent"):
        OAuthHttpClient(user_agent=user_agent)


def test_oauth_http_client_rejects_http_endpoints_and_malformed_token_payloads() -> (
    None
):
    client = OAuthHttpClient(fetch=lambda *_args, **_kwargs: _Response(b"{}"))
    challenge = PkceChallenge.generate()

    with pytest.raises(SubscriptionTransportError):
        client.exchange_code(
            "http://provider.example/token",
            code="code",
            redirect_uri="http://127.0.0.1:43123/callback",
            client_id="client-id",
            code_verifier=challenge.verifier,
        )

    with pytest.raises(SubscriptionAuthError) as raised:
        client.exchange_code(
            "https://provider.example/token",
            code="code",
            redirect_uri="http://127.0.0.1:43123/callback",
            client_id="client-id",
            code_verifier=challenge.verifier,
        )
    assert "access_token" not in str(raised.value)


def test_oauth_http_client_caps_response_body_without_leaking_details() -> None:
    secret = "access-secret-that-must-not-appear-in-errors"
    client = OAuthHttpClient(
        fetch=lambda *_args, **_kwargs: _Response(
            json.dumps({"access_token": secret}).encode() + b"x" * 64
        ),
        max_response_bytes=32,
    )
    challenge = PkceChallenge.generate()

    with pytest.raises(SubscriptionTransportError) as raised:
        client.exchange_code(
            "https://provider.example/token",
            code="code",
            redirect_uri="http://127.0.0.1:43123/callback",
            client_id="client-id",
            code_verifier=challenge.verifier,
        )
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)


def test_authorization_url_builder_does_not_create_double_question_marks() -> None:
    client = OAuthHttpClient(allowed_hosts={"provider.example"})

    result = client.build_authorization_url(
        "https://provider.example/authorize",
        {"client_id": "client-id", "state": "state-value"},
    )

    assert result == (
        "https://provider.example/authorize?client_id=client-id&state=state-value"
    )


class _RedirectingResponse(_Response):
    def __init__(self, payload: bytes, final_url: str) -> None:
        super().__init__(payload)
        self.final_url = final_url

    def geturl(self) -> str:
        return self.final_url


def test_oauth_http_client_rejects_a_redirected_token_endpoint() -> None:
    payload = json.dumps(
        {"access_token": "access-secret", "token_type": "Bearer"}
    ).encode()
    client = OAuthHttpClient(
        allowed_hosts={"provider.example"},
        fetch=lambda *_args, **_kwargs: _RedirectingResponse(
            payload, "https://evil.example/token"
        ),
    )

    with pytest.raises(SubscriptionTransportError):
        client.request_token(
            "https://provider.example/token",
            {"grant_type": "refresh_token"},
        )


def test_loopback_receiver_requires_an_ephemeral_port() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    fixed_port = int(probe.getsockname()[1])
    probe.close()

    receiver = None
    try:
        with pytest.raises(ValueError):
            receiver = LoopbackOAuthReceiver(
                expected_state="state-value",
                port=fixed_port,
            )
    finally:
        if receiver is not None:
            receiver.close()


@pytest.mark.parametrize(
    "query",
    [
        "code=authorization-code&state=state-value&error=",
        "code=authorization-code&state=state-value&error=denied&error=again",
    ],
)
def test_loopback_receiver_rejects_malformed_error_parameters(query: str) -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value")

    try:
        with pytest.raises(SubscriptionAuthError):
            receiver.handle_callback(f"{receiver.redirect_uri}?{query}")
    finally:
        receiver.close()


def test_loopback_receiver_requires_absolute_callback_authority_to_match() -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value")

    try:
        with pytest.raises(SubscriptionAuthError):
            receiver.handle_callback(
                "http:///callback?code=authorization-code&state=state-value"
            )
    finally:
        receiver.close()


def test_loopback_handler_rejects_a_mismatched_host_header() -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value", timeout=1)
    outcome: dict[str, object] = {}

    def receive() -> None:
        try:
            outcome["result"] = receiver.receive()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=receive)
    thread.start()
    deadline = time.monotonic() + 1
    while not receiver._server_started and time.monotonic() < deadline:
        time.sleep(0.005)

    parsed = urlsplit(receiver.redirect_uri)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=1) as client:
        client.sendall(
            (
                f"GET {parsed.path}?code=authorization-code&state=state-value "
                "HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        response = client.recv(4096)

    thread.join(timeout=1)
    assert response.split(b" ", 2)[1] == b"400"
    assert isinstance(outcome.get("error"), SubscriptionAuthError)
    assert "result" not in outcome


def test_loopback_receiver_timeout_is_not_stalled_by_an_incomplete_request() -> None:
    receiver = LoopbackOAuthReceiver(expected_state="state-value", timeout=0.05)
    outcome: dict[str, object] = {}

    def receive() -> None:
        try:
            outcome["result"] = receiver.receive()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=receive)
    thread.start()
    deadline = time.monotonic() + 1
    while not receiver._server_started and time.monotonic() < deadline:
        time.sleep(0.005)

    parsed = urlsplit(receiver.redirect_uri)
    client = socket.create_connection((parsed.hostname, parsed.port), timeout=1)
    client.sendall(
        (
            f"GET {parsed.path}?code=partial&state=state-value "
            "HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        ).encode()
    )
    thread.join(timeout=1)
    completed = not thread.is_alive()
    client.close()
    receiver.close()
    thread.join(timeout=1)

    assert completed
    assert isinstance(outcome.get("error"), SubscriptionTransportError)


def test_oauth_http_client_maps_an_overflowing_expiry_to_auth_error() -> None:
    client = OAuthHttpClient(
        fetch=lambda *_args, **_kwargs: _Response(
            json.dumps(
                {"access_token": "access-secret", "expires_in": 10**1000}
            ).encode()
        )
    )

    with pytest.raises(SubscriptionAuthError):
        client.request_token(
            "https://provider.example/token",
            {"grant_type": "refresh_token"},
        )


def _free_loopback_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_fixed_receiver_emits_localhost_registered_uri() -> None:
    port = _free_loopback_port()
    receiver = LoopbackOAuthReceiver(
        expected_state="state-value",
        host="127.0.0.1",
        port=port,
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
    )
    assert receiver.redirect_uri == f"http://localhost:{port}/auth/callback"
    receiver.close()


def test_container_mode_receiver_binds_container_interface_for_http_callback() -> None:
    port = _free_loopback_port()
    receiver = LoopbackOAuthReceiver(
        expected_state="state-value",
        host="0.0.0.0",
        port=port,
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
        container_mode=True,
        timeout=2,
    )
    outcome: dict[str, object] = {}

    def receive() -> None:
        try:
            outcome["result"] = receiver.receive()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=receive)
    thread.start()
    try:
        deadline = time.monotonic() + 1
        while not receiver._server_started and time.monotonic() < deadline:
            time.sleep(0.005)
        with urlopen(
            f"http://localhost:{port}/auth/callback"
            "?code=authorization-code&state=state-value",
            timeout=1,
        ) as callback:
            assert callback.status == 200
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert outcome["result"].code == "authorization-code"  # type: ignore[union-attr]
    finally:
        receiver.close()
        thread.join(timeout=1)


def test_container_mode_receiver_accepts_private_container_sources_only() -> None:
    receiver = LoopbackOAuthReceiver(
        expected_state="state-value",
        host="0.0.0.0",
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
        container_mode=True,
    )
    try:
        result = receiver.handle_callback(
            f"{receiver.redirect_uri}?code=authorization-code&state=state-value",
            client_address=("172.18.0.1", 12345),
        )
        assert result.code == "authorization-code"
        receiver.close()

        receiver = LoopbackOAuthReceiver(
            expected_state="state-value",
            host="0.0.0.0",
            path="/auth/callback",
            redirect_host="localhost",
            allow_fixed_port=True,
            container_mode=True,
        )
        with pytest.raises(SubscriptionAuthError) as raised:
            receiver.handle_callback(
                f"{receiver.redirect_uri}?code=authorization-code&state=state-value",
                client_address=("8.8.8.8", 12345),
            )
        assert raised.value.metadata["reason"] == "non_loopback_callback"
    finally:
        receiver.close()


def test_receiver_close_before_receive_is_terminal() -> None:
    listener_started = threading.Event()

    def listener(_redirect_uri: str, _callback) -> None:  # type: ignore[no-untyped-def]
        listener_started.set()

    receiver = LoopbackOAuthReceiver(expected_state="state-value", listener=listener)
    receiver.close()
    closed_error = receiver._error

    with pytest.raises(SubscriptionTransportError) as raised:
        receiver.receive(timeout=0.05)

    assert raised.value is closed_error
    assert raised.value.metadata["reason"] == "callback_closed"
    assert receiver._closed is True
    assert receiver._error is closed_error
    assert receiver._event.is_set()
    assert receiver._server_started is False
    assert not listener_started.is_set()
