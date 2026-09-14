"""Tests for account-aware stage model catalog behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from time import sleep
from typing import Any

from mudidi.web.models import (
    CatalogAuthMode,
    CatalogStage,
    LiveModelOption,
    ModelCatalog,
    ModelCatalogService,
    ModelDiscoveryError,
    ModelOption,
    sort_models_newest_first,
    Provider,
)


class _Credential:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _Discovery:
    def __init__(self, models: tuple[LiveModelOption, ...]) -> None:
        self.models = models
        self.models_by_key: dict[str, tuple[LiveModelOption, ...]] = {}
        self.error: ModelDiscoveryError | None = None
        self.calls: list[tuple[Provider, str]] = []

    def discover(
        self,
        provider: Provider,
        *,
        api_key: str,
    ) -> tuple[LiveModelOption, ...]:
        self.calls.append((provider, api_key))
        if self.error is not None:
            raise self.error
        return self.models_by_key.get(api_key, self.models)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 13, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def _option(
    model_id: str,
    *,
    recommended_for: tuple[str, ...],
) -> ModelOption:
    return ModelOption(
        model_id=model_id,
        display_name=model_id.rsplit("/", 1)[-1],
        provider=Provider.OPENAI,
        image_input=True,
        recommended_for=recommended_for,
        source_url="https://developers.openai.com/api/docs/models",
    )


def _live(model_id: str) -> LiveModelOption:
    return LiveModelOption(
        model_id=model_id,
        display_name=model_id.rsplit("/", 1)[-1],
        provider=Provider.OPENAI,
        image_input=None,
    )


def _catalog() -> ModelCatalog:
    return ModelCatalog(
        (
            _option(
                "openai/gpt-recommended",
                recommended_for=("stage1", "verification"),
            ),
            _option("openai/gpt-stage2", recommended_for=("stage2",)),
            _option("openai/gpt-unavailable", recommended_for=("stage1",)),
        ),
        as_of="2026-09-13",
    )


def test_live_openai_catalog_ignores_curated_stage_recommendations() -> None:
    discovery = _Discovery(
        (
            _live("openai/gpt-recommended"),
            _live("openai/gpt-stage2"),
            _live("openai/gpt-account-only"),
        )
    )
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert result.recommended == ()
    assert [item.model_id for item in result.available] == [
        "openai/gpt-account-only",
        "openai/gpt-recommended",
        "openai/gpt-stage2",
    ]
    assert {item.compatibility for item in result.available} == {"unverified"}
    assert result.source == "live"
    assert result.stale is False
    assert result.warning is None
    assert discovery.calls == [(Provider.OPENAI, "private-key")]


def test_missing_credential_returns_empty_authentication_required_result() -> None:
    discovery = _Discovery(())
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: None,
        clock=_Clock(),
    )

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert result.recommended == ()
    assert result.available == ()
    assert result.source == "authentication_required"
    assert result.warning is not None
    assert result.warning.code == "authentication_required"
    assert discovery.calls == []


def test_subscription_openai_catalog_uses_account_discovery_without_api_key() -> None:
    discovery = _Discovery(())
    subscription_calls: list[Provider] = []

    def reject_resolver(_provider: Provider) -> Any:
        raise AssertionError("subscription catalog resolved an API credential")

    def discover_subscription(
        provider: Provider,
    ) -> tuple[LiveModelOption, ...]:
        subscription_calls.append(provider)
        return (_live("openai/gpt-account-only"),)

    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=reject_resolver,
        subscription_auth_resolver=lambda _provider: True,
        subscription_discovery_resolver=discover_subscription,
        clock=_Clock(),
    )

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE2,
        auth_mode=CatalogAuthMode.SUBSCRIPTION,
    )
    cached = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.SUBSCRIPTION,
    )

    assert result.recommended == ()
    assert [item.model_id for item in result.available] == ["openai/gpt-account-only"]
    assert result.source == "live"
    assert result.stale is False
    assert result.warning is None
    assert cached.source == "cached"
    assert discovery.calls == []
    assert subscription_calls == [Provider.OPENAI]


def test_catalog_cache_honors_ttl_and_force_refresh() -> None:
    clock = _Clock()
    discovery = _Discovery((_live("openai/gpt-recommended"),))
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=clock,
    )

    first = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    cached = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE2,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    clock.advance(timedelta(minutes=15))
    expired = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    forced = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
        force_refresh=True,
    )

    assert first.source == "live"
    assert cached.source == "cached"
    assert expired.source == "live"
    assert forced.source == "live"
    assert len(discovery.calls) == 3


def test_catalog_cache_isolated_by_credential_and_invalidated_by_provider() -> None:
    current = [_Credential("first-private-key")]
    discovery = _Discovery(())
    discovery.models_by_key = {
        "first-private-key": (_live("openai/gpt-first-account"),),
        "second-private-key": (_live("openai/gpt-second-account"),),
    }
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: current[0],
        clock=_Clock(),
    )

    first = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    current[0] = _Credential("second-private-key")
    second = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    service.invalidate(Provider.OPENAI)
    service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert [item.model_id for item in first.available] == ["openai/gpt-first-account"]
    assert [item.model_id for item in second.available] == ["openai/gpt-second-account"]
    assert [key for _provider, key in discovery.calls] == [
        "first-private-key",
        "second-private-key",
        "second-private-key",
    ]


def test_failed_refresh_returns_last_success_without_secret_or_raw_error() -> None:
    clock = _Clock()
    discovery = _Discovery((_live("openai/gpt-account-only"),))
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=clock,
    )
    service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    discovery.error = ModelDiscoveryError(
        "provider said private-key is invalid: raw-provider-detail"
    )
    clock.advance(timedelta(minutes=16))

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    payload = repr(result.to_payload())

    assert result.source == "stale"
    assert result.stale is True
    assert [item.model_id for item in result.available] == ["openai/gpt-account-only"]
    assert result.warning is not None
    assert result.warning.code == "provider_unavailable"
    assert "private-key" not in payload
    assert "raw-provider-detail" not in payload


def test_first_openai_discovery_failure_returns_no_hard_coded_models() -> None:
    discovery = _Discovery(())
    discovery.error = ModelDiscoveryError("raw-provider-detail private-key")
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert result.source == "unavailable"
    assert result.recommended == ()
    assert result.available == ()
    assert result.warning is not None
    assert result.warning.code == "provider_unavailable"
    assert "private-key" not in repr(result.to_payload())
    assert "raw-provider-detail" not in repr(result.to_payload())


def test_invalidate_removes_cached_entries_for_provider() -> None:
    discovery = _Discovery((_live("openai/gpt-account-only"),))
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )
    for _ in range(2):
        service.list_models(
            Provider.OPENAI,
            stage=CatalogStage.STAGE1,
            auth_mode=CatalogAuthMode.API_KEY,
        )

    service.invalidate(Provider.OPENAI)
    service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert len(discovery.calls) == 2


def test_concurrent_stage_requests_share_one_provider_discovery() -> None:
    gate = Barrier(3)

    class _SlowDiscovery:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()

        def discover(
            self,
            provider: Provider,
            *,
            api_key: str,
        ) -> tuple[LiveModelOption, ...]:
            del provider, api_key
            with self.lock:
                self.calls += 1
            sleep(0.2)
            return (_live("openai/gpt-recommended"),)

    discovery = _SlowDiscovery()
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )

    def list_stage(stage: CatalogStage, *, force_refresh: bool) -> str:
        gate.wait()
        return service.list_models(
            Provider.OPENAI,
            stage=stage,
            auth_mode=CatalogAuthMode.API_KEY,
            force_refresh=force_refresh,
        ).source

    with ThreadPoolExecutor(max_workers=2) as executor:
        stage1 = executor.submit(
            list_stage,
            CatalogStage.STAGE1,
            force_refresh=True,
        )
        stage2 = executor.submit(
            list_stage,
            CatalogStage.STAGE2,
            force_refresh=True,
        )
        gate.wait()
        sources = {stage1.result(), stage2.result()}

    assert discovery.calls == 1
    assert sources == {"live", "cached"}


def test_concurrent_stage_requests_share_one_failed_discovery() -> None:
    gate = Barrier(3)

    class _SlowFailure:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()

        def discover(
            self,
            provider: Provider,
            *,
            api_key: str,
        ) -> tuple[LiveModelOption, ...]:
            del provider, api_key
            with self.lock:
                self.calls += 1
            sleep(0.2)
            raise ModelDiscoveryError("fixed safe error")

    discovery = _SlowFailure()
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=discovery,
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )

    def list_stage(stage: CatalogStage) -> tuple[str, str | None]:
        gate.wait()
        result = service.list_models(
            Provider.OPENAI,
            stage=stage,
            auth_mode=CatalogAuthMode.API_KEY,
            force_refresh=True,
        )
        return result.source, result.warning.code if result.warning else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        stage1 = executor.submit(list_stage, CatalogStage.STAGE1)
        stage2 = executor.submit(list_stage, CatalogStage.STAGE2)
        gate.wait()
        results = {stage1.result(), stage2.result()}

    assert discovery.calls == 1
    assert results == {("unavailable", "provider_unavailable")}


def test_model_order_uses_timestamp_then_numeric_revision() -> None:
    timestamped = LiveModelOption(
        model_id="gemini/gemini-3.7-flash",
        display_name="Gemini 3.7 Flash",
        provider=Provider.GEMINI,
        image_input=True,
        release_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    models = (
        LiveModelOption(
            model_id="gemini/gemini-3.9-flash",
            display_name="Gemini 3.9 Flash",
            provider=Provider.GEMINI,
            image_input=True,
        ),
        LiveModelOption(
            model_id="gemini/gemini-3.10-flash",
            display_name="Gemini 3.10 Flash",
            provider=Provider.GEMINI,
            image_input=True,
        ),
        timestamped,
    )

    assert [model.model_id for model in sort_models_newest_first(models)] == [
        "gemini/gemini-3.7-flash",
        "gemini/gemini-3.10-flash",
        "gemini/gemini-3.9-flash",
    ]


def test_catalog_payload_includes_public_reasoning_capabilities() -> None:
    service = ModelCatalogService(
        catalog=_catalog(),
        discovery=_Discovery((_live("openai/gpt-5.6-terra"),)),
        credential_resolver=lambda _provider: _Credential("private-key"),
        clock=_Clock(),
    )

    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )

    assert result.to_payload()["available"] == [
        {
            "model_id": "openai/gpt-5.6-terra",
            "display_name": "gpt-5.6-terra",
            "compatibility": "unverified",
            "reasoning": {
                "efforts": ["low", "medium", "high", "xhigh", "max"],
                "default": "medium",
                "supports_off": False,
                "source": "reviewed",
            },
        }
    ]
