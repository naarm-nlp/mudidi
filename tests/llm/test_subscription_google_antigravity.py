"""Focused fake-transport tests for direct Google Antigravity routing."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import threading
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs

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
from mudidi.llm.subscriptions.oauth import (
    LoopbackOAuthReceiver,
    OAuthCallback,
    OAuthHttpClient,
)
from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.types import SubscriptionCredential
from mudidi.llm.subscriptions.google_antigravity import (
    GoogleAntigravityBackend,
    google_cloud_project_from_environment,
    google_oauth_client_id_from_environment,
    google_oauth_client_secret_from_environment,
)


_ACCESS_TOKEN = "google-access-secret"
_REFRESH_TOKEN = "google-refresh-secret"
_ROTATED_TOKEN = "google-rotated-secret"
_PROJECT_ID = "project-123"
_ACCOUNT_ID = "account-456"
_CLIENT_ID = "desktop-client.apps.googleusercontent.com"
_CLIENT_SECRET = "desktop-client-secret"


class _Response:
    def __init__(
        self,
        payload: bytes | str | dict[str, Any],
        *,
        status: int = 200,
        url: str | None = None,
    ) -> None:
        self.status = status
        self.url = url
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
        assert provider is SubscriptionProvider.GOOGLE
        self.credential = credential
        self.saved.append(credential)

    def load(self, provider):  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.GOOGLE
        return self.credential

    def delete(self, provider) -> None:  # type: ignore[no-untyped-def]
        assert provider is SubscriptionProvider.GOOGLE
        self.credential = None


def _credential(*, expires_at: datetime | None = None) -> SubscriptionCredential:
    return SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="research@example.test",
        account_id=_ACCOUNT_ID,
        project_id=_PROJECT_ID,
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
        "email": "research@example.test",
        "sub": _ACCOUNT_ID,
        "project_id": _PROJECT_ID,
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
        self.exchange_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []

    def build_authorization_url(self, endpoint: str, parameters: dict[str, Any]) -> str:
        self.authorization = (endpoint, parameters)
        return f"{endpoint}?{__import__('urllib.parse', fromlist=['urlencode']).urlencode(parameters)}"

    def exchange_code(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.exchange_calls.append({"endpoint": endpoint, **kwargs})
        return self.exchange_payload

    def refresh(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.refresh_calls.append({"endpoint": endpoint, **kwargs})
        return self.refresh_payload


class _Receiver:
    def __init__(self, callback: OAuthCallback) -> None:
        self.redirect_uri = "http://127.0.0.1:51121/oauth-callback"
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

        def receiver_factory(**kwargs: Any) -> _Receiver:
            return _Receiver(
                OAuthCallback(code="auth-code", state=kwargs["expected_state"])
            )

        backend = GoogleAntigravityBackend(
            store=store,
            oauth_client=oauth,
            receiver_factory=receiver_factory,
            client_id=_CLIENT_ID,
            project_id=_PROJECT_ID,
        )
        operation = backend.login
        if complete:
            transaction = backend.begin_login(backend.login_redirect_uri)

            def complete_login() -> SubscriptionCredential:
                return backend.complete_login(
                    "auth-code",
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


def test_login_builds_google_authorization_url_and_exchanges_pkce_code() -> None:
    store = _Store()
    oauth = _OAuth()
    opened: list[str] = []
    receiver_box: list[_Receiver] = []

    def receiver_factory(**kwargs: Any) -> _Receiver:
        receiver = _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        )
        receiver_box.append(receiver)
        return receiver

    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        opener=opened.append,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        project_id=_PROJECT_ID,
    )

    credential = backend.login()

    assert credential.provider is SubscriptionProvider.GOOGLE
    assert credential.access_token.get_secret_value() == _ACCESS_TOKEN
    assert credential.refresh_token is not None
    assert credential.refresh_token.get_secret_value() == _REFRESH_TOKEN
    assert credential.project_id == _PROJECT_ID
    assert credential.account_id == _ACCOUNT_ID
    assert credential.account_label == "research@example.test"
    assert oauth.authorization is not None
    endpoint, parameters = oauth.authorization
    assert endpoint == "https://accounts.google.com/o/oauth2/v2/auth"
    assert parameters["client_id"] == _CLIENT_ID
    assert parameters["redirect_uri"] == backend.login_redirect_uri
    assert parameters["access_type"] == "offline"
    scopes = set(str(parameters["scope"]).split())
    assert "https://www.googleapis.com/auth/cclog" in scopes
    assert "https://www.googleapis.com/auth/experimentsandconfigs" in scopes
    assert parameters["code_challenge_method"] == "S256"
    assert parameters["code_challenge"]
    assert parameters["state"]
    assert "code_verifier" not in parameters
    assert oauth.exchange_calls[0]["endpoint"] == "https://oauth2.googleapis.com/token"
    assert oauth.exchange_calls[0]["client_id"] == _CLIENT_ID
    assert oauth.exchange_calls[0]["client_secret"] == _CLIENT_SECRET
    assert oauth.exchange_calls[0]["code"] == "auth-code"
    assert oauth.exchange_calls[0]["code_verifier"]
    assert opened == [receiver_box[0].authorization_url]
    assert receiver_box[0].closed is True
    assert store.saved == [credential]


def test_environment_registration_supports_fake_google_login_and_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUDIDI_GOOGLE_OAUTH_CLIENT_ID", _CLIENT_ID)
    oauth = _OAuth()
    store = _Store()

    def receiver_factory(**kwargs: Any) -> _Receiver:
        return _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        )

    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=google_oauth_client_id_from_environment(),
        project_id=_PROJECT_ID,
    )

    backend.login()
    backend.refresh()

    assert oauth.exchange_calls[0]["client_id"] == _CLIENT_ID
    assert oauth.refresh_calls[0]["client_id"] == _CLIENT_ID
    assert oauth.exchange_calls[0]["code"] == "auth-code"
    assert oauth.refresh_calls[0]["refresh_token"] == _REFRESH_TOKEN


def test_two_step_complete_login_persists_token_before_project_setup() -> None:
    token_payload = _token_payload()
    token_payload.pop("email")
    token_payload.pop("sub")
    token_payload.pop("project_id")
    oauth = _OAuth(exchange=token_payload)
    store = _Store()

    def fetch(_request: Any, **_kwargs: Any) -> _Response:
        pytest.fail("OAuth callback attempted Cloud Code project setup")

    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        fetch=fetch,
        client_id=_CLIENT_ID,
    )
    transaction = backend.begin_login(backend.login_redirect_uri)
    credential = backend.complete_login(
        "auth-code",
        transaction.state,
        transaction,
    )

    assert credential.account_id is None
    assert credential.project_id is None
    assert store.saved == [credential]
    assert store.credential is credential


def test_login_uses_shared_loopback_receiver_listener_and_opener_seams() -> None:
    oauth = _OAuth()
    opened: list[str] = []
    receiver_box: list[LoopbackOAuthReceiver] = []

    def receiver_factory(**kwargs: Any) -> LoopbackOAuthReceiver:
        challenge = kwargs["pkce"]

        def listener(_redirect_uri: str, callback: Any) -> None:
            callback({"code": "auth-code", "state": challenge.state})

        receiver = LoopbackOAuthReceiver(
            expected_state=challenge.state,
            pkce=challenge,
            path=kwargs["path"],
            listener=listener,
            opener=opened.append,
        )
        receiver_box.append(receiver)
        return receiver

    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=_CLIENT_ID,
        project_id=_PROJECT_ID,
    )

    backend.login()

    assert receiver_box[0].redirect_uri.startswith("http://127.0.0.1:")
    assert receiver_box[0].redirect_uri.endswith("/oauth-callback")
    assert opened and opened[0].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert oauth.exchange_calls[0]["code"] == "auth-code"


def test_shared_oauth_http_client_posts_google_code_and_refresh_forms() -> None:
    requests: list[tuple[str, dict[str, list[str]]]] = []
    payloads = [
        _token_payload(),
        _token_payload(access_token=_ROTATED_TOKEN, refresh_token=None),
    ]

    def fetch(request, **_kwargs: Any) -> _Response:
        assert request.full_url == "https://oauth2.googleapis.com/token"
        values = parse_qs(request.data.decode())
        requests.append((request.full_url, values))
        return _Response(payloads.pop(0))

    oauth = OAuthHttpClient(
        fetch=fetch,
        allowed_hosts={"accounts.google.com", "oauth2.googleapis.com"},
    )
    store = _Store()
    receiver_box: list[_Receiver] = []

    def receiver_factory(**kwargs: Any) -> _Receiver:
        receiver = _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        )
        receiver_box.append(receiver)
        return receiver

    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        project_id=_PROJECT_ID,
    )

    backend.login()
    backend.refresh()

    assert requests[0][1]["grant_type"] == ["authorization_code"]
    assert requests[0][1]["code"] == ["auth-code"]
    assert requests[0][1]["client_id"] == [_CLIENT_ID]
    assert requests[0][1]["client_secret"] == [_CLIENT_SECRET]
    assert requests[0][1]["code_verifier"]
    assert requests[1][1]["grant_type"] == ["refresh_token"]
    assert requests[1][1]["refresh_token"] == [_REFRESH_TOKEN]
    assert requests[1][1]["client_id"] == [_CLIENT_ID]
    assert requests[1][1]["client_secret"] == [_CLIENT_SECRET]


def test_login_fetches_identity_and_provisions_cloud_assist_project() -> None:
    token_payload = _token_payload()
    token_payload.pop("email")
    token_payload.pop("sub")
    token_payload.pop("project_id")
    oauth = _OAuth(exchange=token_payload)
    requests: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        requests.append(request)
        if request.full_url == "https://www.googleapis.com/oauth2/v1/userinfo?alt=json":
            return _Response(
                {"email": "provisioned@example.test", "sub": "provisioned-account"}
            )
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            body = json.loads(request.data)
            assert "cloudaicompanionProject" not in body
            assert body["metadata"]["ideType"] == "ANTIGRAVITY"
            return _Response(
                {
                    "currentTier": {"id": "standard-tier"},
                    "cloudaicompanionProject": {"id": "provisioned-project"},
                }
            )
        raise AssertionError(f"unexpected setup URL: {request.full_url}")

    def receiver_factory(**kwargs: Any) -> _Receiver:
        return _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        )

    store = _Store()
    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=_CLIENT_ID,
        fetch=fetch,
    )

    credential = backend.login()

    assert credential.project_id == "provisioned-project"
    assert credential.account_id == "provisioned-account"
    assert credential.account_label == "provisioned@example.test"
    assert [request.full_url for request in requests] == [
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
    ]
    assert all(
        request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
        for request in requests
    )
    assert store.credential is credential


def test_login_onboards_and_polls_cloud_assist_operation_for_project() -> None:
    token_payload = _token_payload()
    token_payload.pop("email")
    token_payload.pop("sub")
    token_payload.pop("project_id")
    oauth = _OAuth(exchange=token_payload)
    requests: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        requests.append(request)
        if request.full_url == "https://www.googleapis.com/oauth2/v1/userinfo?alt=json":
            return _Response(
                {"email": "onboarded@example.test", "sub": "onboarded-account"}
            )
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            return _Response(
                {"allowedTiers": [{"id": "legacy-tier", "isDefault": True}]}
            )
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser"
        ):
            body = json.loads(request.data)
            assert body["tierId"] == "legacy-tier"
            return _Response({"name": "operations/123", "done": False})
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal/operations/123"
        ):
            return _Response(
                {
                    "done": True,
                    "response": {
                        "cloudaicompanionProject": {"id": "onboarded-project"}
                    },
                }
            )
        raise AssertionError(f"unexpected setup URL: {request.full_url}")

    def receiver_factory(**kwargs: Any) -> _Receiver:
        return _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        )

    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=_CLIENT_ID,
        fetch=fetch,
        operation_poll_delay=0,
        operation_poll_attempts=2,
    )

    credential = backend.login()

    assert credential.project_id == "onboarded-project"
    assert [request.full_url for request in requests] == [
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser",
        "https://daily-cloudcode-pa.googleapis.com/v1internal/operations/123",
    ]


def test_current_tier_without_project_requires_configuration_without_onboarding() -> (
    None
):
    requests: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        if request.full_url == "https://www.googleapis.com/oauth2/v1/userinfo?alt=json":
            return _Response({"email": "current@example.test", "sub": _ACCOUNT_ID})
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            return _Response({"currentTier": {"id": "standard-tier"}})
        pytest.fail("current-tier account was incorrectly sent to onboardUser")

    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=_OAuth(
            exchange={
                key: value
                for key, value in _token_payload().items()
                if key not in {"email", "sub", "project_id"}
            }
        ),
        receiver_factory=lambda **kwargs: _Receiver(
            OAuthCallback(code="auth-code", state=kwargs["expected_state"])
        ),
        client_id=_CLIENT_ID,
        fetch=fetch,
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert raised.value.metadata["reason"] == "project_configuration_required"
    assert [request.full_url for request in requests] == [
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
    ]


def test_unsupported_individual_client_requires_project_without_onboarding() -> None:
    requests: list[Any] = []
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="individual@example.test",
        account_id=_ACCOUNT_ID,
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            return _Response(
                {
                    "allowedTiers": [
                        {
                            "id": "standard-tier",
                            "isDefault": True,
                            "userDefinedCloudaicompanionProject": True,
                        }
                    ],
                    "ineligibleTiers": [
                        {
                            "tierId": "free-tier",
                            "reasonCode": "UNSUPPORTED_CLIENT",
                        }
                    ],
                }
            )
        pytest.fail("project-required account was incorrectly sent to onboardUser")

    backend = GoogleAntigravityBackend(store=_Store(credential), fetch=fetch)

    with pytest.raises(SubscriptionPolicyError) as raised:
        backend._provision_project(credential)

    assert raised.value.metadata["reason"] == "individual_client_unsupported"
    assert [request.full_url for request in requests] == [
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
    ]


@pytest.mark.parametrize(
    ("tier_id", "includes_project"),
    [("free-tier", False), ("legacy-tier", True)],
)
def test_configured_project_onboarding_matches_official_tier_payload(
    tier_id: str,
    includes_project: bool,
) -> None:
    requests: list[Any] = []
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="configured@example.test",
        account_id=_ACCOUNT_ID,
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            return _Response({"allowedTiers": [{"id": tier_id, "isDefault": True}]})
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser"
        ):
            body = json.loads(request.data)
            assert ("cloudaicompanionProject" in body) is includes_project
            assert ("duetProject" in body["metadata"]) is includes_project
            return _Response(
                {"response": {"cloudaicompanionProject": {"id": "onboarded-project"}}}
            )
        raise AssertionError(f"unexpected setup URL: {request.full_url}")

    backend = GoogleAntigravityBackend(
        store=_Store(credential),
        fetch=fetch,
        project_id=_PROJECT_ID,
    )

    provisioned = backend._provision_project(credential)

    assert provisioned.project_id == "onboarded-project"
    assert [request.full_url for request in requests] == [
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser",
    ]


def test_configured_project_is_used_when_onboarding_response_omits_project() -> None:
    requests: list[Any] = []
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="configured@example.test",
        account_id=_ACCOUNT_ID,
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            return _Response(
                {
                    "allowedTiers": [
                        {
                            "id": "standard-tier",
                            "isDefault": True,
                            "userDefinedCloudaicompanionProject": True,
                        }
                    ]
                }
            )
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser"
        ):
            body = json.loads(request.data)
            assert body["cloudaicompanionProject"] == _PROJECT_ID
            assert body["metadata"]["duetProject"] == _PROJECT_ID
            return _Response(
                {
                    "done": True,
                    "response": {"cloudaicompanionProject": {}},
                }
            )
        raise AssertionError(f"unexpected setup URL: {request.full_url}")

    backend = GoogleAntigravityBackend(
        store=_Store(credential),
        fetch=fetch,
        project_id=_PROJECT_ID,
    )

    provisioned = backend._provision_project(credential)

    assert provisioned.project_id == _PROJECT_ID
    assert [request.full_url for request in requests] == [
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser",
    ]


def test_google_oauth_registration_requires_explicit_environment() -> None:
    assert google_oauth_client_id_from_environment({}) is None
    assert google_oauth_client_secret_from_environment({}) is None

def test_google_login_reports_missing_deployment_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUDIDI_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    backend = GoogleAntigravityBackend(store=_Store())

    with pytest.raises(
        SubscriptionAuthError,
        match="MUDIDI_GOOGLE_OAUTH_CLIENT_ID",
    ):
        backend.begin_login(backend.login_redirect_uri)


def test_authenticated_model_discovery_collapses_effort_variants() -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(
            {
                "models": {
                    "gemini-3.8-flash-high": {
                        "displayName": "Gemini 3.8 Flash",
                        "priority": 7,
                    },
                    "gemini-3.8-flash-medium": {
                        "displayName": "Gemini 3.8 Flash",
                        "priority": 3,
                    },
                    "gemini-3.8-flash-low": {
                        "displayName": "Gemini 3.8 Flash",
                        "priority": 5,
                    },
                    "gemini-3.8-flash-tiered": {},
                    "claude-sonnet-4-6": {"displayName": "Claude Sonnet"},
                }
            }
        )

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)

    models = backend.list_models()
    assert [model.model_id for model in models] == ["gemini-3.8-flash"]
    assert models[0].display_name == "Gemini 3.8 Flash"
    assert models[0].reasoning.efforts == ("minimal", "low", "medium", "high")
    assert dict(models[0].reasoning.wire_model_map) == {
        "minimal": "gemini-3.8-flash-low",
        "low": "gemini-3.8-flash-low",
        "medium": "gemini-3.8-flash-medium",
        "high": "gemini-3.8-flash-high",
    }
    assert models[0].provider_order == 3
    request = captured[0]
    assert request.full_url == (
        "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
    )
    assert json.loads(request.data) == {"project": _PROJECT_ID}
    assert request.headers["User-agent"].startswith("antigravity/hub/")


def test_authenticated_model_discovery_normalizes_internal_aliases() -> None:
    def fetch(request, **_kwargs: Any) -> _Response:
        return _Response(
            {
                "models": {
                    "gemini-3.5-flash-extra-low": {
                        "displayName": "Gemini 3.5 Flash (Low)"
                    },
                    "gemini-3.5-flash-low": {"displayName": "Gemini 3.5 Flash (Low)"},
                    "gemini-3-flash-agent": {"displayName": "Gemini 3.5 Flash (High)"},
                    "gemini-pro-agent": {"displayName": "Gemini 3.1 Pro (High)"},
                    "gemini-2.5-flash": {
                        "displayName": "Gemini 3.5 Flash Lite",
                        "tagDescription": "gemini-3.5-flash-lite",
                    },
                    "gemini-2.5-flash-lite": {
                        "displayName": "Gemini 3.5 Flash Lite",
                        "tagDescription": "gemini-3.5-flash-lite",
                    },
                    "gemini-2.5-flash-thinking": {
                        "displayName": "Gemini 3.5 Flash Lite",
                        "tagDescription": "gemini-3.5-flash-lite",
                    },
                    "gemini-3.5-flash-lite": {
                        "displayName": "Gemini 3.5 Flash Lite",
                        "tagDescription": "gemini-3.5-flash-lite",
                    },
                }
            }
        )

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)

    models = {model.model_id: model for model in backend.list_models()}

    configurable = models["gemini-3.5-flash"]
    assert configurable.display_name == "Gemini 3.5 Flash"
    assert configurable.reasoning.efforts == ("minimal", "low")
    assert models["gemini-3-flash-agent"].reasoning.efforts == ("high",)
    assert models["gemini-pro-agent"].reasoning.efforts == ("high",)
    assert models["gemini-3.5-flash-lite"].display_name == "Gemini 3.5 Flash Lite"
    assert {
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash-thinking",
    }.isdisjoint(models)
    assert all(model.provider_order is None for model in models.values())


def test_fixed_effort_gemini_alias_does_not_synthesize_a_model_suffix() -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_response_json("ok"))

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)
    backend.complete(
        CompletionRequest(
            model="gemini-3-flash-agent",
            messages=[{"role": "user", "content": "Think."}],
            reasoning="low",
        )
    )

    body = json.loads(captured[0].data)
    assert body["model"] == "gemini-3-flash-agent"
    assert body["request"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "HIGH"
    }


def test_google_cloud_project_environment_is_optional_and_validated() -> None:
    assert google_cloud_project_from_environment({}) is None
    assert google_cloud_project_from_environment(
        {"GOOGLE_CLOUD_PROJECT": "project-123"}
    ) == ("project-123")
    assert (
        google_cloud_project_from_environment(
            {"GOOGLE_CLOUD_PROJECT": "invalid/project"}
        )
        is None
    )


def test_google_oauth_registration_environment_overrides_defaults() -> None:
    environment = {
        "MUDIDI_GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID,
        "MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
    }

    assert google_oauth_client_id_from_environment(environment) == _CLIENT_ID
    assert google_oauth_client_secret_from_environment(environment) == _CLIENT_SECRET


def test_login_rejects_callback_state_mismatch_before_token_exchange() -> None:
    oauth = _OAuth()

    def receiver_factory(**_kwargs: Any) -> _Receiver:
        return _Receiver(OAuthCallback(code="auth-code", state="wrong-state"))

    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=oauth,
        receiver_factory=receiver_factory,
        client_id=_CLIENT_ID,
        project_id=_PROJECT_ID,
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert raised.value.metadata["reason"] == "state_mismatch"
    assert oauth.exchange_calls == []


def test_login_rejects_injected_receiver_with_wrong_callback_path() -> None:
    oauth = _OAuth()
    receiver = _Receiver(OAuthCallback(code="auth-code", state="unused"))
    receiver.redirect_uri = "http://127.0.0.1:43123/wrong-callback"

    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=oauth,
        receiver=receiver,
        client_id=_CLIENT_ID,
        project_id=_PROJECT_ID,
    )

    with pytest.raises(SubscriptionAuthError) as raised:
        backend.login()

    assert raised.value.metadata["reason"] == "invalid_redirect_uri"
    assert receiver.closed is True
    assert oauth.exchange_calls == []


def test_refresh_rotates_access_token_and_preserves_missing_refresh_response() -> None:
    store = _Store(_credential())
    oauth = _OAuth(
        refresh=_token_payload(access_token=_ROTATED_TOKEN, refresh_token=None)
    )
    backend = GoogleAntigravityBackend(
        store=store,
        oauth_client=oauth,
        client_id=_CLIENT_ID,
        project_id=_PROJECT_ID,
    )

    replacement = backend.refresh()

    assert replacement.access_token.get_secret_value() == _ROTATED_TOKEN
    assert replacement.refresh_token is not None
    assert replacement.refresh_token.get_secret_value() == _REFRESH_TOKEN
    assert oauth.refresh_calls[0]["refresh_token"] == _REFRESH_TOKEN
    assert store.credential is replacement
    assert _ROTATED_TOKEN not in repr(backend.status())
    assert _REFRESH_TOKEN not in backend.status().model_dump_json()


def test_complete_lazily_sets_up_project_when_userinfo_is_unavailable() -> None:
    raw_credential = _credential().model_copy(
        update={
            "account_label": "Google account",
            "account_id": None,
            "project_id": None,
        }
    )
    store = _Store(raw_credential)
    requests: list[Any] = []
    sse = (
        'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ready"}]},'
        '"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,'
        '"candidatesTokenCount":1,"totalTokenCount":2}}}\n\n'
        "data: [DONE]\n\n"
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        if request.full_url == "https://www.googleapis.com/oauth2/v1/userinfo?alt=json":
            raise URLError("userinfo unavailable")
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
        ):
            body = json.loads(request.data)
            assert body["metadata"]["ideType"] == "ANTIGRAVITY"
            return _Response({"cloudaicompanionProject": _PROJECT_ID})
        if (
            request.full_url
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        ):
            return _Response(sse)
        raise AssertionError(f"unexpected request: {request.full_url}")

    backend = GoogleAntigravityBackend(store=store, fetch=fetch)
    result = backend.complete(
        CompletionRequest(
            model="gemini/gemini-2.5-pro",
            messages=[{"role": "user", "content": "Ready?"}],
        )
    )

    assert result.text == "ready"
    assert store.credential is not None
    assert store.credential.project_id == _PROJECT_ID
    assert store.credential.account_id is None
    assert [request.full_url for request in requests] == [
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
    ]


def test_complete_translates_cloud_code_envelope_and_normalizes_sse_response() -> None:
    captured: list[Any] = []
    sse = "\n\n".join(
        [
            'data: {"response":{"candidates":[{"content":{"parts":[{"text":"Hello "}]}}]}}',
            'data: {"response":{"candidates":[{"content":{"parts":[{"text":"world"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2,"totalTokenCount":6,"cachedContentTokenCount":1,"thoughtsTokenCount":3},"modelVersion":"gemini-2.5-pro"}}',
            "data: [DONE]",
        ]
    )

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(sse)

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)
    result = backend.complete(
        CompletionRequest(
            model="gemini/gemini-2.5-pro",
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
            max_tokens=123,
        )
    )

    request = captured[0]
    assert (
        request.full_url
        == "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    )
    assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert request.headers["Content-type"] == "application/json"
    assert request.headers["User-agent"].startswith("antigravity/hub/")
    assert "api-key" not in {key.lower() for key in request.headers}
    body = json.loads(request.data)
    assert body["project"] == _PROJECT_ID
    assert body["model"] == "gemini-2.5-pro"
    assert body["requestId"]
    assert body["userAgent"] == "antigravity"
    assert body["requestType"] == "agent"
    assert body["request"]["systemInstruction"] == {
        "role": "user",
        "parts": [{"text": "Follow the policy."}],
    }
    assert body["request"]["contents"] == [
        {
            "role": "user",
            "parts": [
                {"text": "Describe this."},
                {"inlineData": {"mimeType": "image/png", "data": "ZmFrZS1pbWFnZQ=="}},
            ],
        },
        {"role": "model", "parts": [{"text": "Prior answer"}]},
    ]
    generation = body["request"]["generationConfig"]
    assert "temperature" not in generation
    assert generation["maxOutputTokens"] == 123
    assert generation["thinkingConfig"] == {"thinkingBudget": 8192}
    assert result.text == "Hello world"
    assert result.model == "gemini-2.5-pro"
    assert result.finish_reason == "stop"
    assert result.usage == {
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
        "cached_tokens": 1,
        "reasoning_tokens": 3,
    }
    assert result.provider is SubscriptionProvider.GOOGLE
    assert result.billing_mode == "subscription"


@pytest.mark.parametrize(
    "persisted_model",
    [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-tiered",
    ],
)
def test_effort_overrides_persisted_gemini_flash_variant(
    persisted_model: str,
) -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_response_json("ok"))

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)
    backend.complete(
        CompletionRequest(
            model=f"gemini/{persisted_model}",
            messages=[{"role": "user", "content": "Think briefly."}],
            reasoning="low",
        )
    )

    body = json.loads(captured[0].data)
    assert body["model"] == "gemini-3.8-flash-low"
    assert body["request"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "LOW"
    }


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [
        ("gemini-1.5-pro", "high"),
        ("custom-gemini", "medium"),
    ],
)
def test_reasoning_rejects_unpreservable_model_level_combinations(
    model: str,
    reasoning: str,
) -> None:
    calls: list[Any] = []
    backend = GoogleAntigravityBackend(
        store=_Store(_credential()),
        fetch=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or _Response(_response_json("no"))
        ),
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(
            CompletionRequest(
                model=model,
                messages=[{"role": "user", "content": "Think."}],
                reasoning=reasoning,
            )
        )

    assert calls == []


def test_gemini_three_flash_allows_model_specific_minimal_reasoning() -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(_response_json("ok"))

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)
    backend.complete(
        CompletionRequest(
            model="gemini-3.1-flash-preview",
            messages=[{"role": "user", "content": "Think briefly."}],
            reasoning="minimal",
        )
    )

    body = json.loads(captured[0].data)
    assert body["request"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "MINIMAL"
    }


def test_complete_structured_translates_schema_and_parses_json_response() -> None:
    captured: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(
            {
                "response": {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": '{"answer":"yes","count":2}'}]
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 4},
                }
            }
        )

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }
    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)

    result = backend.complete_structured(
        CompletionRequest(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": "Return JSON"}],
            schema=schema,
        )
    )

    body = json.loads(captured[0].data)
    generation = body["request"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseJsonSchema"] == schema
    assert result.structured_json == {"answer": "yes", "count": 2}
    assert result.usage["input_tokens"] == 2
    assert result.usage["output_tokens"] == 4
    assert result.usage["total_tokens"] == 6


def test_complete_structured_parses_fenced_json_across_sse_events() -> None:
    stream = "\n\n".join(
        (
            'data: {"response":{"candidates":[{"content":{"parts":'
            '[{"text":"```json\\n{"}]}}]}}',
            'data: {"response":{"candidates":[{"content":{"parts":'
            '[{"text":"\\n  \\"answer\\": 42\\n}\\n```"}]}}]}}',
            'data: {"response":{"candidates":[{"content":{"parts":'
            '[{"text":""}]},"finishReason":"STOP"}]}}',
        )
    )
    backend = GoogleAntigravityBackend(
        store=_Store(_credential()),
        fetch=lambda *_args, **_kwargs: _Response(stream),
    )

    result = backend.complete_structured(
        CompletionRequest(
            model="gemini-3.8-flash-high",
            messages=[{"role": "user", "content": "Return JSON"}],
            reasoning="low",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
    )

    assert result.structured_json == {"answer": 42}


@pytest.mark.parametrize(
    "status,error_type",
    [
        (403, SubscriptionPolicyError),
        (429, SubscriptionTransportError),
        (500, SubscriptionTransportError),
    ],
)
def test_provider_failures_are_typed_secret_safe_and_do_not_fallback(
    status: int,
    error_type: type[Exception],
) -> None:
    calls: list[Any] = []

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response(
            {"error": {"message": _ACCESS_TOKEN, "status": "PERMISSION_DENIED"}},
            status=status,
        )

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)

    with pytest.raises(error_type) as raised:
        backend.complete(
            CompletionRequest(
                model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
            )
        )

    assert _ACCESS_TOKEN not in repr(raised.value)
    assert _REFRESH_TOKEN not in repr(raised.value)
    assert "generativelanguage.googleapis.com" not in repr(raised.value)
    assert calls


def test_401_refreshes_once_then_retries_with_rotated_bearer() -> None:
    calls: list[Any] = []
    responses = [_Response({}, status=401), _Response(_response_json("ok"))]
    oauth = _OAuth(refresh=_token_payload(access_token=_ROTATED_TOKEN))

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return responses.pop(0)

    store = _Store(_credential())
    backend = GoogleAntigravityBackend(
        store=store, oauth_client=oauth, fetch=fetch, client_id=_CLIENT_ID
    )

    result = backend.complete(
        CompletionRequest(
            model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
        )
    )

    assert result.text == "ok"
    assert len(calls) == 2
    assert len(oauth.refresh_calls) == 1
    assert calls[0].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert calls[1].headers["Authorization"] == f"Bearer {_ROTATED_TOKEN}"


def test_second_401_becomes_expired_session_without_second_refresh() -> None:
    calls: list[Any] = []
    oauth = _OAuth(refresh=_token_payload(access_token=_ROTATED_TOKEN))

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return _Response({}, status=401)

    backend = GoogleAntigravityBackend(
        store=_Store(_credential()),
        oauth_client=oauth,
        fetch=fetch,
        client_id=_CLIENT_ID,
    )

    with pytest.raises(SubscriptionTokenExpired) as raised:
        backend.complete(
            CompletionRequest(
                model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
            )
        )

    assert raised.value.status == 401
    assert len(calls) == 2
    assert len(oauth.refresh_calls) == 1


def test_transient_service_status_retries_once_then_succeeds() -> None:
    calls: list[Any] = []
    responses = [_Response({}, status=503), _Response(_response_json("recovered"))]

    def fetch(request, **_kwargs: Any) -> _Response:
        calls.append(request)
        return responses.pop(0)

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)

    result = backend.complete(
        CompletionRequest(
            model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
        )
    )

    assert result.text == "recovered"
    assert len(calls) == 2


def test_missing_project_attempts_lazy_setup_then_rejects_incomplete_response() -> None:
    calls: list[Any] = []
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="Google account",
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        calls.append(request)
        if request.full_url == "https://www.googleapis.com/oauth2/v1/userinfo?alt=json":
            return _Response({"email": "account@example.test", "sub": _ACCOUNT_ID})
        return _Response({})

    backend = GoogleAntigravityBackend(
        store=_Store(credential),
        fetch=fetch,
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model="gemini-2.5-pro",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )

    assert raised.value.metadata["reason"] == "missing_provisioning_tier"
    assert [request.full_url for request in calls] == [
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
    ]


def test_missing_account_metadata_does_not_block_authenticated_request() -> None:
    calls: list[Any] = []
    credential = SubscriptionCredential(
        provider=SubscriptionProvider.GOOGLE,
        account_label="Google account",
        project_id=_PROJECT_ID,
        access_token=_ACCESS_TOKEN,
        refresh_token=_REFRESH_TOKEN,
    )
    backend = GoogleAntigravityBackend(
        store=_Store(credential),
        fetch=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or _Response(_response_json("ready"))
        ),
    )

    result = backend.complete(
        CompletionRequest(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": "Hi"}],
        )
    )

    assert result.text == "ready"
    assert len(calls) == 1


def test_thought_only_and_malformed_sse_responses_fail_closed() -> None:
    responses = [
        _Response(
            'data: {"response":{"candidates":[{"content":{"parts":[{"text":"private","thought":true}]}}]}}\n\ndata: [DONE]\n\n'
        ),
        _Response("data: not-json\n\n"),
    ]

    def fetch(_request, **_kwargs: Any) -> _Response:
        return responses.pop(0)

    backend = GoogleAntigravityBackend(store=_Store(_credential()), fetch=fetch)
    request = CompletionRequest(
        model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
    )

    with pytest.raises(SubscriptionTransportError):
        backend.complete(request)
    with pytest.raises(SubscriptionTransportError):
        backend.complete(request)


def test_json_response_without_cloud_assist_response_envelope_is_rejected() -> None:
    backend = GoogleAntigravityBackend(
        store=_Store(_credential()),
        fetch=lambda *args, **kwargs: _Response({"text": "ok"}),
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model="gemini-2.5-pro", messages=[{"role": "user", "content": "Hi"}]
            )
        )

    assert raised.value.metadata["reason"] == "malformed_response"


def test_unsupported_tools_audio_and_schema_keywords_fail_before_transport() -> None:
    calls: list[Any] = []
    backend = GoogleAntigravityBackend(
        store=_Store(_credential()),
        fetch=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or _Response(_response_json("no"))
        ),
    )

    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete(
            CompletionRequest(
                model="gemini-2.5-pro",
                messages=[
                    {"role": "user", "content": [{"type": "audio", "data": "abc"}]}
                ],
            )
        )
    with pytest.raises(SubscriptionUnsupportedRequest):
        backend.complete_structured(
            CompletionRequest(
                model="gemini-2.5-pro",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "properties": {}, "pattern": "unsupported"},
            )
        )
    assert calls == []


def _response_json(text: str) -> str:
    return json.dumps(
        {
            "response": {
                "candidates": [
                    {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
                ]
            }
        }
    )


def test_google_authorization_rejects_dashboard_prefixed_callback_path() -> None:
    backend = GoogleAntigravityBackend(
        store=_Store(),
        oauth_client=_OAuth(),
        client_id=_CLIENT_ID,
    )

    with pytest.raises(SubscriptionAuthError):
        backend.build_authorization_url(
            PkceChallenge.generate(),
            "http://127.0.0.1:43123/subscriptions/google/oauth2callback",
        )
