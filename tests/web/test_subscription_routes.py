"""Local-only subscription authentication lifecycle route tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import logging
import socket
import time
import subprocess
from typing import Any
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from mudidi.config.yaml_config import AuthConfig
from mudidi.llm.client import resolve_subscription_runtime
from mudidi.llm.reasoning import resolve_reasoning_profile
from mudidi.llm.subscriptions import (
    BackendCapabilities,
    SubscriptionAuthError,
    SubscriptionError,
    SubscriptionPolicyError,
    SubscriptionCredential,
    SubscriptionLoginTransaction,
    SubscriptionProvider,
    SubscriptionModel,
    SubscriptionStatus,
    SubscriptionTokenExpired,
)
from mudidi.llm.subscriptions.oauth import LoopbackOAuthReceiver
from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend
from mudidi.llm.subscriptions.google_antigravity import GoogleAntigravityBackend
from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend
from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.storage import SubscriptionStore
from mudidi.web.app import create_app
from mudidi.web.models import Provider


class _FakeBackend:
    def __init__(
        self,
        provider: SubscriptionProvider,
        *,
        refresh_fails: bool = False,
        login_redirect_uri: str | None = None,
    ) -> None:
        self.provider = provider
        self.login_redirect_uri = login_redirect_uri
        self._refresh_fails = refresh_fails
        self._credential: SubscriptionCredential | None = None
        self._transaction: SubscriptionLoginTransaction | None = None
        self.login_calls = 0
        self.refresh_calls = 0
        self.callback_calls: list[tuple[str, str]] = []
        self.logout_calls = 0

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(image_input=True, structured_output=True)

    def begin_login(self, redirect_uri: str) -> SubscriptionLoginTransaction:
        challenge = PkceChallenge.generate()
        self._transaction = SubscriptionLoginTransaction(
            authorization_url=(
                "https://auth.example.test/authorize?"
                f"{urlencode({'redirect_uri': redirect_uri, 'state': challenge.state})}"
            ),
            redirect_uri=redirect_uri,
            state=challenge.state,
            verifier=SecretStr(challenge.verifier),
        )
        return self._transaction

    def status(self) -> SubscriptionStatus:
        if self._credential is None:
            return SubscriptionStatus(
                provider=self.provider,
                metadata={"category": "missing", "subscription": True},
            )
        expires_at = self._credential.expires_at
        authenticated = expires_at is None or expires_at > datetime.now(UTC)
        category = (
            "authenticated"
            if authenticated
            else "expired_session"
            if expires_at is not None
            else "missing"
        )
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=authenticated,
            account_label=self._credential.account_label,
            expires_at=expires_at,
            metadata={
                "account_id": self._credential.account_id,
                "access_token": self._credential.access_token.get_secret_value(),
                "category": category,
                "credential_present": True,
                "subscription": True,
            },
        )

    def expire(self) -> None:
        if self._credential is None:
            raise AssertionError("expire requires a seeded credential")
        self._credential = self._credential.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )

    def login(self) -> SubscriptionCredential:
        self.login_calls += 1
        self._credential = SubscriptionCredential(
            provider=self.provider,
            account_label="account@example.test",
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            account_id="account-id",
        )
        return self._credential

    def complete_login(
        self,
        code: str,
        state: str,
        transaction: SubscriptionLoginTransaction,
    ) -> SubscriptionCredential:
        assert self._transaction is transaction
        self.callback_calls.append((code, state))
        return self.login()

    def refresh(self) -> SubscriptionCredential:
        self.refresh_calls += 1
        if self._refresh_fails:
            raise SubscriptionTokenExpired(
                "refresh failed",
                provider=self.provider,
                metadata={"reason": "refresh_failed"},
            )
        return self.login()

    def logout(self) -> None:
        self.logout_calls += 1
        self._credential = None

    def list_models(self) -> tuple[SubscriptionModel, ...]:
        model_id = {
            SubscriptionProvider.OPENAI: "gpt-5.6-terra",
            SubscriptionProvider.GOOGLE: "gemini-3.8-flash",
            SubscriptionProvider.CLAUDE: "claude-sonnet-4-6",
        }[self.provider]
        return (
            SubscriptionModel(
                model_id=model_id,
                display_name=model_id,
                reasoning=resolve_reasoning_profile(self.provider.value, model_id),
                provider_order=0,
            ),
        )

    def complete(self, request: Any) -> Any:
        del request
        raise NotImplementedError

    def complete_structured(self, request: Any) -> Any:
        del request
        raise NotImplementedError


class _LoginErrorBackend(_FakeBackend):
    def __init__(
        self, provider: SubscriptionProvider, error: SubscriptionError
    ) -> None:
        super().__init__(provider)
        self.error = error

    def begin_login(self, redirect_uri: str) -> SubscriptionLoginTransaction:
        del redirect_uri
        raise self.error


def _app(
    tmp_path: Path,
    *,
    backends: dict[SubscriptionProvider, _FakeBackend] | None = None,
    container_mode: bool = False,
) -> Any:
    return create_app(
        data_dir=tmp_path / "app-data",
        subscription_backends=backends,
        container_mode=container_mode,
    )


def _pending_transaction(
    app: Any,
    provider: SubscriptionProvider,
) -> tuple[str, SubscriptionLoginTransaction, datetime]:
    for handle, (
        pending_provider,
        transaction,
        expires_at,
    ) in app.state.subscription_transactions.items():
        if pending_provider is provider:
            return handle, transaction, expires_at
    raise AssertionError(f"no pending transaction for {provider.value}")


def _pdf_bytes() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def _preview_subscription(
    client: TestClient,
    tmp_path: Path,
    **overrides: str,
) -> Any:
    data: dict[str, str] = {
        "output_directory": str(tmp_path / "output"),
        "pipeline": "transcription",
        "stage1_provider": "openai",
        "stage2_provider": "openai",
        "model": "openai/gpt-5.6-terra",
        "reasoning": "low",
        "agentic": "false",
        "dictionary_pages": "1",
        "auth_mode": "subscription",
    }
    data.update(overrides)
    return client.post(
        "/runs/preview",
        data=data,
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
    )


def test_subscription_preview_refreshes_expired_credential_before_requiring_login(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    backend.login()
    backend.expire()
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    response = _preview_subscription(client, tmp_path)

    assert response.status_code == 200
    assert backend.refresh_calls == 1
    assert "Log in to the openai subscription" not in response.text
    assert "refresh-secret" not in response.text


def test_subscription_preview_reports_refresh_failure_as_safe_form_error(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI, refresh_fails=True)
    backend.login()
    backend.expire()
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    response = _preview_subscription(client, tmp_path)

    assert response.status_code == 422
    assert backend.refresh_calls == 1
    assert "Subscription session expired; log in again" in response.text
    assert "refresh-secret" not in response.text


def test_subscription_preview_preserves_missing_login_form_guidance(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    response = _preview_subscription(client, tmp_path)

    assert response.status_code == 422
    assert "Log in to the openai subscription before starting a run." in response.text
    assert "Subscription authentication failed" not in response.text
    assert "access-secret" not in response.text
    assert "refresh-secret" not in response.text


def test_expired_subscription_can_be_logged_out_and_status_marks_removable(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    backend.login()
    backend.expire()
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    status = client.get("/subscriptions/openai/status")
    logged_out = client.post("/subscriptions/openai/logout")

    assert status.status_code == 200
    assert status.json()["authenticated"] is False
    assert status.json()["credential_present"] is True
    assert status.json()["removable"] is True
    assert logged_out.status_code == 200
    assert logged_out.json()["authenticated"] is False
    assert logged_out.json()["credential_present"] is False
    assert logged_out.json()["removable"] is False
    assert backend.logout_calls == 1


def test_subscription_card_distinguishes_expiry_and_safe_removal_state() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
    this.disabled = false;
  }
}

const state = new Element();
const category = new Element();
const account = new Element();
const expiry = new Element();
const login = new Element();
const logout = new Element();
const card = new Element();
card.querySelector = (selector) => ({
  "[data-subscription-state]": state,
  "[data-subscription-category]": category,
  "[data-subscription-account]": account,
  "[data-subscription-expiry]": expiry,
  "[data-subscription-login]": login,
  "[data-subscription-logout]": logout,
}[selector] || null);

const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("const applySubscriptionStatus");
const end = source.indexOf("\n};", start) + 3;
const functionSource = source
  .slice(start, end)
  .replace("const applySubscriptionStatus", "globalThis.applySubscriptionStatus");
const context = vm.createContext({console});
vm.runInContext(functionSource, context);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(
  context.applySubscriptionStatus(card, {
    authenticated: false,
    credential_present: true,
    removable: true,
    available: true,
    category: "expired_session",
    account_label: "account@example.test",
    expires_at: "2026-09-10T00:00:00+00:00",
  }),
  "expired status should be accepted",
);
assert(card.dataset.subscriptionAuthenticated === "false", "auth state leaked expiry semantics");
assert(card.dataset.subscriptionCredentialPresent === "true", "presence state was not recorded");
assert(card.dataset.subscriptionRemovable === "true", "removable state was not recorded");
assert(state.textContent === "Session expired", "expired status should be visible");
assert(!logout.disabled, "expired credentials must remain removable");

context.applySubscriptionStatus(card, {
  authenticated: false,
  credential_present: false,
  removable: true,
  available: true,
  category: "policy",
});
assert(state.textContent === "Log in required", "unauthenticated state should be visible");
assert(!logout.disabled, "removable credentials must keep logout enabled");

context.applySubscriptionStatus(card, {
  authenticated: false,
  credential_present: false,
  removable: false,
  available: true,
  category: "missing",
});
assert(logout.disabled, "missing credentials may disable logout");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_subscription_login_displays_safe_server_message_and_closes_popup() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const helperStart = source.indexOf("const subscriptionLoginErrorMessage");
if (helperStart < 0) {
  throw new Error("subscriptionLoginErrorMessage is not present");
}
const helperEnd = source.indexOf("\n};", helperStart) + 3;
const helperSource = source
  .slice(helperStart, helperEnd)
  .replace(
    "const subscriptionLoginErrorMessage",
    "globalThis.subscriptionLoginErrorMessage",
  );
const context = vm.createContext({console, URL});
vm.runInContext(helperSource, context);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const providerMessage = "Subscription authentication failed";
assert(
  context.subscriptionLoginErrorMessage({message: providerMessage}) === providerMessage,
  "server login guidance should be displayed",
);
assert(
  context.subscriptionLoginErrorMessage({message: "   "}) === "Subscription login failed.",
  "blank server messages should use the safe fallback",
);

const makePopup = () => ({
  closed: false,
  opener: {},
  location: {href: ""},
  close() {
    this.closed = true;
  },
});
const makeCard = (actionStatus) => {
  const login = {
    disabled: false,
    addEventListener(_event, handler) {
      this.handler = handler;
    },
  };
  const card = {
    dataset: {
      subscriptionProvider: "google",
      subscriptionAvailable: "true",
    },
    querySelector(selector) {
      return {
        "[data-subscription-action-status]": actionStatus,
        "[data-subscription-login]": login,
      }[selector] || null;
    },
  };
  return {card, login};
};
const handlerStart = source.indexOf(
  "subscriptionCards.forEach((card) => {",
  source.indexOf("const subscriptionLoginErrorMessage"),
);
const handlerEnd = source.indexOf("\n  logout?.addEventListener", handlerStart);
const handlerSource = source.slice(handlerStart, handlerEnd) + "\n});";

const failedActionStatus = {textContent: ""};
const failedPopup = makePopup();
const failed = makeCard(failedActionStatus);
const failedWindow = {
  location: {origin: "http://localhost"},
  open() {
    return failedPopup;
  },
  fetch: async () => ({
    ok: false,
    json: async () => ({message: providerMessage}),
  }),
};
context.subscriptionCards = [failed.card];
context.window = failedWindow;
vm.runInContext(handlerSource, context);


const successfulActionStatus = {textContent: ""};
const successfulPopup = makePopup();
const successful = makeCard(successfulActionStatus);
const successfulWindow = {
  location: {origin: "http://localhost"},
  open() {
    return successfulPopup;
  },
  fetch: async () => ({
    ok: true,
    json: async () => ({launch_url: "/subscriptions/google/login/launch"}),
  }),
};
context.waitForSubscriptionLogin = async () => {};
context.subscriptionCards = [successful.card];
context.window = successfulWindow;
vm.runInContext(handlerSource, context);


const externalActionStatus = {textContent: ""};
const externalPopup = makePopup();
const external = makeCard(externalActionStatus);
const externalWindow = {
  location: {origin: "http://localhost"},
  open() {
    return externalPopup;
  },
  fetch: async () => ({
    ok: true,
    json: async () => ({launch_url: "https://evil.example/login"}),
  }),
};
context.subscriptionCards = [external.card];
context.window = externalWindow;
vm.runInContext(handlerSource, context);


(async () => {
  context.window = failedWindow;
  await failed.login.handler();
  assert(
    failedActionStatus.textContent === providerMessage,
    "failed login should display the server guidance",
  );
  assert(failedPopup.closed, "failed login initiation should close the popup");

  context.window = successfulWindow;
  await successful.login.handler();
  assert(
    successfulPopup.location.href === "http://localhost/subscriptions/google/login/launch",
    "successful login should navigate to the local launch route",
  );
  assert(!successfulPopup.closed, "successful login should keep the popup open");

  context.window = externalWindow;
  await external.login.handler();
  assert(
    externalPopup.location.href === "",
    "cross-origin launch URLs must not navigate the popup",
  );
  assert(externalPopup.closed, "rejected launch URLs should close the popup");
  assert(
    externalActionStatus.textContent === "Login URL must stay on this server",
    "cross-origin launch rejection should not expose the URL",
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_default_google_backend_uses_encrypted_web_store(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")

    backend = app.state.subscription_backends[SubscriptionProvider.GOOGLE]
    assert isinstance(backend, GoogleAntigravityBackend)
    assert backend._store is app.state.subscription_store


def test_subscription_status_contains_only_safe_identity_expiry_and_category(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    backend.login()
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    response = TestClient(app).get("/subscriptions/openai/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["account_label"] == "account@example.test"
    assert payload["category"] == "authenticated"
    assert payload["expires_at"]
    assert "access-secret" not in response.text
    assert "refresh-secret" not in response.text
    assert "account_id" not in payload
    assert "metadata" not in payload


def test_subscription_login_callback_and_logout_never_return_credentials(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    logged_in = client.post("/subscriptions/openai/login")
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state
    callback = client.get(
        f"/subscriptions/openai/callback?code=authorization-secret&state={state}"
    )
    logged_out = client.post("/subscriptions/openai/logout")

    assert logged_in.status_code == 200
    login_payload = logged_in.json()
    assert login_payload["status"] == "pending"
    assert login_payload["launch_url"] == "/subscriptions/openai/login/launch"
    assert "authorization_url" not in login_payload
    assert "transaction" not in login_payload
    assert transaction.authorization_url not in logged_in.text
    assert handle not in logged_in.text
    assert transaction.state not in logged_in.text
    assert transaction.verifier.get_secret_value() not in logged_in.text
    assert "authorization-secret" not in logged_in.text
    assert "access-secret" not in logged_in.text
    assert "refresh-secret" not in logged_in.text
    assert callback.status_code == 200
    assert logged_out.status_code == 200
    assert logged_out.json()["authenticated"] is False
    assert logged_out.json()["available"] is True
    assert logged_out.json()["category"] == "missing"
    assert backend.login_calls == 1
    assert backend.callback_calls == [("authorization-secret", state)]
    assert backend.logout_calls == 1
    for response in (callback, logged_out):
        assert "access-secret" not in response.text
        assert "refresh-secret" not in response.text
        assert "authorization-secret" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/subscriptions/openai/login/launch",
        "/auth/subscriptions/openai/login/launch",
        "/auth/subscription/openai/login/launch",
    ],
)
def test_subscription_login_launch_redirects_without_json_secrets(
    tmp_path: Path,
    path: str,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    initiation = client.post("/subscriptions/openai/login")
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    payload = initiation.json()
    assert payload["launch_url"] == "/subscriptions/openai/login/launch"
    assert "authorization_url" not in payload
    assert "transaction" not in payload

    launch = client.get(path, follow_redirects=False)

    assert launch.status_code == 307
    assert launch.headers["location"] == transaction.authorization_url
    assert launch.headers["cache-control"] == "no-store"
    assert handle in app.state.subscription_transactions


def test_subscription_login_launch_requires_unexpired_pending_transaction(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    missing = client.get("/subscriptions/openai/login/launch")
    initiation = client.post("/subscriptions/openai/login")
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    provider, stored_transaction, _ = app.state.subscription_transactions[handle]
    app.state.subscription_transactions[handle] = (
        provider,
        stored_transaction,
        datetime.now(UTC) - timedelta(seconds=1),
    )
    expired = client.get("/subscriptions/openai/login/launch")

    assert initiation.status_code == 200
    for response in (missing, expired):
        assert response.status_code == 409
        payload = response.json()
        assert payload["status"] == "error"
        assert payload["category"] == "authentication"
        assert payload["message"] == "Subscription authentication failed"
        assert "authorization_url" not in payload
        assert "transaction" not in payload
        assert transaction.authorization_url not in response.text
        assert handle not in response.text
        assert transaction.state not in response.text
        assert transaction.verifier.get_secret_value() not in response.text
        assert "authorization-code" not in response.text
        assert "access-secret" not in response.text
        assert "refresh-secret" not in response.text




def test_claude_login_reports_provider_policy_errors_without_secrets(
    tmp_path: Path,
) -> None:
    backend = _LoginErrorBackend(
        SubscriptionProvider.CLAUDE,
        SubscriptionPolicyError(
            "Claude subscription access was rejected by the provider",
            provider=SubscriptionProvider.CLAUDE,
            metadata={"reason": "provider_policy_rejected"},
        ),
    )
    response = TestClient(
        _app(tmp_path, backends={SubscriptionProvider.CLAUDE: backend})
    ).post("/subscriptions/claude/login")

    assert response.status_code == 403
    assert response.json()["category"] == "policy"
    assert (
        response.json()["message"]
        == "Subscription request was blocked by provider policy"
    )
    assert "warning" not in response.json()
    for secret in ("authorization-secret", "access-secret", "refresh-secret"):
        assert secret not in response.text


def test_subscription_callback_rejects_missing_or_malformed_callback_safely(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    missing = client.get("/subscriptions/openai/callback")
    repeated = client.get(
        "/subscriptions/openai/callback?code=one&code=two&state=state-value"
    )

    assert missing.status_code == 400
    assert repeated.status_code == 400
    assert "authorization-secret" not in missing.text
    assert "authorization-secret" not in repeated.text


def test_provider_availability_is_non_secret_and_includes_supported_providers(
    tmp_path: Path,
) -> None:
    backends = {provider: _FakeBackend(provider) for provider in SubscriptionProvider}
    client = TestClient(_app(tmp_path, backends=backends))

    response = client.get("/subscriptions/providers")

    assert response.status_code == 200
    payload = response.json()
    assert [item["provider"] for item in payload["providers"]] == [
        "openai",
        "google",
        "claude",
    ]
    assert all(item["available"] for item in payload["providers"])
    assert "access-secret" not in response.text
    assert "refresh-secret" not in response.text


def test_claude_status_and_dashboard_are_warning_free(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.CLAUDE)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.CLAUDE: backend}))

    response = client.get("/subscriptions/claude/status")
    home = client.get("/")
    warning_endpoint = client.get("/subscriptions/claude/warning")

    assert response.status_code == 200
    assert response.json()["category"] == "missing"
    assert "warning" not in response.json()
    assert warning_endpoint.status_code == 404
    assert "data-auth-policy-warning" not in home.text
    assert "data-subscription-warning" not in home.text
    assert "subscription-policy-warning" not in home.text
    assert "access-secret" not in response.text
    assert "refresh-secret" not in response.text


def test_subscription_routes_are_local_only(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app, client=("192.0.2.10", 50000))

    response = client.get("/subscriptions/status")

    assert response.status_code == 403
    assert "local-only" in response.text


def test_container_mode_subscription_login_accepts_private_gateway_source(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(
        tmp_path,
        backends={SubscriptionProvider.OPENAI: backend},
        container_mode=True,
    )
    client = TestClient(app, client=("172.18.0.1", 50000))

    response = client.post("/subscriptions/openai/login")

    assert response.status_code == 200
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    payload = response.json()
    assert payload["launch_url"] == "/subscriptions/openai/login/launch"
    assert "authorization_url" not in payload
    assert "transaction" not in payload
    assert transaction.authorization_url not in response.text
    assert transaction.state not in response.text
    assert transaction.verifier.get_secret_value() not in response.text
    assert handle not in response.text
    app.state.subscription_transactions.clear()


def test_container_mode_subscription_login_rejects_public_source(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.GOOGLE)
    app = _app(
        tmp_path,
        backends={SubscriptionProvider.GOOGLE: backend},
        container_mode=True,
    )
    client = TestClient(app, client=("8.8.8.8", 50000))

    response = client.post("/subscriptions/google/login")

    assert response.status_code == 403
    assert backend._transaction is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/subscriptions/status"),
        ("get", "/subscriptions/providers"),
        ("get", "/subscriptions/openai/status"),
        ("post", "/subscriptions/openai/login"),
        ("get", "/subscriptions/openai/login/launch"),
        ("get", "/auth/subscriptions/openai/login/launch"),
        ("get", "/auth/subscription/openai/login/launch"),
        ("get", "/subscriptions/openai/callback"),
        ("get", "/subscriptions/openai/auth/callback"),
        ("get", "/subscriptions/google/oauth2callback"),
        ("get", "/auth/subscriptions/openai/auth/callback"),
        ("get", "/auth/subscriptions/google/oauth2callback"),
        ("get", "/auth/subscription/claude/callback"),
        ("post", "/subscriptions/openai/logout"),
    ],
)
def test_all_subscription_lifecycle_routes_are_local_only(
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    client = TestClient(
        _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}),
        client=("192.0.2.10", 50000),
    )

    response = getattr(client, method)(path)

    assert response.status_code == 403
    assert "local-only" in response.text
    assert backend.login_calls == 0


def test_subscription_store_rejects_managed_directory_symlink(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_dir / "subscriptions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SubscriptionAuthError):
        SubscriptionStore(data_dir / "subscriptions")
    assert not (outside / "subscriptions.sqlite3").exists()
    assert not (outside / "subscriptions.key").exists()


@pytest.mark.parametrize("filename", ["subscriptions.sqlite3", "subscriptions.key"])
def test_subscription_store_rejects_managed_file_symlinks(
    tmp_path: Path,
    filename: str,
) -> None:
    store_dir = tmp_path / "subscriptions"
    store_dir.mkdir()
    outside = tmp_path / f"outside-{filename}"
    outside.write_bytes(b"not-a-managed-file")
    (store_dir / filename).symlink_to(outside)

    with pytest.raises(SubscriptionAuthError):
        SubscriptionStore(store_dir)


def test_claude_callback_errors_are_warning_free(tmp_path: Path) -> None:
    class ErrorBackend(_FakeBackend):
        def complete_login(
            self,
            code: str,
            state: str,
            transaction: SubscriptionLoginTransaction,
        ) -> SubscriptionCredential:
            del code, state, transaction
            raise RuntimeError("provider callback failure")

    backend = ErrorBackend(SubscriptionProvider.CLAUDE)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.CLAUDE: backend}))

    invalid = client.get("/subscriptions/claude/callback")
    unavailable = client.get(
        "/subscriptions/claude/callback?code=one&state=unknown-state"
    )
    client.post("/subscriptions/claude/login")
    assert backend._transaction is not None
    state = backend._transaction.state
    unexpected = client.get(f"/subscriptions/claude/callback?code=one&state={state}")

    for response in (invalid, unavailable, unexpected):
        assert response.status_code in {400, 409}
        assert "warning" not in response.json()


def test_unavailable_claude_callback_is_warning_free(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path, backends={}))

    response = client.get("/subscriptions/claude/callback")

    assert response.status_code == 404
    assert "warning" not in response.json()


def test_login_is_post_only_and_cross_origin_protected(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    get_response = client.get("/subscriptions/openai/login")
    post_response = client.post(
        "/subscriptions/openai/login",
        headers={"origin": "https://evil.example"},
    )

    assert get_response.status_code == 405
    assert post_response.status_code == 403
    assert backend._transaction is None


def test_subscription_callback_transaction_expires_and_is_not_completed(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)
    client.post("/subscriptions/openai/login")
    handle, pending_transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = pending_transaction.state
    provider, transaction, _expires_at = app.state.subscription_transactions[handle]
    app.state.subscription_transactions[handle] = (
        provider,
        transaction,
        datetime.now(UTC) - timedelta(seconds=1),
    )

    response = client.get(
        f"/subscriptions/openai/callback?code=authorization-code&state={state}"
    )

    assert response.status_code == 409
    assert backend.callback_calls == []


def test_subscription_callback_transaction_is_single_use(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)
    client.post("/subscriptions/openai/login")
    _handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state
    callback_url = (
        f"/subscriptions/openai/callback?code=authorization-code&state={state}"
    )

    first = client.get(callback_url)
    replay = client.get(callback_url)

    assert first.status_code == 200
    assert replay.status_code == 409
    assert backend.callback_calls == [("authorization-code", state)]


def test_subscription_callback_transaction_consumption_is_atomic(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)
    client.post("/subscriptions/openai/login")
    _handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state
    callback_url = (
        f"/subscriptions/openai/callback?code=authorization-code&state={state}"
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.get(callback_url), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(backend.callback_calls) == 1


def test_subscription_callback_transaction_is_provider_bound(tmp_path: Path) -> None:
    backends = {
        SubscriptionProvider.OPENAI: _FakeBackend(SubscriptionProvider.OPENAI),
        SubscriptionProvider.GOOGLE: _FakeBackend(SubscriptionProvider.GOOGLE),
    }
    app = _app(tmp_path, backends=backends)
    client = TestClient(app)
    client.post("/subscriptions/openai/login")
    _handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state

    mismatched = client.get(
        f"/subscriptions/google/callback?code=authorization-code&state={state}"
    )
    matching = client.get(
        f"/subscriptions/openai/callback?code=authorization-code&state={state}"
    )

    assert mismatched.status_code == 409
    assert matching.status_code == 200
    assert backends[SubscriptionProvider.OPENAI].callback_calls == [
        ("authorization-code", state)
    ]
    assert backends[SubscriptionProvider.GOOGLE].callback_calls == []


def test_real_openai_adapter_callback_persists_for_new_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOAuth:
        def build_authorization_url(
            self,
            endpoint: str,
            parameters: dict[str, str],
        ) -> str:
            return f"{endpoint}?{urlencode(parameters)}"

        def exchange_code(self, endpoint: str, **kwargs: str) -> dict[str, object]:
            assert endpoint.endswith("/oauth/token")
            assert kwargs["code"] == "authorization-code"
            assert kwargs["code_verifier"]
            return {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

    store = SubscriptionStore(tmp_path / "subscriptions")
    backend = OpenAICodexBackend(store=store, oauth_client=FakeOAuth())
    app = create_app(
        data_dir=tmp_path / "app-data",
        subscription_store=store,
        subscription_backends={SubscriptionProvider.OPENAI: backend},
    )
    client = TestClient(app)

    initiation = client.post("/subscriptions/openai/login")
    assert initiation.status_code == 200
    assert initiation.json()["status"] == "pending"
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    authorization_query = parse_qs(urlsplit(transaction.authorization_url).query)
    payload = initiation.json()
    assert payload["launch_url"] == "/subscriptions/openai/login/launch"
    assert "authorization_url" not in payload
    assert "transaction" not in payload
    assert transaction.authorization_url not in initiation.text
    assert transaction.state not in initiation.text
    assert transaction.verifier.get_secret_value() not in initiation.text
    assert handle not in initiation.text
    assert authorization_query["redirect_uri"] == [
        "http://localhost:1455/auth/callback"
    ]
    state = transaction.state
    callback = client.get(
        f"/subscriptions/openai/auth/callback?code=authorization-code&state={state}"
    )
    new_runtime = OpenAICodexBackend(
        store=SubscriptionStore(tmp_path / "subscriptions")
    )

    monkeypatch.setenv("MUDIDI_SUBSCRIPTION_STORE", str(tmp_path / "subscriptions"))
    runtime = resolve_subscription_runtime(
        AuthConfig(mode="subscription", providers=("openai",))
    )
    assert callback.status_code == 200
    assert callback.json()["status"] == "authenticated"
    assert new_runtime.status().authenticated
    assert runtime is not None
    assert runtime.backend.status().authenticated
    assert "refresh-secret" not in callback.text


def test_google_browser_login_uses_shared_oauth_transaction(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.GOOGLE)
    app = _app(tmp_path, backends={SubscriptionProvider.GOOGLE: backend})
    client = TestClient(app)

    response = client.post("/subscriptions/google/login")

    assert response.status_code == 200
    assert response.json()["provider"] == "google"
    assert response.json()["status"] == "pending"
    assert response.json()["launch_url"] == "/subscriptions/google/login/launch"
    assert any(
        provider is SubscriptionProvider.GOOGLE
        for provider, _transaction, _expires_at in app.state.subscription_transactions.values()
    )


def test_real_google_callback_persists_for_new_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class FakeOAuth:
        def build_authorization_url(
            self,
            endpoint: str,
            parameters: dict[str, str],
        ) -> str:
            return f"{endpoint}?{urlencode(parameters)}"

        def exchange_code(self, endpoint: str, **kwargs: str) -> dict[str, object]:
            assert endpoint == "https://oauth2.googleapis.com/token"
            assert kwargs["code"] == "authorization-code"
            assert kwargs["code_verifier"]
            assert (
                kwargs["client_id"]
                == "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
            )
            return {
                "access_token": "google-access-secret",
                "refresh_token": "google-refresh-secret",
                "token_type": "Bearer",
                "expires_in": 3600,
                "email": "research@example.test",
                "sub": "google-account-id",
                "project_id": "antigravity-project",
            }

    store_path = tmp_path / "subscriptions"
    store = SubscriptionStore(store_path)
    backend = GoogleAntigravityBackend(store=store, oauth_client=FakeOAuth())
    app = create_app(
        data_dir=tmp_path / "app-data",
        subscription_store=store,
        subscription_backends={SubscriptionProvider.GOOGLE: backend},
    )
    invalidated: list[Provider] = []
    original_invalidate = app.state.model_catalog_service.invalidate

    def track_invalidation(provider: Provider) -> None:
        invalidated.append(provider)
        original_invalidate(provider)

    monkeypatch.setattr(
        app.state.model_catalog_service,
        "invalidate",
        track_invalidation,
    )
    client = TestClient(app)

    initiation = client.post("/subscriptions/google/login")
    assert initiation.status_code == 200
    _handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.GOOGLE
    )
    authorization_query = parse_qs(urlsplit(transaction.authorization_url).query)
    assert authorization_query["redirect_uri"] == [
        "http://127.0.0.1:51121/oauth-callback"
    ]
    assert authorization_query["client_id"] == [
        "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    ]
    assert set(authorization_query["scope"][0].split()) == {
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/cclog",
        "https://www.googleapis.com/auth/experimentsandconfigs",
    }

    with urlopen(
        "http://127.0.0.1:51121/oauth-callback?"
        f"code=authorization-code&state={transaction.state}",
        timeout=2,
    ) as response:
        callback_body = response.read().decode("utf-8")
        assert response.status == 200

    monkeypatch.setenv("MUDIDI_SUBSCRIPTION_STORE", str(store_path))
    runtime = resolve_subscription_runtime(
        AuthConfig(mode="subscription", providers=("google",))
    )

    assert runtime is not None
    assert runtime.backend.status().authenticated
    assert "google-refresh-secret" not in callback_body
    logged_out = client.post("/subscriptions/google/logout")

    assert logged_out.status_code == 200
    assert logged_out.json()["authenticated"] is False
    assert logged_out.json()["credential_present"] is False
    assert logged_out.json()["removable"] is False
    assert not runtime.backend.status().authenticated
    assert invalidated == [Provider.GEMINI, Provider.GEMINI]


def test_real_claude_adapter_callback_uses_provider_redirect_alias(
    tmp_path: Path,
) -> None:
    class FakeOAuth:
        def build_authorization_url(
            self,
            endpoint: str,
            parameters: dict[str, str],
        ) -> str:
            return f"{endpoint}?{urlencode(parameters)}"

        def request_json_token(
            self,
            endpoint: str,
            parameters: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            assert endpoint.endswith("/oauth/token")
            assert parameters["grant_type"] == "authorization_code"
            assert parameters["state"]
            assert parameters["code_verifier"]
            return {
                "access_token": "claude-access-secret",
                "refresh_token": "claude-refresh-secret",
                "token_type": "Bearer",
                "expires_in": 3600,
                "account": {
                    "uuid": "claude-account",
                    "email_address": "claude@example.test",
                },
            }

    store = SubscriptionStore(tmp_path / "claude-subscriptions")
    backend = ClaudeResearchBackend(
        store=store,
        oauth_client=FakeOAuth(),
    )
    app = create_app(
        data_dir=tmp_path / "claude-app-data",
        subscription_store=store,
        subscription_backends={SubscriptionProvider.CLAUDE: backend},
    )
    client = TestClient(app)

    initiation = client.post("/subscriptions/claude/login")
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.CLAUDE
    )
    authorization_query = parse_qs(urlsplit(transaction.authorization_url).query)
    assert initiation.status_code == 200
    assert initiation.json()["status"] == "pending"
    payload = initiation.json()
    assert payload["launch_url"] == "/subscriptions/claude/login/launch"
    assert "authorization_url" not in payload
    assert "transaction" not in payload
    assert transaction.authorization_url not in initiation.text
    assert transaction.state not in initiation.text
    assert handle not in initiation.text
    assert transaction.verifier.get_secret_value() not in initiation.text
    assert authorization_query["redirect_uri"] == ["http://localhost:54545/callback"]
    state = transaction.state
    callback = client.get(
        f"/subscriptions/claude/callback?code=authorization-code&state={state}"
    )

    assert callback.status_code == 200
    assert callback.json()["status"] == "authenticated"
    assert (
        SubscriptionStore(tmp_path / "claude-subscriptions")
        .status(SubscriptionProvider.CLAUDE)
        .authenticated
    )
    assert "claude-access-secret" not in callback.text
    assert "claude-refresh-secret" not in callback.text


def _free_loopback_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_fixed_callback_receiver_completes_web_transaction(tmp_path: Path) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(
        tmp_path,
        backends={SubscriptionProvider.OPENAI: backend},
        container_mode=True,
    )
    client = TestClient(app)
    client.post("/subscriptions/openai/login")
    _handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state
    callback_url = (
        f"http://localhost:{port}/auth/callback?code=authorization-code&state={state}"
    )
    with urlopen(callback_url, timeout=2) as callback:
        body = callback.read().decode()
        assert callback.status == 200
        assert callback.headers["Content-Type"] == "text/html; charset=utf-8"
        assert callback.headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in callback.headers["Content-Security-Policy"]
    assert "<title>MUDIDI · Authentication successful</title>" in body
    assert 'data-auth-result="success"' in body
    assert "Authentication successful" in body
    assert "You have successfully logged in." in body
    assert "Please close this tab manually." in body
    assert backend.callback_calls == [("authorization-code", state)]
    assert client.get("/subscriptions/openai/status").json()["authenticated"] is True
    assert "authorization-code" not in body
    assert state not in body


def test_fixed_callback_failure_logs_only_safe_diagnostics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    port = _free_loopback_port()

    class ErrorBackend(_FakeBackend):
        def complete_login(
            self,
            code: str,
            state: str,
            transaction: SubscriptionLoginTransaction,
        ) -> SubscriptionCredential:
            del transaction
            raise SubscriptionAuthError(
                f"provider rejected {code} for {state}",
                provider=SubscriptionProvider.CLAUDE,
                status=400,
                metadata={"reason": "token_http_error"},
                secret_values=(code, state),
            )

    backend = ErrorBackend(
        SubscriptionProvider.CLAUDE,
        login_redirect_uri=f"http://localhost:{port}/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.CLAUDE: backend})
    client = TestClient(app)
    caplog.set_level(logging.WARNING, logger="mudidi.web.subscription")

    try:
        pending = client.post("/subscriptions/claude/login")
        assert pending.status_code == 200
        _handle, transaction, _expires_at = _pending_transaction(
            app, SubscriptionProvider.CLAUDE
        )
        callback_url = (
            f"http://localhost:{port}/callback"
            f"?code=authorization-code&state={transaction.state}"
        )

        with pytest.raises(HTTPError) as raised:
            urlopen(callback_url, timeout=2)
        deadline = time.monotonic() + 2
        while not caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)

        assert raised.value.code == 400
        error_body = raised.value.read().decode()
        assert raised.value.headers["Content-Type"] == "text/html; charset=utf-8"
        assert "<title>MUDIDI · Authentication failed</title>" in error_body
        assert 'data-auth-result="failure"' in error_body
        assert "Authentication failed" in error_body
        assert "MUDIDI could not complete authentication." in error_body
        assert "authorization-code" not in error_body
        assert transaction.state not in error_body
        assert (
            "Subscription OAuth callback failed provider=claude "
            "category=authentication status=400 reason=token_http_error"
        ) in caplog.text
        assert "authorization-code" not in caplog.text
        assert transaction.state not in caplog.text
        assert "provider rejected" not in caplog.text
    finally:
        client.close()


def test_fixed_callback_login_retry_replaces_abandoned_transaction(
    tmp_path: Path,
) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    try:
        first_response = client.post("/subscriptions/openai/login")
        assert first_response.status_code == 200
        first_handle, _first_transaction, _expires_at = _pending_transaction(
            app, SubscriptionProvider.OPENAI
        )
        first_receiver = app.state.subscription_receivers[first_handle]
        assert isinstance(first_receiver, LoopbackOAuthReceiver)

        second_response = client.post("/subscriptions/openai/login")
        assert second_response.status_code == 200, second_response.json()
        second_handle, _second_transaction, _expires_at = _pending_transaction(
            app, SubscriptionProvider.OPENAI
        )
        second_receiver = app.state.subscription_receivers[second_handle]

        assert isinstance(second_receiver, LoopbackOAuthReceiver)
        assert second_receiver is not first_receiver
        assert first_handle not in app.state.subscription_transactions
        assert first_handle not in app.state.subscription_receivers
        assert set(app.state.subscription_transactions) == {second_handle}
        assert app.state.subscription_receivers == {second_handle: second_receiver}
    finally:
        for receiver in list(app.state.subscription_receivers.values()):
            receiver.close()


def test_supersession_closes_inflight_receiver(tmp_path: Path) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    try:
        first_response = client.post("/subscriptions/openai/login")
        assert first_response.status_code == 200
        first_handle, _first_transaction, _expires_at = _pending_transaction(
            app, SubscriptionProvider.OPENAI
        )
        first_receiver = app.state.subscription_receivers[first_handle]
        assert isinstance(first_receiver, LoopbackOAuthReceiver)

        with app.state.subscription_transaction_lock:
            consumed = app.state.subscription_transactions.pop(first_handle)
        assert consumed[0] is SubscriptionProvider.OPENAI
        assert first_handle not in app.state.subscription_transactions
        assert app.state.subscription_receivers[first_handle] is first_receiver

        second_response = client.post("/subscriptions/openai/login")
        assert second_response.status_code == 200, second_response.json()
        second_handle, _second_transaction, _expires_at = _pending_transaction(
            app, SubscriptionProvider.OPENAI
        )
        second_receiver = app.state.subscription_receivers[second_handle]

        assert first_receiver._closed is True
        assert first_handle not in app.state.subscription_receivers
        assert isinstance(second_receiver, LoopbackOAuthReceiver)
        assert set(app.state.subscription_transactions) == {second_handle}
        assert app.state.subscription_receivers == {second_handle: second_receiver}
    finally:
        for receiver in list(app.state.subscription_receivers.values()):
            receiver.close()


def test_subscription_logout_cancels_pending_callback(tmp_path: Path) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    pending = client.post("/subscriptions/openai/login")
    assert pending.status_code == 200
    handle, transaction, _expires_at = _pending_transaction(
        app, SubscriptionProvider.OPENAI
    )
    state = transaction.state
    receiver = app.state.subscription_receivers[handle]
    assert isinstance(receiver, LoopbackOAuthReceiver)

    logged_out = client.post("/subscriptions/openai/logout")
    assert logged_out.status_code == 200
    assert logged_out.json()["authenticated"] is False
    assert app.state.subscription_transactions == {}
    assert app.state.subscription_receivers == {}
    assert receiver._closed is True

    with pytest.raises(SubscriptionError):
        receiver.handle_callback({"code": "authorization-code", "state": state})
    assert backend.login_calls == 0
    assert client.get("/subscriptions/openai/status").json()["authenticated"] is False


def test_fixed_callback_waiter_start_failure_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    class FailingThread:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread startup failed")

    monkeypatch.setattr("mudidi.web.app.Thread", FailingThread)
    try:
        response = client.post("/subscriptions/openai/login")
        assert response.status_code == 503
        assert app.state.subscription_transactions == {}
        assert app.state.subscription_receivers == {}
        probe = LoopbackOAuthReceiver(
            expected_state="state-value",
            host="127.0.0.1",
            port=port,
            path="/auth/callback",
            redirect_host="localhost",
            allow_fixed_port=True,
        )
        probe.close()
    finally:
        for receiver in list(app.state.subscription_receivers.values()):
            receiver.close()


def test_fixed_callback_timeout_cleans_up_transaction_receiver_and_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    monkeypatch.setattr(
        "mudidi.web.app._SUBSCRIPTION_TRANSACTION_TTL",
        timedelta(seconds=0.05),
    )
    client = TestClient(app)

    try:
        response = client.post("/subscriptions/openai/login")
        assert response.status_code == 200

        deadline = time.monotonic() + 2
        while (
            app.state.subscription_transactions or app.state.subscription_receivers
        ) and time.monotonic() < deadline:
            time.sleep(0.01)

        assert app.state.subscription_transactions == {}
        assert app.state.subscription_receivers == {}
        probe = LoopbackOAuthReceiver(
            expected_state="state-value",
            host="127.0.0.1",
            port=port,
            path="/auth/callback",
            redirect_host="localhost",
            allow_fixed_port=True,
        )
        probe.close()
    finally:
        for receiver in list(app.state.subscription_receivers.values()):
            receiver.close()


def test_fixed_callback_listener_collision_cleans_up_transaction_and_socket(
    tmp_path: Path,
) -> None:
    port = _free_loopback_port()
    holder = LoopbackOAuthReceiver(
        expected_state="holder-state",
        host="127.0.0.1",
        port=port,
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
    )
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)

    try:
        response = client.post("/subscriptions/openai/login")
        assert response.status_code == 503
        assert app.state.subscription_transactions == {}
        assert app.state.subscription_receivers == {}
    finally:
        holder.close()
        for receiver in list(app.state.subscription_receivers.values()):
            receiver.close()

    probe = LoopbackOAuthReceiver(
        expected_state="state-value",
        host="127.0.0.1",
        port=port,
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
    )
    probe.close()


def test_subscription_preview_rejects_models_outside_authenticated_catalog(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    backend.login()
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    response = _preview_subscription(
        client,
        tmp_path,
        model="openai/gpt-not-entitled",
    )

    assert response.status_code == 422
    assert "currently available from the openai subscription" in response.text


def test_subscription_preview_rejects_manual_model_entry(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.OPENAI)
    backend.login()
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.OPENAI: backend}))

    response = _preview_subscription(
        client,
        tmp_path,
        stage1_model="__other__",
        stage1_custom_model="gpt-manual",
    )

    assert response.status_code == 422
    assert "authenticated subscription" in response.text
