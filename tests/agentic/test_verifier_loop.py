import json
from pathlib import Path

from mudidi.agentic.verifier_loop import (
    AgenticEdit,
    AgenticEditorResponse,
    AgenticEditVerdict,
    AgenticLoopConfig,
    AgenticVerifierDecision,
    actionable_edits,
    apply_edit,
    apply_editor_verdicts,
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


def _verdict(index: int, proposal: AgenticEdit, verdict: str = "apply", **changes):
    fields = {
        "proposal_index": index,
        "verdict": verdict,
        "line_index": proposal.line_index,
        "current_text": proposal.current_text,
        "replacement_text": proposal.replacement_text,
        "reason": "checked against the source",
    }
    fields.update(changes)
    return AgenticEditVerdict(**fields)


def _approve_all(output: str, decision: AgenticVerifierDecision, attempt: int):
    return AgenticEditorResponse(
        verdicts=[_verdict(i, item) for i, item in enumerate(decision.edits)]
    )


def _refuse_all(output: str, decision: AgenticVerifierDecision, attempt: int):
    return AgenticEditorResponse(
        verdicts=[
            _verdict(i, item, "refuse", reason="the source shows the current text")
            for i, item in enumerate(decision.edits)
        ],
        notes="Nothing to change.",
    )


def _no_edit(output: str, decision: AgenticVerifierDecision, attempt: int):
    raise AssertionError("the Editor should not run")


def _run(tmp_path: Path, verify, edit=_approve_all, *, initial="bad line", **kwargs):
    config = kwargs.pop("config", AgenticLoopConfig())
    return run_bounded_verifier_loop(
        stage="stage1",
        initial_output=initial,
        artifact_dir=tmp_path,
        output_suffix=".txt",
        verify=verify,
        edit=edit,
        config=config,
        **kwargs,
    )


def _scripted(*decisions: AgenticVerifierDecision):
    def verify(output: str, attempt: int, rounds: list):
        return decisions[min(attempt, len(decisions) - 1)]

    return verify


# ── apply_edit ───────────────────────────────────────────────────────────────


def test_apply_edit_replaces_text_on_the_named_line() -> None:
    lines = ["ala fish", "alo water"]

    assert apply_edit(lines, line_index=1, current_text="water", replacement_text="rain") == (
        True,
        "replaced on line 1",
    )
    assert lines == ["ala fish", "alo rain"]


def test_apply_edit_finds_text_on_another_line_when_the_index_is_off() -> None:
    lines = ["ala fish", "alo water"]

    applied, detail = apply_edit(
        lines, line_index=0, current_text="water", replacement_text="rain"
    )

    assert applied
    assert detail == "replaced on line 1 (proposed line 0)"
    assert lines == ["ala fish", "alo rain"]


def test_apply_edit_refuses_missing_or_ambiguous_text() -> None:
    lines = ["a a", "b", "c x", "d x"]

    assert apply_edit(lines, line_index=1, current_text="zzz", replacement_text="q")[0] is False
    assert apply_edit(lines, line_index=0, current_text="a", replacement_text="q")[0] is False
    assert apply_edit(lines, line_index=1, current_text="x", replacement_text="q")[0] is False
    assert lines == ["a a", "b", "c x", "d x"]


def test_apply_edit_inserts_and_deletes_lines() -> None:
    lines = ["one", "three"]

    assert apply_edit(lines, line_index=1, current_text="", replacement_text="two")[0]
    assert lines == ["one", "two", "three"]
    assert apply_edit(lines, line_index=0, current_text="one", replacement_text="") == (
        True,
        "deleted line 0",
    )
    assert lines == ["two", "three"]
    assert apply_edit(lines, line_index=9, current_text="", replacement_text="four")[0]
    assert lines == ["two", "three", "four"]
    assert apply_edit(lines, line_index=0, current_text="", replacement_text=" ")[0] is False


def test_editor_verdicts_are_applied_and_every_proposal_gets_an_outcome() -> None:
    decision = _edits(
        _edit(0, "bad", "good"),
        _edit(1, "wrong", "right"),
        _edit(2, "gone", "back"),
        _edit(2, "third", "3rd"),
    )
    response = AgenticEditorResponse(
        verdicts=[
            _verdict(0, decision.edits[0], replacement_text="better"),
            _verdict(1, decision.edits[1], "refuse", reason="the source says wrong"),
            _verdict(2, decision.edits[2]),
            _verdict(9, decision.edits[0]),
        ]
    )

    edited, outcomes = apply_editor_verdicts(
        "bad line\nwrong line\nthird line\n", decision, response
    )

    assert edited == "better line\nwrong line\nthird line\n"
    assert [item.status for item in outcomes] == [
        "applied",
        "refused",
        "failed",
        "unreviewed",
    ]
    assert outcomes[0].replacement_text == "better"
    assert outcomes[1].reason == "the source says wrong"
    assert outcomes[2].detail == "current_text was not found in the output"


def test_insertions_do_not_shift_edits_higher_up_the_page() -> None:
    decision = _edits(_edit(0, "", "new first"), _edit(1, "b", "B"))

    edited, outcomes = apply_editor_verdicts("a\nb", decision, _approve_all("", decision, 1))

    assert edited == "new first\na\nB"
    assert [item.status for item in outcomes] == ["applied", "applied"]


# ── loop ─────────────────────────────────────────────────────────────────────


def test_loop_accepts_initial_output_without_an_editor_call(tmp_path: Path) -> None:
    calls: list[str] = []

    def verify(output: str, attempt: int, rounds: list) -> AgenticVerifierDecision:
        calls.append(f"verify:{attempt}:{output}")
        return _accept()

    result = _run(tmp_path, verify, _no_edit, initial="initial transcript")

    assert result.output == "initial transcript"
    assert result.stop_reason == "accepted"
    assert result.rewrite_count == 0
    assert calls == ["verify:0:initial transcript"]
    assert (tmp_path / "attempt_0_output.txt").read_text() == "initial transcript"
    assert (tmp_path / "attempt_0_verifier.json").is_file()
    assert (tmp_path / "final_decision.json").is_file()


def test_default_iteration_cap_is_three() -> None:
    assert AgenticLoopConfig().max_iterations == 3


def test_loop_stops_when_evaluator_rejects(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(AgenticVerifierDecision(action="reject", confidence=0.9)),
        _no_edit,
    )

    assert result.stop_reason == "rejected"
    assert result.output == "bad line"


def test_editor_approved_edit_is_applied_and_then_accepted(tmp_path: Path) -> None:
    result = _run(tmp_path, _scripted(_edits(_edit(0, "bad", "good")), _accept()))

    assert result.output == "good line"
    assert result.stop_reason == "accepted"
    assert result.rewrite_count == 1
    assert (tmp_path / "attempt_1_output.txt").read_text() == "good line"
    round_record = json.loads((tmp_path / "attempt_1_editor.json").read_text())
    assert round_record["changed_output"] is True
    assert round_record["outcomes"][0]["status"] == "applied"
    assert result.attempts[0].round is not None


def test_only_the_editors_version_of_an_edit_is_applied(tmp_path: Path) -> None:
    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        return AgenticEditorResponse(
            verdicts=[_verdict(0, decision.edits[0], replacement_text="fine")]
        )

    result = _run(tmp_path, _scripted(_edits(_edit(0, "bad", "good")), _accept()), edit)

    assert result.output == "fine line"


def test_evaluator_sees_what_the_editor_did_with_its_proposals(tmp_path: Path) -> None:
    seen: list[list] = []
    decisions = [_edits(_edit(0, "bad", "good")), _edits(_edit(0, "line", "LINE")), _accept()]

    def verify(output: str, attempt: int, rounds: list) -> AgenticVerifierDecision:
        seen.append(rounds)
        return decisions[attempt]

    result = _run(tmp_path, verify, _refuse_all)

    assert seen[0] == []
    assert len(seen[1]) == 1
    feedback = seen[1][0]
    assert feedback.changed_output is False
    assert feedback.editor_notes == "Nothing to change."
    assert feedback.outcomes[0].status == "refused"
    assert feedback.outcomes[0].reason == "the source shows the current text"
    # A second round in which nothing changes ends the loop.
    assert result.stop_reason == "no_progress"
    assert result.output == "bad line"


def test_one_refused_round_does_not_end_the_loop(tmp_path: Path) -> None:
    replies = iter([_refuse_all, _approve_all])

    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        return next(replies)(output, decision, attempt)

    result = _run(
        tmp_path,
        _scripted(_edits(_edit(0, "bad", "good")), _edits(_edit(0, "line", "LINE")), _accept()),
        edit,
    )

    assert result.output == "bad LINE"
    assert result.stop_reason == "accepted"


def test_loop_stops_when_evaluator_repeats_a_refused_edit(tmp_path: Path) -> None:
    edits: list[int] = []

    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        edits.append(attempt)
        return _refuse_all(output, decision, attempt)

    # The same edit comes back on a different line number; it is still a repeat.
    result = _run(
        tmp_path,
        _scripted(_edits(_edit(0, "bad", "good")), _edits(_edit(4, "bad", "good"))),
        edit,
    )

    assert edits == [1]
    assert result.stop_reason == "repeated_issue"
    assert (tmp_path / "attempt_1_verifier_raw.json").is_file()


def test_refused_edits_are_dropped_from_later_proposals(tmp_path: Path) -> None:
    received: list[list[str]] = []
    replies = iter([_refuse_all, _approve_all])

    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        received.append([item.current_text for item in decision.edits])
        return next(replies)(output, decision, attempt)

    _run(
        tmp_path,
        _scripted(
            _edits(_edit(0, "bad", "good")),
            _edits(_edit(0, "bad", "good"), _edit(0, "line", "LINE")),
            _accept(),
        ),
        edit,
    )

    assert received == [["bad"], ["line"]]


def test_loop_stops_when_the_output_returns_to_an_earlier_version(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(
            _edits(_edit(0, "bad", "good")),
            _edits(_edit(0, "good", "bad")),
            _accept(),
        ),
    )

    assert result.stop_reason == "oscillation"
    assert result.output == "good line"
    assert result.rewrite_count == 1


def test_loop_stops_when_evaluator_proposes_the_same_edits_again(tmp_path: Path) -> None:
    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        return AgenticEditorResponse(
            verdicts=[_verdict(0, decision.edits[0], current_text="line", replacement_text=f"l{attempt}")]
        )

    result = _run(tmp_path, _scripted(_edits(_edit(0, "bad", "good"))), edit)

    assert result.stop_reason == "repeated_issue"
    assert result.output == "bad l1"


def test_loop_stops_at_the_iteration_cap(tmp_path: Path) -> None:
    verifier_calls: list[int] = []

    def verify(output: str, attempt: int, rounds: list) -> AgenticVerifierDecision:
        verifier_calls.append(attempt)
        return _edits(_edit(0, f"w{attempt}", f"w{attempt + 1}"))

    result = _run(tmp_path, verify, initial="w0", config=AgenticLoopConfig(max_iterations=3))

    # Three Evaluator-to-Editor rounds, then one last Evaluator look.
    assert verifier_calls == [0, 1, 2, 3]
    assert result.stop_reason == "max_iterations"
    assert result.rewrite_count == 3
    assert result.output == "w3"


def test_loop_stops_when_edit_confidence_is_too_low(tmp_path: Path) -> None:
    result = _run(tmp_path, _scripted(_edits(confidence=0.2)), _no_edit)

    assert result.stop_reason == "low_confidence_retry"


def test_targeted_edits_without_edits_is_an_invalid_decision(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(AgenticVerifierDecision(action="targeted_edits", confidence=0.9)),
        _no_edit,
    )

    assert result.stop_reason == "invalid_decision"


def test_edits_that_change_nothing_are_dropped(tmp_path: Path) -> None:
    decision = _edits(
        _edit(0, "same", "same"),
        _edit(1, "  ", ""),
        _edit(0, "bad", "good"),
    )
    assert [item.current_text for item in actionable_edits(decision)] == ["bad"]

    received: list[AgenticVerifierDecision] = []

    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        received.append(decision)
        return _approve_all(output, decision, attempt)

    _run(tmp_path, _scripted(decision, _accept()), edit)

    assert [item.current_text for item in received[0].edits] == ["bad"]
    assert (tmp_path / "attempt_0_verifier_raw.json").is_file()


def test_only_no_op_edits_means_nothing_needs_changing(tmp_path: Path) -> None:
    result = _run(tmp_path, _scripted(_edits(_edit(0, "same", "same"))), _no_edit)

    assert result.stop_reason == "accepted"
    assert result.output == "bad line"


def test_evaluator_must_state_its_confidence() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgenticVerifierDecision(action="accept")
    assert "confidence" in AgenticVerifierDecision.model_json_schema()["required"]


def test_apply_edit_handles_a_span_across_several_lines() -> None:
    lines = ["\\lx a", "\\se extra", "\\ge made up", "\\lx b"]

    applied, detail = apply_edit(
        lines, line_index=1, current_text="\\se extra\n\\ge made up", replacement_text=""
    )

    assert applied and detail == "deleted 2 lines starting at line 1"
    assert lines == ["\\lx a", "\\lx b"]

    lines = ["one", "two", "three"]
    assert apply_edit(
        lines, line_index=0, current_text="one\ntwo", replacement_text="1\n2"
    ) == (True, "replaced 2 lines starting at line 0")
    assert lines == ["1", "2", "three"]
    assert apply_edit(lines, line_index=0, current_text="x\ny", replacement_text="")[0] is False


def test_full_redo_replaces_the_whole_output(tmp_path: Path) -> None:
    redo_request = AgenticVerifierDecision(
        action="full_redo",
        confidence=0.1,
        redo_reason="The transcript is from a different page.",
    )

    result = _run(
        tmp_path,
        _scripted(redo_request, _accept()),
        _no_edit,
        redo=lambda output, decision, attempt: "fresh transcription",
    )

    # A full redo is not held back by the confidence gate.
    assert result.output == "fresh transcription"
    assert result.stop_reason == "accepted"
    assert (tmp_path / "attempt_1_full_redo.txt").read_text() == "fresh transcription"


def test_full_redo_without_a_reason_is_an_invalid_decision(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(AgenticVerifierDecision(action="full_redo", confidence=0.9)),
        _no_edit,
        redo=lambda output, decision, attempt: "unused",
    )

    assert result.stop_reason == "invalid_decision"


def test_full_redo_is_refused_where_the_stage_cannot_redo(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(
            AgenticVerifierDecision(
                action="full_redo", confidence=0.9, redo_reason="mostly wrong"
            )
        ),
        _no_edit,
    )

    assert result.stop_reason == "full_redo_unavailable"
    assert result.output == "bad line"


def test_full_redo_that_changes_nothing_stops_the_loop(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _scripted(
            AgenticVerifierDecision(
                action="full_redo", confidence=0.9, redo_reason="mostly wrong"
            )
        ),
        _no_edit,
        redo=lambda output, decision, attempt: output,
    )

    assert result.stop_reason == "no_progress"


def test_loop_records_usage_for_both_roles(tmp_path: Path) -> None:
    decisions = [_edits(), _accept()]
    usage = {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
        "cost_usd": None,
        "billing_mode": "subscription",
    }

    def verify(output: str, attempt: int, rounds: list):
        return decisions[attempt], dict(usage, reasoning_tokens=2)

    def edit(output: str, decision: AgenticVerifierDecision, attempt: int):
        return _approve_all(output, decision, attempt), dict(usage)

    result = _run(tmp_path, verify, edit)

    assert result.attempts[0].verifier_usage["reasoning_tokens"] == 2
    assert result.attempts[0].rewrite_usage == usage
    assert (tmp_path / "attempt_0_verifier_usage.json").is_file()
    assert (tmp_path / "attempt_1_rewrite_usage.json").is_file()
    assert result.agentic_usage_summary["rewriter"]["total_tokens"] == 4
    assert result.agentic_usage_summary["billing_mode"] == "subscription"
    assert result.agentic_usage_summary["verifier"]["billing_mode"] == "subscription"
    assert '"agentic_usage_summary"' in (tmp_path / "final_decision.json").read_text()
