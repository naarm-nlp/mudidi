from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import mudidi.cli.main as cli_module
from mudidi.llm.subscriptions import (
    BackendCapabilities,
    SubscriptionAuthError,
    SubscriptionCredential,
    SubscriptionProvider,
    SubscriptionStatus,
)


class _FakeAuthBackend:
    def __init__(self, provider: SubscriptionProvider) -> None:
        self.provider = provider
        self.login_calls = 0
        self.logout_calls = 0
        self.authenticated = True
        self.secret = "access-secret-must-not-print"
        self.account_label = f"{provider.value}-account"
        self.expires_at = datetime.now(UTC) + timedelta(hours=1)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(model_discovery=False)

    def status(self) -> SubscriptionStatus:
        metadata: dict[str, bool | str] = {"quota_state": "available"}
        if self.authenticated:
            metadata["category"] = "authenticated"
        if self.provider is SubscriptionProvider.CLAUDE:
            metadata.update(research_only=True, opt_in_enabled=False)
        return SubscriptionStatus(
            provider=self.provider,
            authenticated=self.authenticated,
            account_label=self.account_label if self.authenticated else None,
            expires_at=self.expires_at if self.authenticated else None,
            metadata=metadata,
        )

    def login(self) -> SubscriptionCredential:
        self.login_calls += 1
        self.authenticated = True
        return SubscriptionCredential(
            provider=self.provider,
            account_label=self.account_label,
            access_token=self.secret,
            expires_at=self.expires_at,
        )

    def logout(self) -> None:
        self.logout_calls += 1
        self.authenticated = False


def _patch_backends(
    monkeypatch: pytest.MonkeyPatch,
    backends: dict[SubscriptionProvider, _FakeAuthBackend],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_build_subscription_backend",
        lambda provider: backends[provider],
    )


def test_google_auth_factory_uses_encrypted_subscription_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "subscriptions"
    monkeypatch.setenv("MUDIDI_SUBSCRIPTION_STORE", str(store_path))

    backend = cli_module._build_subscription_backend(SubscriptionProvider.GOOGLE)

    from mudidi.llm.subscriptions.google_antigravity import (
        GoogleAntigravityBackend,
    )

    assert isinstance(backend, GoogleAntigravityBackend)
    assert backend._store.database_path == store_path / "subscriptions.sqlite3"


def test_auth_status_is_human_readable_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = _FakeAuthBackend(SubscriptionProvider.OPENAI)
    _patch_backends(monkeypatch, {SubscriptionProvider.OPENAI: backend})

    assert cli_module.main(["auth", "status", "--provider", "openai"]) == 0

    output = capsys.readouterr().out
    assert "Provider: openai" in output
    assert "Authenticated: yes" in output
    assert "Account: openai-account" in output
    assert "Expiry:" in output
    assert "Category: authenticated" in output
    assert backend.secret not in output
    assert "access_token" not in output
    assert "refresh_token" not in output


def test_auth_status_reports_unauthenticated_claude_without_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = _FakeAuthBackend(SubscriptionProvider.CLAUDE)
    backend.authenticated = False
    _patch_backends(monkeypatch, {SubscriptionProvider.CLAUDE: backend})

    assert cli_module.main(["auth", "status", "--provider", "claude"]) == 0

    output = capsys.readouterr().out
    assert "Category: missing" in output
    assert "Policy warning:" not in output


def test_auth_login_calls_backend_lifecycle_without_printing_credential(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = _FakeAuthBackend(SubscriptionProvider.GOOGLE)
    backend.authenticated = False
    _patch_backends(monkeypatch, {SubscriptionProvider.GOOGLE: backend})

    assert cli_module.main(["auth", "login", "--provider", "google"]) == 0

    output = capsys.readouterr().out
    assert backend.login_calls == 1
    assert "Logged in" in output
    assert "google" in output
    assert backend.secret not in output
    assert "SubscriptionCredential" not in output


def test_auth_login_can_replace_a_corrupt_stored_credential(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _RecoverableBackend(_FakeAuthBackend):
        def status(self) -> SubscriptionStatus:
            if not self.authenticated:
                raise SubscriptionAuthError(
                    "stored subscription credential is invalid",
                    provider=self.provider,
                    metadata={"reason": "corrupt_record"},
                )
            return super().status()

    backend = _RecoverableBackend(SubscriptionProvider.GOOGLE)
    backend.authenticated = False
    _patch_backends(monkeypatch, {SubscriptionProvider.GOOGLE: backend})

    assert cli_module.main(["auth", "login", "--provider", "google"]) == 0

    output = capsys.readouterr().out
    assert backend.login_calls == 1
    assert "Logged in" in output


def test_auth_logout_only_calls_selected_provider_backend(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    openai = _FakeAuthBackend(SubscriptionProvider.OPENAI)
    google = _FakeAuthBackend(SubscriptionProvider.GOOGLE)
    _patch_backends(
        monkeypatch,
        {
            SubscriptionProvider.OPENAI: openai,
            SubscriptionProvider.GOOGLE: google,
        },
    )

    assert cli_module.main(["auth", "logout", "--provider", "openai"]) == 0

    output = capsys.readouterr().out
    assert openai.logout_calls == 1
    assert google.logout_calls == 0
    assert "Logged out" in output
    assert "openai" in output
    assert openai.secret not in output
    assert google.secret not in output


def test_auth_status_rejects_invalid_provider() -> None:
    with pytest.raises(SystemExit):
        cli_module.main(["auth", "status", "--provider", "not-a-provider"])


@pytest.mark.parametrize("command", ["status", "login", "logout"])
def test_auth_rejects_conflicting_provider_spellings(command: str) -> None:
    with pytest.raises(SystemExit):
        cli_module.main(["auth", command, "openai", "--provider", "google"])
