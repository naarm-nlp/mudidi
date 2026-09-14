"""End-to-end local fake-provider coverage for subscription routing."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient
import pytest

from mudidi.cli.run import execution_namespace_from_config
from mudidi.config.yaml_config import InferenceConfig
from mudidi.llm.client import resolve_subscription_runtime
from mudidi.llm.subscriptions import (
    CompletionRequest,
    SubscriptionCredential,
    SubscriptionProvider,
)
from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend
from mudidi.llm.subscriptions.storage import SubscriptionStore
from mudidi.web import jobs as jobs_module
from mudidi.web import production_worker
from mudidi.web.app import create_app
from mudidi.web.inference_worker import apply_credential_message
from mudidi.web.jobs import JobController, _credential_handoff
from mudidi.web.models import Provider

_ACCESS_TOKEN = "web-smoke-access-secret"
_REFRESH_TOKEN = "web-smoke-refresh-secret"
_AUTHORIZATION_CODE = "web-smoke-authorization-code"
_ENV_API_KEY = "environment-api-key-must-not-cross"
_ENV_HF_TOKEN = "environment-token-must-not-cross"
_ONE_BY_ONE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Response:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.status = status
        self.payload = json.dumps(payload).encode("utf-8")
        self.url: str | None = None

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]

    def close(self) -> None:
        return None


class _FakeOAuth:
    def __init__(self) -> None:
        self.authorization: tuple[str, dict[str, Any]] | None = None
        self.exchange_calls: list[dict[str, Any]] = []

    def build_authorization_url(
        self,
        endpoint: str,
        parameters: dict[str, Any],
    ) -> str:
        self.authorization = (endpoint, parameters)
        return f"{endpoint}?{urlencode(parameters)}"

    def exchange_code(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.exchange_calls.append({"endpoint": endpoint, **kwargs})
        return {
            "access_token": _ACCESS_TOKEN,
            "refresh_token": _REFRESH_TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
        }


def test_local_fake_provider_crosses_web_store_worker_and_result_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "app-data"
    store_path = data_dir / "subscriptions"
    store = SubscriptionStore(store_path)
    oauth = _FakeOAuth()
    requests: list[Any] = []

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
        raw_body = request.data
        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8")
        assert isinstance(raw_body, bytes)
        body = json.loads(raw_body.decode("utf-8"))
        assert _ACCESS_TOKEN not in json.dumps(body)
        assert _REFRESH_TOKEN not in json.dumps(body)
        return _Response(
            {
                "status": "completed",
                "model": "gpt-5.6-terra",
                "output_text": "\\lx fake-headword\\n\\ge fake-gloss",
                "usage": {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11},
            }
        )

    backend = OpenAICodexBackend(
        store=store,
        oauth_client=oauth,
        fetch=fetch,
    )
    app = create_app(
        data_dir=data_dir,
        subscription_store=store,
        subscription_backends={SubscriptionProvider.OPENAI: backend},
        offline_inference=True,
    )

    monkeypatch.setenv("OPENAI_API_KEY", _ENV_API_KEY)
    monkeypatch.setenv("HF_TOKEN", _ENV_HF_TOKEN)
    captured_environment: dict[str, str] | None = None
    real_popen = jobs_module.subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal captured_environment
        raw_environment = kwargs.get("env")
        assert isinstance(raw_environment, dict)
        captured_environment = dict(raw_environment)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(jobs_module.subprocess, "Popen", recording_popen)

    with TestClient(app) as client:
        pending = client.post("/subscriptions/openai/login")
        assert pending.status_code == 200
        pending_payload = pending.json()
        assert pending_payload["status"] == "pending"
        assert pending_payload["provider"] == "openai"
        assert oauth.authorization is not None
        authorization_parameters = oauth.authorization[1]
        state = authorization_parameters["state"]
        assert isinstance(state, str) and state
        assert authorization_parameters["code_challenge_method"] == "S256"
        assert "access_token" not in pending.text
        assert _ACCESS_TOKEN not in pending.text
        assert _REFRESH_TOKEN not in pending.text

        callback = client.get(
            "/subscriptions/openai/auth/callback",
            params={"code": _AUTHORIZATION_CODE, "state": state},
        )
        assert callback.status_code == 200
        callback_payload = callback.json()
        assert callback_payload["status"] == "authenticated"
        assert callback_payload["provider"] == "openai"
        assert callback_payload["authenticated"] is True
        assert _ACCESS_TOKEN not in callback.text
        assert _REFRESH_TOKEN not in callback.text
        assert "access_token" not in callback.text.lower()
        assert oauth.exchange_calls[0]["code"] == _AUTHORIZATION_CODE
        code_verifier = oauth.exchange_calls[0]["code_verifier"]
        assert isinstance(code_verifier, str) and code_verifier
        expected_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        assert authorization_parameters["code_challenge"] == expected_challenge
        assert code_verifier not in pending.text
        assert code_verifier not in callback.text

        loaded_store = SubscriptionStore(store_path)
        credential = loaded_store.load(SubscriptionProvider.OPENAI)
        assert credential is not None
        assert credential.access_token.get_secret_value() == _ACCESS_TOKEN
        assert credential.refresh_token is not None
        assert credential.refresh_token.get_secret_value() == _REFRESH_TOKEN
        assert _ACCESS_TOKEN.encode() not in store.database_path.read_bytes()
        assert _REFRESH_TOKEN.encode() not in store.database_path.read_bytes()

        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "page_1.png").write_bytes(base64.b64decode(_ONE_BY_ONE_PNG))
        output = tmp_path / "output"
        config = InferenceConfig.model_validate(
            {
                "kind": "inference",
                "input": {"pages": pages},
                "output": {"directory": output},
                "auth": {"mode": "subscription", "providers": ["openai"]},
                "pipeline": {"stage": "1"},
            }
        )
        namespace = execution_namespace_from_config(config)
        assert namespace.auth_mode == "subscription"
        assert namespace.auth_providers == ["openai"]
        assert not hasattr(namespace, "access_token")
        assert not hasattr(namespace, "refresh_token")

        controller = app.state.job_controller
        assert isinstance(controller, JobController)
        controller.prepare_inference(
            "fake-provider-smoke",
            config=config,
            provider=Provider.OPENAI,
        )
        descriptor_text = _credential_handoff(config, ())
        assert descriptor_text == '{"auth_mode":"subscription","providers":["openai"]}'
        descriptor = apply_credential_message(descriptor_text, environ={})
        assert descriptor is not None
        assert descriptor.providers == (SubscriptionProvider.OPENAI,)
        boundary_secrets = (
            _ACCESS_TOKEN,
            _REFRESH_TOKEN,
            _AUTHORIZATION_CODE,
            state,
            code_verifier,
            _ENV_API_KEY,
            _ENV_HF_TOKEN,
        )
        for secret in boundary_secrets:
            assert secret not in descriptor_text
        assert state not in descriptor_text

        controller.start_inference(
            "fake-provider-smoke",
            credentials=(),
            offline_executor=True,
        )
        assert captured_environment is not None
        assert "OPENAI_API_KEY" not in captured_environment
        assert "HF_TOKEN" not in captured_environment
        assert all(
            secret not in captured_environment.values() for secret in boundary_secrets
        )
        controller.wait("fake-provider-smoke", timeout=10)
        assert (
            controller.store.get_run("fake-provider-smoke").status.value == "completed"
        )

        command_text = " ".join(controller.command_for("fake-provider-smoke"))
        config_text = controller.config_path("fake-provider-smoke").read_text(
            encoding="utf-8"
        )
        event_text = json.dumps(controller.store.list_events("fake-provider-smoke"))
        log_text = controller.log_path("fake-provider-smoke").read_text(
            encoding="utf-8"
        )
        process = controller._workers["fake-provider-smoke"].process
        stderr_text = process.stderr.read() if process.stderr is not None else ""
        for boundary_text in (
            command_text,
            config_text,
            event_text,
            log_text,
            stderr_text,
        ):
            for secret in boundary_secrets:
                assert secret not in boundary_text
        assert '"auth": {' in config_text
        assert '"providers": [' in config_text
        assert '"openai"' in config_text
        assert "run.completed" in event_text

        runtime = resolve_subscription_runtime(
            config.auth,
            backend=OpenAICodexBackend(
                store=loaded_store,
                oauth_client=oauth,
                fetch=fetch,
            ),
        )
        assert runtime is not None
        assert runtime.provider is SubscriptionProvider.OPENAI
        normalized_result = runtime.backend.complete(
            CompletionRequest(
                model=config.models.default,
                messages=[{"role": "user", "content": "Extract this fake page."}],
            )
        )
        assert normalized_result.text == "\\lx fake-headword\\n\\ge fake-gloss"
        assert normalized_result.provider is SubscriptionProvider.OPENAI
        assert normalized_result.model == "gpt-5.6-terra"
        assert normalized_result.usage == {
            "input_tokens": 7,
            "output_tokens": 4,
            "total_tokens": 11,
        }
        assert normalized_result.billing_mode == "subscription"
        assert len(requests) == 1

    assert _ACCESS_TOKEN not in repr(callback_payload)
    assert _REFRESH_TOKEN not in repr(callback_payload)


def test_in_process_worker_main_resolves_descriptor_and_completes_fake_subscription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store_path = tmp_path / "subscriptions"
    store = SubscriptionStore(store_path)
    store.save(
        SubscriptionProvider.OPENAI,
        SubscriptionCredential(
            provider=SubscriptionProvider.OPENAI,
            account_label="in-process fake account",
            access_token=_ACCESS_TOKEN,
            refresh_token=_REFRESH_TOKEN,
        ),
    )
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(base64.b64decode(_ONE_BY_ONE_PNG))
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {"pages": pages},
            "output": {"directory": output},
            "auth": {"mode": "subscription", "providers": ["openai"]},
            "pipeline": {"stage": "1"},
            "models": {"default": "openai/gpt-5.6-terra"},
        }
    )
    config_path = tmp_path / "runs" / "in-process" / "resolved_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    requests: list[Any] = []
    observed: dict[str, Any] = {}

    def fetch(request: Any, **_kwargs: Any) -> _Response:
        requests.append(request)
        raw_body = request.data
        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8")
        assert isinstance(raw_body, bytes)
        body = json.loads(raw_body.decode("utf-8"))
        assert _ACCESS_TOKEN not in json.dumps(body)
        assert _REFRESH_TOKEN not in json.dumps(body)
        assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
        return _Response(
            {
                "status": "completed",
                "model": "gpt-5.6-terra",
                "output_text": "\\lx in-process\\n\\ge fake-result",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            }
        )

    def execute(
        phase_config: InferenceConfig,
        *,
        approved_parse_rules: object | None = None,
        progress_callback: object | None = None,
    ) -> int:
        del approved_parse_rules, progress_callback
        active_store_path = Path(os.environ["MUDIDI_SUBSCRIPTION_STORE"])
        reloaded_store = SubscriptionStore(active_store_path)
        credential = reloaded_store.load(SubscriptionProvider.OPENAI)
        assert credential is not None
        assert credential.access_token.get_secret_value() == _ACCESS_TOKEN
        backend = OpenAICodexBackend(store=reloaded_store, fetch=fetch)
        runtime = resolve_subscription_runtime(phase_config.auth, backend=backend)
        assert runtime is not None
        result = runtime.backend.complete(
            CompletionRequest(
                model=phase_config.models.default,
                messages=[{"role": "user", "content": "Extract this fake page."}],
            )
        )
        observed.update(
            {
                "store_path": str(active_store_path),
                "provider": runtime.provider,
                "text": result.text,
                "model": result.model,
                "usage": result.usage,
                "billing_mode": result.billing_mode,
            }
        )
        return 0

    monkeypatch.setattr(production_worker, "execute_extraction_config", execute)
    descriptor_text = '{"auth_mode":"subscription","providers":["openai"]}'
    monkeypatch.setattr(
        production_worker.sys,
        "stdin",
        io.StringIO(descriptor_text + "\n"),
    )
    log_path = tmp_path / "worker.log"
    worker_argv = [
        "--run-id",
        "in-process",
        "--config",
        str(config_path),
        "--phase",
        "stage1",
        "--log-file",
        str(log_path),
        "--subscription-store",
        str(store_path),
    ]
    result = production_worker.main(worker_argv)

    assert result == 0
    assert observed == {
        "store_path": str(store_path),
        "provider": SubscriptionProvider.OPENAI,
        "text": "\\lx in-process\\n\\ge fake-result",
        "model": "gpt-5.6-terra",
        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        "billing_mode": "subscription",
    }
    assert len(requests) == 1
    stdout_text = capsys.readouterr().out
    log_text = log_path.read_text(encoding="utf-8")
    boundary_texts = (
        descriptor_text,
        " ".join(worker_argv),
        config_path.read_text(encoding="utf-8"),
        stdout_text,
        log_text,
    )
    for boundary_text in boundary_texts:
        assert _ACCESS_TOKEN not in boundary_text
        assert _REFRESH_TOKEN not in boundary_text
    assert "run.completed" in stdout_text


def test_fake_provider_malformed_result_is_not_converted_to_success(
    tmp_path: Path,
) -> None:
    from mudidi.llm.subscriptions import SubscriptionTransportError

    credential_store = SubscriptionStore(tmp_path / "subscriptions")
    backend = OpenAICodexBackend(
        store=credential_store,
        fetch=lambda _request, **_kwargs: _Response({"status": "completed"}),
    )
    credential_store.save(
        SubscriptionProvider.OPENAI,
        {
            "provider": "openai",
            "account_label": "fake",
            "access_token": _ACCESS_TOKEN,
            "refresh_token": _REFRESH_TOKEN,
            "expires_at": datetime.now(UTC).isoformat(),
        },
    )

    with pytest.raises(SubscriptionTransportError) as raised:
        backend.complete(
            CompletionRequest(
                model="gpt-5.6-terra",
                messages=[{"role": "user", "content": "Malformed result."}],
            )
        )

    assert raised.value.metadata["reason"] == "missing_visible_text"
    assert _ACCESS_TOKEN not in repr(raised.value)
    assert _REFRESH_TOKEN not in repr(raised.value)
