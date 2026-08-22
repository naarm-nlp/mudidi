"""Tests for historical benchmark typography and inference opt-in behavior."""

from __future__ import annotations

import pytest

from mudidi.extraction.llm_two_stage import (
    TwoStageLLMExtraction,
    _stage1_response_schema,
)
from mudidi.llm.prompt_store import configure_prompts, default_prompts_path
from mudidi.llm.prompts import stage_1_flat_system_prompt, stage_1_system_prompt
from mudidi.schemas.transcription import (
    FlatTranscriptionResponse,
    FlatTranscriptionResponsePlain,
    TranscriptionResponse,
    TranscriptionResponsePlain,
)


@pytest.fixture(autouse=True)
def _load_prompts() -> None:
    configure_prompts(default_prompts_path())


def test_flat_inference_prompt_is_plain_by_default() -> None:
    prompt = stage_1_flat_system_prompt(mode="inference")
    assert "Wrap bold text in <b>" not in prompt
    assert "other markup tags" not in prompt
    assert "Typography annotation:" not in prompt


def test_flat_inference_prompt_without_typography_is_plain() -> None:
    prompt = stage_1_flat_system_prompt(mode="inference", typography=False)
    assert "Wrap bold text in <b>" not in prompt
    assert "other markup tags" not in prompt
    assert "Typography annotation:" not in prompt


def test_flat_inference_prompt_with_typography_opt_in() -> None:
    prompt = stage_1_flat_system_prompt(mode="inference", typography=True)
    assert "Typography annotation:" in prompt
    assert "Wrap bold text in <b>" in prompt


@pytest.mark.parametrize("typography", [False, True])
def test_benchmark_flat_prompt_always_includes_historical_typography(
    typography: bool,
) -> None:
    prompt = stage_1_flat_system_prompt(
        mode="benchmark",
        typography=typography,
    )

    assert "Typography annotation:" in prompt
    assert "Wrap bold text in <b>" in prompt
    assert prompt.count("Typography annotation:") == 1


def test_benchmark_column_prompt_always_includes_historical_typography() -> None:
    prompt = stage_1_system_prompt(mode="benchmark", typography=False)

    assert "Typography annotation:" in prompt
    assert "Wrap bold text in <b>" in prompt


def test_benchmark_strategy_uses_typography_response_schema() -> None:
    strategy = TwoStageLLMExtraction(
        prompt_mode="benchmark",
        stage1_typography=False,
    )

    assert strategy.stage1_typography is True
    assert (
        _stage1_response_schema(flat=True, typography=strategy.stage1_typography)
        is FlatTranscriptionResponse
    )


def test_column_inference_prompt_without_typography() -> None:
    prompt = stage_1_system_prompt(mode="inference", typography=False)
    assert "wrap bold text in <b>" not in prompt.lower()
    assert "other markup tags" not in prompt
    assert "Typography annotation:" not in prompt


def test_stage1_response_schema_selector() -> None:
    assert _stage1_response_schema(flat=True, typography=True) is FlatTranscriptionResponse
    assert (
        _stage1_response_schema(flat=True, typography=False)
        is FlatTranscriptionResponsePlain
    )
    assert _stage1_response_schema(flat=False, typography=True) is TranscriptionResponse
    assert (
        _stage1_response_schema(flat=False, typography=False)
        is TranscriptionResponsePlain
    )
