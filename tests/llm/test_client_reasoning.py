"""Tests for direct vs OpenRouter reasoning parameter wiring."""

from __future__ import annotations
from types import SimpleNamespace

import pytest
from mudidi.llm import client

from mudidi.llm.client import _build_params, _completion_with_retries


def test_build_params_direct_openai_sets_reasoning_effort() -> None:
    params = _build_params(
        "openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort="low",
    )
    assert params["reasoning_effort"] == "low"
    assert "extra_body" not in params or "reasoning" not in params.get("extra_body", {})


def test_build_params_direct_openai_stage1_none() -> None:
    params = _build_params(
        "openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort="none",
    )
    assert params["reasoning_effort"] == "low"


def test_build_params_openrouter_uses_extra_body_reasoning() -> None:
    params = _build_params(
        "openrouter/openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort="none",
    )
    assert params["extra_body"]["reasoning"] == {
        "effort": "low",
        "exclude": True,
    }
    assert "reasoning_effort" not in params


def test_openrouter_empty_provider_order_uses_automatic_routing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "")

    params = _build_params(
        "openrouter/anthropic/claude-sonnet-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort="low",
    )

    assert "provider" not in params.get("extra_body", {})


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-4o",
        "openai/gpt-5.5",
        "gemini/gemini-2.5-pro",
        "gemini/gemini-3.1-pro-preview",
        "anthropic/claude-sonnet-4-6",
        "openrouter/anthropic/claude-sonnet-5",
    ],
)
def test_build_params_uses_provider_default_temperature(model: str) -> None:
    params = _build_params(
        model,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort=None,
    )

    assert "temperature" not in params


def test_build_params_gpt4o_ignores_reasoning_flag() -> None:
    params = _build_params(
        "openai/gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort="low",
    )
    assert "reasoning_effort" not in params


def test_explicit_invalid_effort_retries_once_with_nearest_allowed(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class InvalidEffort(Exception):
        status_code = 422

    def completion(**params):  # type: ignore[no-untyped-def]
        effort = params["reasoning_effort"]
        calls.append(effort)
        if len(calls) == 1:
            raise InvalidEffort(
                "reasoning_effort xhigh is unsupported; supported values are low, high, max"
            )
        return {"ok": True}

    monkeypatch.setattr(client, "litellm", SimpleNamespace(completion=completion))

    result = _completion_with_retries(
        {"model": "openai/gpt-5.5", "reasoning_effort": "xhigh"}
    )

    assert result == {"ok": True}
    assert calls == ["xhigh", "max"]


def test_ambiguous_or_second_effort_error_is_not_retried(
    monkeypatch,
) -> None:
    calls = 0

    class InvalidEffort(Exception):
        status_code = 400

    def completion(**_params):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise InvalidEffort(
            "reasoning_effort max is unsupported; allowed values: low, xhigh"
        )

    monkeypatch.setattr(client, "litellm", SimpleNamespace(completion=completion))

    with pytest.raises(InvalidEffort):
        _completion_with_retries(
            {"model": "openai/gpt-5.6-terra", "reasoning_effort": "max"}
        )

    assert calls == 2
