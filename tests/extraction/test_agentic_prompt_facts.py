import pytest

from mudidi.extraction.llm_two_stage import (
    AGENTIC_VERIFIER_MAX_TOKENS_ENV,
    DEFAULT_AGENTIC_VERIFIER_MAX_TOKENS,
    _agentic_verifier_max_tokens,
    _stage1_editor_system_prompt,
    _stage1_verifier_system_prompt,
    _stage2_editor_system_prompt,
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


def test_verifier_prompts_ask_for_exact_targeted_edits() -> None:
    for prompt in (_stage1_verifier_system_prompt(), _stage2_verifier_system_prompt()):
        assert "action=targeted_edits" in prompt
        assert "line_index" in prompt
        assert "current_text copied exactly" in prompt
        assert "replacement_text" in prompt
        assert "Only propose an edit you can specify exactly" in prompt


def test_editor_prompts_rule_on_every_proposed_edit() -> None:
    for prompt in (_stage1_editor_system_prompt(), _stage2_editor_system_prompt()):
        assert "return one verdict per proposed edit" in prompt
        assert "Use verdict=apply only when the source confirms the change" in prompt
        assert "Use verdict=refuse" in prompt
        assert "Do not rewrite the output" in prompt
        assert "describe it in notes" in prompt


def test_evaluator_and_editor_share_acceptance_criteria() -> None:
    for evaluator, editor in (
        (_stage1_verifier_system_prompt(), _stage1_editor_system_prompt()),
        (_stage2_verifier_system_prompt(), _stage2_editor_system_prompt()),
    ):
        start = evaluator.index("Acceptance criteria, shared by")
        criteria = evaluator[start : evaluator.index("is not an error.", start)]
        assert criteria in editor


def test_evaluator_is_told_how_to_answer_a_refusal() -> None:
    for prompt in (_stage1_verifier_system_prompt(), _stage2_verifier_system_prompt()):
        assert "previous_rounds" in prompt
        assert "Do not repeat an edit the Editor refused" in prompt


def test_stage2_evaluator_prompt_carries_previous_rounds() -> None:
    from mudidi.agentic.verifier_loop import AgenticRound

    prompt = _stage2_verifier_user_text(
        "\\lx alpha",
        transcribed_text="alpha",
        field_map=_DummyFieldMap(),
        attempt=1,
        previous_rounds=[
            AgenticRound(attempt=0, action="targeted_edits", editor_notes="line 2 is fine")
        ],
    )

    assert "<previous_rounds>" in prompt
    assert "line 2 is fine" in prompt
    assert "<previous_rounds>" not in _stage2_verifier_user_text(
        "\\lx alpha",
        transcribed_text="alpha",
        field_map=_DummyFieldMap(),
        attempt=0,
    )


def test_both_evaluators_may_request_a_full_redo() -> None:
    stage1 = _stage1_verifier_system_prompt()
    stage2 = _stage2_verifier_system_prompt()

    assert "action=full_redo only when" in stage1
    assert "re-transcribe the entire page" in stage1
    assert "action=full_redo only when the output is not MDF at all" in stage2
    assert "parsed again from the transcript" in stage2
    for prompt in (stage1, stage2):
        assert "never send a placeholder edit" in prompt
        assert "Always state confidence" in prompt


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
