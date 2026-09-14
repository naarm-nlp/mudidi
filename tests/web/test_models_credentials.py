"""Tests for provider model discovery fallbacks and ephemeral credentials."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from mudidi.web import credentials as credential_module
from mudidi.web import models as model_module
from mudidi.web.credentials import CredentialSource, CredentialVault
from mudidi.web.models import (
    ModelCatalog,
    ModelDiscovery,
    ModelDiscoveryError,
    Provider,
    _fetch_json,
    _RejectRedirects,
    normalize_custom_model,
)


def test_bundled_catalog_excludes_openai_models_and_keeps_other_families() -> None:
    catalog = ModelCatalog.bundled()

    assert catalog.for_provider(Provider.OPENAI) == ()
    assert catalog.get("anthropic/claude-fable-5").image_input is True
    assert catalog.get("anthropic/claude-opus-5").image_input is True
    assert catalog.get("anthropic/claude-sonnet-5").image_input is True
    assert catalog.get("anthropic/claude-haiku-4-5").image_input is True
    assert catalog.get("gemini/gemini-3.1-pro-preview").image_input is True
    assert catalog.get("gemini/gemini-3.8-flash").image_input is True
    assert catalog.get("gemini/gemini-3.5-flash-lite").image_input is True
    assert catalog.for_provider(Provider.OPENROUTER) == ()


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
        normalize_custom_model(Provider.OPENROUTER, "anthropic/claude-opus-5")
        == "openrouter/anthropic/claude-opus-5"
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


def test_persistent_credentials_are_encrypted_and_survive_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mudidi-web.sqlite3"
    key_file = tmp_path / ".credential-key"
    store = credential_module.PersistentCredentialStore(
        database_path=database,
        key_path=key_file,
    )
    first = CredentialVault(environ={}, persistent_store=store)

    first.set_persistent(Provider.GEMINI, "gemini-private-key")

    assert b"gemini-private-key" not in database.read_bytes()
    assert b"gemini-private-key" not in key_file.read_bytes()
    assert key_file.stat().st_mode & 0o077 == 0
    restarted = CredentialVault(
        environ={},
        persistent_store=credential_module.PersistentCredentialStore(
            database_path=database,
            key_path=key_file,
        ),
    )
    resolved = restarted.resolve(Provider.GEMINI)
    assert resolved is not None
    assert resolved.source is CredentialSource.PERSISTENT
    assert resolved.get_secret_value() == "gemini-private-key"
    assert restarted.reveal_persistent(Provider.GEMINI) == "gemini-private-key"

    restarted.clear_persistent(Provider.GEMINI)

    assert restarted.resolve(Provider.GEMINI) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_credentials"
        ).fetchone() == (0,)


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


def test_anthropic_discovery_follows_last_id_without_duplicates() -> None:
    pages = {
        "https://api.anthropic.com/v1/models?limit=1000": {
            "data": [{"id": "claude-sonnet-current"}],
            "has_more": True,
            "last_id": "claude-sonnet-current",
        },
        (
            "https://api.anthropic.com/v1/models"
            "?limit=1000&after_id=claude-sonnet-current"
        ): {
            "data": [
                {"id": "claude-sonnet-current"},
                {"id": "claude-opus-current"},
            ],
            "has_more": False,
        },
    }
    calls: list[str] = []

    def fetch(url: str, _headers: Mapping[str, str]) -> dict[str, object]:
        calls.append(url)
        return pages[url]

    models = ModelDiscovery(fetch=fetch).discover(
        Provider.ANTHROPIC,
        api_key="private-key",
    )

    assert calls == list(pages)
    assert [model.model_id for model in models] == [
        "anthropic/claude-opus-current",
        "anthropic/claude-sonnet-current",
    ]


def test_gemini_discovery_follows_next_page_token() -> None:
    pages = {
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000": {
            "models": [
                {
                    "name": "models/gemini-first",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
            "nextPageToken": "next page",
        },
        (
            "https://generativelanguage.googleapis.com/v1beta/models"
            "?pageSize=1000&pageToken=next+page"
        ): {
            "models": [
                {
                    "name": "models/gemini-second",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    }
    calls: list[str] = []

    def fetch(url: str, _headers: Mapping[str, str]) -> dict[str, object]:
        calls.append(url)
        return pages[url]

    models = ModelDiscovery(fetch=fetch).discover(
        Provider.GEMINI,
        api_key="private-key",
    )

    assert calls == list(pages)
    assert [model.model_id for model in models] == [
        "gemini/gemini-first",
        "gemini/gemini-second",
    ]


def test_gemini_empty_page_stops_even_with_valid_page_token() -> None:
    calls = 0

    def fetch(_url: str, _headers: Mapping[str, str]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"models": [], "nextPageToken": "unused"}

    assert (
        ModelDiscovery(fetch=fetch).discover(
            Provider.GEMINI,
            api_key="private-key",
        )
        == ()
    )
    assert calls == 1


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        (
            Provider.GEMINI,
            {
                "models": [
                    {
                        "name": "models/gemini-current",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ],
                "nextPageToken": 7,
            },
        ),
        (
            Provider.GEMINI,
            {
                "models": [
                    {
                        "name": "models/gemini-current",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ],
                "nextPageToken": "",
            },
        ),
        (
            Provider.ANTHROPIC,
            {"data": [{"id": "claude-current"}], "has_more": "true"},
        ),
        (
            Provider.ANTHROPIC,
            {
                "data": [{"id": "claude-current"}],
                "has_more": True,
                "last_id": 7,
            },
        ),
    ],
)
def test_discovery_rejects_malformed_pagination_fields(
    provider: Provider,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ModelDiscoveryError, match="model discovery failed"):
        ModelDiscovery(fetch=lambda _url, _headers: payload).discover(
            provider,
            api_key="private-key",
        )


def test_pagination_rejects_current_url_outside_provider_host() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        model_module._next_page_url(
            Provider.ANTHROPIC,
            "https://attacker.example/v1/models",
            {"data": [], "has_more": True, "last_id": "next"},
        )


def test_discovery_rejects_repeated_page_token() -> None:
    def fetch(_url: str, _headers: Mapping[str, str]) -> dict[str, object]:
        return {
            "models": [
                {
                    "name": "models/gemini-current",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
            "nextPageToken": "same",
        }

    with pytest.raises(
        ModelDiscoveryError,
        match="gemini model discovery failed; bundled models remain available",
    ):
        ModelDiscovery(fetch=fetch).discover(
            Provider.GEMINI,
            api_key="private-key",
        )


def test_discovery_excludes_incompatible_model_families() -> None:
    payload = {
        "data": [
            {"id": "gpt-5.6"},
            {"id": "nova-text-preview"},
            {"id": "whisper-1"},
            {"id": "gpt-embedding-3-small"},
            {"id": "omni-moderation-latest"},
            {"id": "gpt-4o-transcribe"},
            {"id": "gpt-4o-audio-preview"},
            {"id": "gpt-image-1"},
            {"id": "gpt-rerank-1"},
            {"id": "gpt-safety-classifier"},
        ]
    }

    models = ModelDiscovery(fetch=lambda _url, _headers: payload).discover(
        Provider.OPENAI,
        api_key="private-key",
    )

    assert [model.model_id for model in models] == [
        "openai/gpt-5.6",
        "openai/nova-text-preview",
    ]


def test_model_redirect_handler_refuses_before_forwarding_credentials() -> None:
    request = Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": "Bearer private-key"},
    )

    redirected = _RejectRedirects().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://evil.example/models",
    )

    assert redirected is None


def test_model_opener_never_sends_credentials_to_redirect_target() -> None:
    requests: list[tuple[str, str | None]] = []

    class _RedirectServer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/first":
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self.server.server_port}/second",
                )
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectServer)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/first",
        headers={"Authorization": "Bearer private-key"},
    )
    try:
        with pytest.raises(HTTPError) as exc_info:
            model_module._MODEL_API_OPENER.open(request, timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert exc_info.value.code == 302
    assert requests == [("/first", "Bearer private-key")]


def test_model_fetch_rejects_redirect_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RedirectedResponse:
        def __enter__(self) -> RedirectedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://evil.example/models"

        def read(self, _limit: int) -> bytes:
            return b'{"data":[]}'

    monkeypatch.setattr(
        model_module._MODEL_API_OPENER,
        "open",
        lambda *_args, **_kwargs: RedirectedResponse(),
    )

    with pytest.raises(ValueError, match="allowlisted"):
        _fetch_json("https://api.openai.com/v1/models", {})
