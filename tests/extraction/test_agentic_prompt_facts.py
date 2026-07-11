import pytest

from mudidi.extraction.llm_two_stage import (
    AGENTIC_VERIFIER_MAX_TOKENS_ENV,
    DEFAULT_AGENTIC_VERIFIER_MAX_TOKENS,
    _agentic_verifier_max_tokens,
    _stage1_rewriter_system_prompt,
    _stage1_verifier_system_prompt,
    _stage2_rewriter_system_prompt,
    _stage2_grounding_summary,
    _stage2_verifier_system_prompt,
    _stage2_verifier_user_text,
)


class _DummyFieldMap:
    def format_prompt_block(self) -> str:
        return "\\lx headword\n\\ge English gloss"


def test_stage2_grounding_summary_reports_marker_and_coverage_facts() -> None:
    summary = _stage2_grounding_summary(
        "alpha beta gamma",
        "\\lx alpha\n\\ge invented gloss\n\n\\lx beta\n\\ge gamma",
    )

    assert "mdf_record_count: 2" in summary
    assert "mdf_field_line_count: 4" in summary
    assert "stage2_value_token_count: 5" in summary
    assert "stage2_value_tokens_found_in_stage1: 3" in summary
    assert "stage2_value_tokens_missing_from_stage1: 2" in summary
    assert "missing_stage2_value_tokens_sample: invented, gloss" in summary


def test_stage2_verifier_prompt_includes_grounding_summary() -> None:
    prompt = _stage2_verifier_user_text(
        "\\lx alpha\n\\ge invented",
        transcribed_text="alpha beta",
        field_map=_DummyFieldMap(),
        attempt=0,
    )

    assert "<deterministic_grounding_summary>" in prompt
    assert "mdf_record_count: 1" in prompt
    assert "stage2_value_tokens_missing_from_stage1: 1" in prompt
    assert "Use the deterministic grounding summary as a warning signal" in prompt


def test_agentic_prompts_require_localized_retry_evidence() -> None:
    verifier_prompt = _stage1_verifier_system_prompt() + _stage2_verifier_system_prompt()
    rewriter_prompt = _stage1_rewriter_system_prompt() + _stage2_rewriter_system_prompt()

    assert "line_index" in verifier_prompt
    assert "current_text" in verifier_prompt
    assert "expected_text" in verifier_prompt
    assert "Never leave current_text and expected_text empty" in verifier_prompt
    assert "minimum necessary edit" in rewriter_prompt


def test_agentic_verifier_max_tokens_defaults_to_safe_budget(monkeypatch) -> None:
    monkeypatch.delenv(AGENTIC_VERIFIER_MAX_TOKENS_ENV, raising=False)

    assert _agentic_verifier_max_tokens() == DEFAULT_AGENTIC_VERIFIER_MAX_TOKENS
    assert DEFAULT_AGENTIC_VERIFIER_MAX_TOKENS > 0


def test_agentic_verifier_max_tokens_blank_env_uses_default(monkeypatch) -> None:
    monkeypatch.setenv(AGENTIC_VERIFIER_MAX_TOKENS_ENV, " ")

    assert _agentic_verifier_max_tokens() == DEFAULT_AGENTIC_VERIFIER_MAX_TOKENS


def test_agentic_verifier_max_tokens_reads_env_override(monkeypatch) -> None:
    monkeypatch.setenv(AGENTIC_VERIFIER_MAX_TOKENS_ENV, "16000")

    assert _agentic_verifier_max_tokens() == 16000


def test_agentic_verifier_max_tokens_accepts_minimum_positive_value(monkeypatch) -> None:
    monkeypatch.setenv(AGENTIC_VERIFIER_MAX_TOKENS_ENV, "1")

    assert _agentic_verifier_max_tokens() == 1


@pytest.mark.parametrize("bad_value", ["0", "-1", "abc", "1.5"])
def test_agentic_verifier_max_tokens_rejects_invalid_env(
    monkeypatch,
    bad_value: str,
) -> None:
    monkeypatch.setenv(AGENTIC_VERIFIER_MAX_TOKENS_ENV, bad_value)

    with pytest.raises(ValueError, match="positive integer"):
        _agentic_verifier_max_tokens()
