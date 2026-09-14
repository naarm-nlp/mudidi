"""
Optional live LLM integration tests for direct ``reasoning_effort`` wiring.

These call the real provider APIs via ``mudidi.llm.client.complete`` (litellm).

Enable:
  export MUDIDI_LLM_INTEGRATION=1
  export OPENAI_API_KEY=...          # required for OpenAI test
  uv run --extra dev pytest tests/llm/test_client_reasoning_integration.py -v

Optional overrides (use cheaper / available models on your account):
  MUDIDI_INTEGRATION_OPENAI_MODEL=openai/gpt-5-mini
  MUDIDI_INTEGRATION_ANTHROPIC_MODEL=anthropic/claude-opus-4-7
"""

from __future__ import annotations

import os

import pytest

from mudidi.llm.client import complete
from mudidi.llm.reasoning import resolve_reasoning_profile

_INTEGRATION_ENABLED = os.getenv("MUDIDI_LLM_INTEGRATION", "").lower() in (
    "1",
    "true",
    "yes",
)

pytestmark = pytest.mark.integration


def _integration_skip() -> str | None:
    if not _INTEGRATION_ENABLED:
        return "Set MUDIDI_LLM_INTEGRATION=1 to run live LLM integration tests"
    return None


def _openai_model() -> str:
    return os.getenv("MUDIDI_INTEGRATION_OPENAI_MODEL", "openai/gpt-5-mini")


def _anthropic_model() -> str | None:
    return os.getenv("MUDIDI_INTEGRATION_ANTHROPIC_MODEL")


@pytest.fixture(autouse=True)
def _require_integration_gate() -> None:
    reason = _integration_skip()
    if reason:
        pytest.skip(reason)


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for direct OpenAI integration test",
)
def test_direct_openai_reasoning_effort_live() -> None:
    """Stage-2-style low effort on a direct OpenAI reasoning model."""
    model = _openai_model()
    assert resolve_reasoning_profile("openai", model).efforts, (
        f"{model!r} has no configured direct reasoning profile"
    )

    text = complete(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one word: ok",
            }
        ],
        max_tokens=128,
        reasoning_effort="low",
    )

    assert text.strip(), "Expected non-empty completion from OpenAI"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for direct OpenAI integration test",
)
def test_direct_openai_reasoning_none_live() -> None:
    """Stage-1-style none effort on a direct OpenAI reasoning model."""
    model = _openai_model()
    assert resolve_reasoning_profile("openai", model).efforts

    text = complete(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one word: ok",
            }
        ],
        max_tokens=128,
        reasoning_effort="none",
    )

    assert text.strip(), "Expected non-empty completion from OpenAI"


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY required for direct Anthropic integration test",
)
@pytest.mark.skipif(
    not os.getenv("MUDIDI_INTEGRATION_ANTHROPIC_MODEL"),
    reason=(
        "Set MUDIDI_INTEGRATION_ANTHROPIC_MODEL to a direct Anthropic reasoning "
        "model (e.g. anthropic/claude-opus-4-20250514) to avoid accidental spend"
    ),
)
def test_direct_anthropic_reasoning_effort_live() -> None:
    """Stage-2-style low effort on a direct Anthropic reasoning model."""
    model = _anthropic_model()
    assert model is not None
    assert resolve_reasoning_profile("anthropic", model).efforts, (
        f"{model!r} has no configured direct reasoning profile"
    )

    text = complete(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one word: ok",
            }
        ],
        max_tokens=128,
        reasoning_effort="low",
    )

    assert text.strip(), "Expected non-empty completion from Anthropic"
