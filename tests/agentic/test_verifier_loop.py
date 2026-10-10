from pathlib import Path

from mudidi.agentic.verifier_loop import (
    AgenticEdit,
    AgenticLoopConfig,
    AgenticVerifierDecision,
    actionable_edits,
    run_bounded_verifier_loop,
)


def _edit(
    line_index: int = 0,
    current_text: str = "bad",
    replacement_text: str = "good",
) -> AgenticEdit:
    return AgenticEdit(
        line_index=line_index,
        current_text=current_text,
        replacement_text=replacement_text,
        reason="the source shows the replacement",
    )


def _edits(*edits: AgenticEdit, confidence: float = 0.9) -> AgenticVerifierDecision:
    return AgenticVerifierDecision(
        action="targeted_edits",
        confidence=confidence,
        edits=list(edits) or [_edit()],
    )


def _accept() -> AgenticVerifierDecision:
    return AgenticVerifierDecision(action="accept", confidence=0.95)


def _run(tmp_path: Path, verify, rewrite, *, initial="bad line", **kwargs):
    config = kwargs.pop("config", AgenticLoopConfig(max_iterations=2))
    return run_bounded_verifier_loop(
        stage="stage1",
        initial_output=initial,
        artifact_dir=tmp_path,
        output_suffix=".txt",
        verify=verify,
        rewrite=rewrite,
        config=config,
        **kwargs,
    )


def _no_rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
    raise AssertionError("rewrite should not run")


def test_loop_accepts_initial_output_without_rewrite(tmp_path: Path) -> None:
    calls: list[str] = []

    def verify(output: str, attempt: int) -> AgenticVerifierDecision:
        calls.append(f"verify:{attempt}:{output}")
        return _accept()

    result = _run(tmp_path, verify, _no_rewrite, initial="initial transcript")

    assert result.output == "initial transcript"
    assert result.stop_reason == "accepted"
    assert result.rewrite_count == 0
    assert calls == ["verify:0:initial transcript"]
    assert (tmp_path / "attempt_0_output.txt").read_text() == "initial transcript"
    assert (tmp_path / "attempt_0_verifier.json").is_file()
    assert (tmp_path / "final_decision.json").is_file()


def test_loop_records_verifier_usage_in_artifacts_and_final_decision(tmp_path: Path) -> None:
    def verify(output: str, attempt: int):
        return (
            _accept(),
            {
                "model": "verifier",
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "reasoning_tokens": 2,
                "response_text_tokens": 1,
                "cost_usd": 0.25,
            },
        )

    result = _run(tmp_path, verify, _no_rewrite)

    assert result.agentic_usage_summary["total_cost_usd"] == 0.25
    assert result.attempts[0].verifier_usage["reasoning_tokens"] == 2
    assert (tmp_path / "attempt_0_verifier_usage.json").is_file()
    final_decision = (tmp_path / "final_decision.json").read_text()
    assert '"agentic_usage_summary"' in final_decision


def test_loop_stops_when_verifier_rejects(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: AgenticVerifierDecision(action="reject", confidence=0.9),
        _no_rewrite,
    )

    assert result.stop_reason == "rejected"
    assert result.output == "bad line"


def test_rewriter_receives_proposed_edits_and_applies_them_itself(tmp_path: Path) -> None:
    decisions = [_edits(_edit(0, "bad", "good")), _accept()]
    seen: list[tuple[str, AgenticVerifierDecision, int]] = []

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        seen.append((output, decision, attempt))
        return "good line"

    result = _run(tmp_path, lambda output, attempt: decisions[attempt], rewrite)

    # The loop hands the untouched output to the rewriter; no edit is applied by code.
    assert seen == [("bad line", decisions[0], 1)]
    assert result.output == "good line"
    assert result.stop_reason == "accepted"
    assert result.rewrite_count == 1
    assert (tmp_path / "attempt_1_output.txt").read_text() == "good line"
    assert (tmp_path / "attempt_1_verifier.json").is_file()


def test_loop_never_patches_text_without_the_rewriter(tmp_path: Path) -> None:
    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        return output  # the rewriter confirmed none of the proposed edits

    result = _run(tmp_path, lambda output, attempt: _edits(_edit(0, "bad", "good")), rewrite)

    assert result.output == "bad line"
    assert result.stop_reason == "unchanged"
    assert result.rewrite_count == 0


def test_loop_stops_when_edit_confidence_is_too_low(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: _edits(confidence=0.2),
        _no_rewrite,
    )

    assert result.stop_reason == "low_confidence_retry"


def test_targeted_edits_without_edits_is_an_invalid_decision(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: AgenticVerifierDecision(
            action="targeted_edits", confidence=0.9
        ),
        _no_rewrite,
    )

    assert result.stop_reason == "invalid_decision"


