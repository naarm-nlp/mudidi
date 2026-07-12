"""Tests for direct vs OpenRouter reasoning parameter wiring."""

from __future__ import annotations

from mudidi.llm.client import (
    _build_params,
    _direct_supports_reasoning_effort,
    _effective_temperature,
    _openrouter_supports_reasoning_api,
    _requires_temperature_one,
)


def test_direct_openai_gpt5_supports_reasoning() -> None:
    assert _direct_supports_reasoning_effort("openai/gpt-5.5")
    assert not _direct_supports_reasoning_effort("openai/gpt-4o")
    assert not _direct_supports_reasoning_effort("openrouter/openai/gpt-5.5")


def test_direct_anthropic_opus_supports_reasoning() -> None:
    assert _direct_supports_reasoning_effort("anthropic/claude-opus-4-20250514")
    assert not _direct_supports_reasoning_effort("anthropic/claude-3-5-sonnet-20241022")


def test_build_params_direct_openai_sets_reasoning_effort() -> None:
    params = _build_params(
        "openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="low",
    )
    assert params["reasoning_effort"] == "low"
    assert "extra_body" not in params or "reasoning" not in params.get("extra_body", {})


def test_build_params_direct_openai_stage1_none() -> None:
    params = _build_params(
        "openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="none",
    )
    assert params["reasoning_effort"] == "none"


def test_build_params_openrouter_uses_extra_body_reasoning() -> None:
    assert _openrouter_supports_reasoning_api("openrouter/openai/gpt-5.5")
    params = _build_params(
        "openrouter/openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="none",
    )
    assert params["extra_body"]["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in params


def test_openrouter_empty_provider_order_uses_automatic_routing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "")

    params = _build_params(
        "openrouter/anthropic/claude-sonnet-5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="low",
    )

    assert "provider" not in params.get("extra_body", {})


def test_gpt5_requires_temperature_one() -> None:
    assert _requires_temperature_one("openai/gpt-5.5")
    assert _requires_temperature_one("openrouter/openai/gpt-5.5")
    assert not _requires_temperature_one("openai/gpt-4o")


def test_build_params_gpt5_clamps_temperature() -> None:
    params = _build_params(
        "openai/gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="low",
    )
    assert params["temperature"] == 1.0
    assert _effective_temperature("openai/gpt-5.5", 0.1) == 1.0


def test_build_params_gpt4o_keeps_temperature() -> None:
    params = _build_params(
        "openai/gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort=None,
    )
    assert params["temperature"] == 0.1


def test_build_params_gpt4o_ignores_reasoning_flag() -> None:
    params = _build_params(
        "openai/gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="low",
    )
    assert "reasoning_effort" not in params