def test_edits_that_change_nothing_are_dropped(tmp_path: Path) -> None:
    decision = _edits(
        _edit(0, "same", "same"),
        _edit(1, "  ", ""),
        _edit(2, "bad", "good"),
    )
    assert [edit.line_index for edit in actionable_edits(decision)] == [2]

    received: list[AgenticVerifierDecision] = []

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        received.append(decision)
        return "good line"

    decisions = [decision, _accept()]
    _run(tmp_path, lambda output, attempt: decisions[attempt], rewrite)

    assert [edit.line_index for edit in received[0].edits] == [2]
    assert (tmp_path / "attempt_0_verifier_raw.json").is_file()


def test_only_no_op_edits_is_an_invalid_decision(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: _edits(_edit(0, "same", "same")),
        _no_rewrite,
    )

    assert result.stop_reason == "invalid_decision"


def test_insertions_and_deletions_are_actionable() -> None:
    decision = _edits(_edit(3, "", "missing line"), _edit(4, "extra", ""))

    assert len(actionable_edits(decision)) == 2


def test_loop_stops_when_verifier_repeats_the_same_edits(tmp_path: Path) -> None:
    rewrites: list[int] = []

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        rewrites.append(attempt)
        return f"rewrite {attempt}"

    result = _run(tmp_path, lambda output, attempt: _edits(), rewrite)

    assert rewrites == [1]
    assert result.stop_reason == "repeated_issue"
    assert result.output == "rewrite 1"


def test_loop_keeps_last_rewrite_when_budget_is_exhausted(tmp_path: Path) -> None:
    def verify(output: str, attempt: int) -> AgenticVerifierDecision:
        return _edits(_edit(attempt, f"bad {attempt}", "good"))

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        return f"rewrite {attempt}"

    result = _run(tmp_path, verify, rewrite)

    assert result.stop_reason == "max_iterations"
    assert result.rewrite_count == 2
    assert result.output == "rewrite 2"


def test_full_redo_rewrites_the_whole_output(tmp_path: Path) -> None:
    decisions = [
        AgenticVerifierDecision(
            action="full_redo",
            confidence=0.1,
            redo_reason="The transcript is from a different page.",
        ),
        _accept(),
    ]
    seen: list[str] = []

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int) -> str:
        seen.append(decision.action)
        return "fresh transcription"

    result = _run(tmp_path, lambda output, attempt: decisions[attempt], rewrite)

    # A full redo is not held back by the confidence gate.
    assert seen == ["full_redo"]
    assert result.output == "fresh transcription"
    assert result.stop_reason == "accepted"
    assert (tmp_path / "attempt_1_full_redo.txt").read_text() == "fresh transcription"


def test_full_redo_without_a_reason_is_an_invalid_decision(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: AgenticVerifierDecision(action="full_redo", confidence=0.9),
        _no_rewrite,
    )

    assert result.stop_reason == "invalid_decision"


def test_full_redo_is_refused_where_the_stage_cannot_redo(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        lambda output, attempt: AgenticVerifierDecision(
            action="full_redo", confidence=0.9, redo_reason="mostly wrong"
        ),
        _no_rewrite,
        allow_full_redo=False,
    )

    assert result.stop_reason == "full_redo_unavailable"
    assert result.output == "bad line"


def test_loop_records_rewriter_usage_when_rewriter_runs(tmp_path: Path) -> None:
    decisions = [_edits(), _accept()]

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int):
        return "good line", {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}

    result = _run(tmp_path, lambda output, attempt: decisions[attempt], rewrite)

    assert result.attempts[0].rewrite_usage == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
    }
    assert (tmp_path / "attempt_1_rewrite_usage.json").is_file()
    assert result.agentic_usage_summary["rewriter"]["total_tokens"] == 4


def test_subscription_billing_mode_survives_agentic_usage_aggregation(
    tmp_path: Path,
) -> None:
    decisions = [_edits(), _accept()]

    def verify(output: str, attempt: int):
        return (
            decisions[attempt],
            {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
                "cost_usd": None,
                "billing_mode": "subscription",
            },
        )

    def rewrite(output: str, decision: AgenticVerifierDecision, attempt: int):
        return (
            "corrected",
            {
                "prompt_tokens": 3,
                "completion_tokens": 1,
                "total_tokens": 4,
                "cost_usd": None,
                "billing_mode": "subscription",
            },
        )

    result = _run(tmp_path, verify, rewrite, initial="incorrect")

    assert result.agentic_usage_summary["billing_mode"] == "subscription"
    assert result.agentic_usage_summary["verifier"]["billing_mode"] == "subscription"
    assert result.agentic_usage_summary["rewriter"]["billing_mode"] == "subscription"
