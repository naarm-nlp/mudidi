"""Bounded evaluator-optimizer loop.

Two models check each other. The Evaluator judges an output against its source
and proposes targeted edits. The Editor verifies every proposed edit against
the same source and decides which to apply; each applied edit is carried out
by :func:`apply_edit`, an exact text replacement on one line. The Editor's
verdicts go back to the Evaluator, which reviews the result and may accept,
propose further edits, or answer a refusal.

The loop is stage-agnostic: stage-specific code supplies the model calls, and
this module owns the round structure, stop conditions and audit artifacts.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


AgenticAction = Literal["accept", "targeted_edits", "full_redo", "reject"]
AgenticSeverity = Literal["low", "medium", "high"]
AgenticEditStatus = Literal["applied", "refused", "failed", "unreviewed"]
AgenticStopReason = Literal[
    "accepted",
    "rejected",
    "max_iterations",
    "repeated_issue",
    "no_progress",
    "oscillation",
    "low_confidence_retry",
    "invalid_decision",
    "full_redo_unavailable",
]


class AgenticEdit(BaseModel):
    """One localized change proposed by the Evaluator."""

    line_index: int = Field(
        ge=0,
        description=(
            "0-based index of the output line to change. For a missing line, "
            "the index the new line should take."
        ),
    )
    current_text: str = Field(
        description=(
            "Exact text as it appears now on that line: the smallest span that "
            "must change. Empty only when adding a missing line."
        ),
    )
    replacement_text: str = Field(
        description=(
            "Text that should stand in place of current_text, exactly as the "
            "source shows it. Empty only when current_text must be deleted."
        ),
    )
    reason: str = Field(
        description="What in the source shows that the current text is wrong.",
    )
    severity: AgenticSeverity = "medium"


class AgenticVerifierDecision(BaseModel):
    """Structured Evaluator response used by both stages."""

    action: AgenticAction = Field(
        description=(
            "accept: the output meets the acceptance criteria. targeted_edits: "
            "specific lines need the changes listed in edits. full_redo: the "
            "output is too wrong for localized edits and must be produced "
            "again. reject: correction is unsafe."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are, from 0 to 1, that the chosen action is right. "
            "This is not a quality score for the output: a clear error you can "
            "point to deserves high confidence."
        ),
    )
    edits: list[AgenticEdit] = Field(
        default_factory=list,
        description="Required for targeted_edits; empty for every other action.",
    )
    redo_reason: str = Field(
        default="",
        description=(
            "Required for full_redo: why localized edits cannot repair the "
            "output. Empty for every other action."
        ),
    )


class AgenticEditVerdict(BaseModel):
    """The Editor's ruling on one proposed edit."""

    proposal_index: int = Field(
        ge=0,
        description="0-based position of the proposed edit in the Evaluator's edits list.",
    )
    verdict: Literal["apply", "refuse"] = Field(
        description=(
            "apply: the source confirms the change. refuse: the source does "
            "not confirm it, or the text to change cannot be found."
        ),
    )
    line_index: int = Field(
        ge=0,
        description="For apply: 0-based index of the output line to change.",
    )
    current_text: str = Field(
        description=(
            "For apply: exact text on that line to replace, copied from the "
            "output. Empty only when adding a missing line."
        ),
    )
    replacement_text: str = Field(
        description=(
            "For apply: the replacement exactly as the source shows it, which "
            "may differ from the proposal. Empty only when deleting."
        ),
    )
    reason: str = Field(
        description="What in the source supports this verdict.",
    )


class AgenticEditorResponse(BaseModel):
    """Structured Editor response: one verdict per proposed edit."""

    verdicts: list[AgenticEditVerdict] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description=(
            "Optional message to the Evaluator: problems it missed, or why a "
            "group of proposals was refused."
        ),
    )


class AgenticEditOutcome(BaseModel):
    """What happened to one proposed edit in a round."""

    proposal_index: int
    status: AgenticEditStatus
    detail: str = ""
    line_index: int
    current_text: str
    replacement_text: str
    reason: str = ""


class AgenticRound(BaseModel):
    """One Evaluator-to-Editor round, fed back to the Evaluator afterwards."""

    attempt: int
    action: AgenticAction
    proposed_edits: list[AgenticEdit] = Field(default_factory=list)
    outcomes: list[AgenticEditOutcome] = Field(default_factory=list)
    editor_notes: str = ""
    changed_output: bool = False


class AgenticLoopConfig(BaseModel):
    """Loop controls shared by Stage 1 and Stage 2 agentic modes."""

    max_iterations: int = Field(default=3, ge=0)
    stop_on_repeated_issue: bool = True
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)


class AgenticAttempt(BaseModel):
    """Audit record for one Evaluator pass and the round it started."""

    attempt: int
    decision: AgenticVerifierDecision
    round: AgenticRound | None = None
    verifier_usage: dict[str, Any] | None = None
    rewrite_usage: dict[str, Any] | None = None


class AgenticLoopResult(BaseModel):
    """Final output and audit metadata from a bounded loop run."""

    stage: str
    output: str
    stop_reason: AgenticStopReason
    rewrite_count: int
    attempt_count: int
    attempts: list[AgenticAttempt]
    agentic_usage_summary: dict[str, Any] = Field(default_factory=dict)


VerifyReturn = AgenticVerifierDecision | tuple[AgenticVerifierDecision, dict[str, Any]]
EditReturn = AgenticEditorResponse | tuple[AgenticEditorResponse, dict[str, Any]]
RedoReturn = str | tuple[str, dict[str, Any]]
VerifyFn = Callable[[str, int, list[AgenticRound]], VerifyReturn]
EditFn = Callable[[str, AgenticVerifierDecision, int], EditReturn]
RedoFn = Callable[[str, AgenticVerifierDecision, int], RedoReturn]


def _normalized_for_change_check(text: str) -> str:
    """Normalize only insignificant edges when detecting no-op rounds."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def actionable_edits(decision: AgenticVerifierDecision) -> list[AgenticEdit]:
    """Return the proposed edits that ask for an actual change.

    An edit that names no text, or whose replacement equals the current text,
    gives the Editor nothing to do and is dropped.
    """
    return [
        edit
        for edit in decision.edits
        if (edit.current_text.strip() or edit.replacement_text.strip())
        and edit.current_text != edit.replacement_text
    ]


def _edit_key(edit: AgenticEdit) -> tuple[str, str]:
    return (edit.current_text, edit.replacement_text)


def _decision_signature(decision: AgenticVerifierDecision) -> tuple[Any, ...]:
    """Return a stable identity used to catch an Evaluator repeating itself."""
    if decision.action == "full_redo":
        return ("full_redo",)
    return tuple(
        sorted(
            (edit.line_index, edit.current_text, edit.replacement_text)
            for edit in decision.edits
        )
    )


def apply_edit(
    lines: list[str],
    *,
    line_index: int,
    current_text: str,
    replacement_text: str,
) -> tuple[bool, str]:
    """Carry out one Editor-approved edit on ``lines`` in place.

    Replaces ``current_text`` with ``replacement_text`` on one line. An empty
    ``current_text`` inserts ``replacement_text`` as a new line at
    ``line_index``; a line left empty by a deletion is removed. The edit is
    refused, and ``lines`` left untouched, when the text is missing or
    ambiguous. Returns whether it was applied and a short explanation.
    """
    if not current_text:
        if not replacement_text.strip():
            return False, "nothing to insert"
        position = min(line_index, len(lines))
        lines.insert(position, replacement_text)
        return True, f"inserted as line {position}"

    if "\n" in current_text:
        return _apply_multiline_edit(
            lines, current_text=current_text, replacement_text=replacement_text
        )

    target: int | None = None
    if line_index < len(lines) and current_text in lines[line_index]:
        target = line_index
    else:
        holders = [i for i, line in enumerate(lines) if current_text in line]
        if not holders:
            return False, "current_text was not found in the output"
        if len(holders) > 1:
            return False, (
                f"current_text is not on line {line_index} and appears on "
                f"{len(holders)} other lines"
            )
        target = holders[0]
    if lines[target].count(current_text) > 1:
        return False, f"current_text appears more than once on line {target}"

    updated = lines[target].replace(current_text, replacement_text, 1)
    if not replacement_text and not updated.strip():
        del lines[target]
        return True, f"deleted line {target}"
    lines[target] = updated
    detail = f"replaced on line {target}"
    if target != line_index:
        detail += f" (proposed line {line_index})"
    return True, detail


def _apply_multiline_edit(
    lines: list[str],
    *,
    current_text: str,
    replacement_text: str,
) -> tuple[bool, str]:
    """Replace a span that covers several whole or partial consecutive lines."""
    text = "\n".join(lines)
    occurrences = text.count(current_text)
    if occurrences == 0:
        return False, "current_text was not found in the output"
    if occurrences > 1:
        return False, f"current_text appears {occurrences} times in the output"
    first_line = text[: text.index(current_text)].count("\n")
    updated = text.replace(current_text, replacement_text, 1).split("\n")
    if not replacement_text:
        # A deletion must not leave an empty line behind where the span was.
        updated = [
            line
            for index, line in enumerate(updated)
            if line.strip() or index != first_line
        ]
    lines[:] = updated
    span = current_text.count("\n") + 1
    verb = "deleted" if not replacement_text else "replaced"
    return True, f"{verb} {span} lines starting at line {first_line}"


def apply_editor_verdicts(
    output: str,
    decision: AgenticVerifierDecision,
    response: AgenticEditorResponse,
) -> tuple[str, list[AgenticEditOutcome]]:
    """Apply the Editor's approved edits and report what happened to each proposal."""
    lines = output.splitlines()
    trailing_newline = output.endswith("\n")
    outcomes: dict[int, AgenticEditOutcome] = {}

    ruled: dict[int, AgenticEditVerdict] = {}
    for verdict in response.verdicts:
        if verdict.proposal_index < len(decision.edits):
            ruled.setdefault(verdict.proposal_index, verdict)

    # Work from the bottom of the output up so that inserting or deleting a
    # line does not shift the lines that later edits refer to.
    for index, verdict in sorted(
        ruled.items(), key=lambda item: item[1].line_index, reverse=True
    ):
        if verdict.verdict == "refuse":
            proposed = decision.edits[index]
            outcomes[index] = AgenticEditOutcome(
                proposal_index=index,
                status="refused",
                line_index=proposed.line_index,
                current_text=proposed.current_text,
                replacement_text=proposed.replacement_text,
                reason=verdict.reason,
            )
            continue
        applied, detail = apply_edit(
            lines,
            line_index=verdict.line_index,
            current_text=verdict.current_text,
            replacement_text=verdict.replacement_text,
        )
        outcomes[index] = AgenticEditOutcome(
            proposal_index=index,
            status="applied" if applied else "failed",
            detail=detail,
            line_index=verdict.line_index,
            current_text=verdict.current_text,
            replacement_text=verdict.replacement_text,
            reason=verdict.reason,
        )

    for index, proposed in enumerate(decision.edits):
        if index not in outcomes:
            outcomes[index] = AgenticEditOutcome(
                proposal_index=index,
                status="unreviewed",
                detail="the Editor returned no verdict for this edit",
                line_index=proposed.line_index,
                current_text=proposed.current_text,
                replacement_text=proposed.replacement_text,
            )

    edited = "\n".join(lines) + ("\n" if trailing_newline and lines else "")
    return edited, [outcomes[index] for index in sorted(outcomes)]


def _split_usage(result: Any) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, None


def _merge_usage_totals(base: dict[str, Any], addition: dict[str, Any] | None) -> dict[str, Any]:
    if not addition:
        return dict(base)
    merged = dict(base)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = int(merged.get(key, 0) or 0) + int(addition.get(key, 0) or 0)
    for key in (
        "image_tokens",
        "text_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "reasoning_tokens",
        "response_text_tokens",
    ):
        if addition.get(key) is not None:
            merged[key] = int(merged.get(key, 0) or 0) + int(addition[key])
    base_cost = merged.get("cost_usd")
    add_cost = addition.get("cost_usd")
    if base_cost is not None and add_cost is not None:
        merged["cost_usd"] = round(float(base_cost) + float(add_cost), 8)
    elif add_cost is not None:
        merged["cost_usd"] = add_cost
    base_billing = merged.get("billing_mode")
    add_billing = addition.get("billing_mode")
    if add_billing is not None:
        if base_billing is None:
            merged["billing_mode"] = add_billing
        elif base_billing != add_billing:
            merged["billing_mode"] = "mixed"
    return merged


def _agentic_usage_summary(attempts: list[AgenticAttempt]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    verifier_total: dict[str, Any] = {}
    rewrite_total: dict[str, Any] = {}
    for attempt in attempts:
        verifier_total = _merge_usage_totals(verifier_total, attempt.verifier_usage)
        rewrite_total = _merge_usage_totals(rewrite_total, attempt.rewrite_usage)
        summary = _merge_usage_totals(summary, attempt.verifier_usage)
        summary = _merge_usage_totals(summary, attempt.rewrite_usage)
    if summary:
        if summary.get("cost_usd") is not None:
            summary["total_cost_usd"] = summary["cost_usd"]
        summary["verifier"] = verifier_total or None
        summary["rewriter"] = rewrite_total or None
    return summary


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        model.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_json_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _finish(
    *,
    stage: str,
    output: str,
    stop_reason: AgenticStopReason,
    rewrite_count: int,
    attempts: list[AgenticAttempt],
    artifact_dir: Path,
) -> AgenticLoopResult:
    result = AgenticLoopResult(
        stage=stage,
        output=output,
        stop_reason=stop_reason,
        rewrite_count=rewrite_count,
        attempt_count=len(attempts),
        attempts=attempts,
        agentic_usage_summary=_agentic_usage_summary(attempts),
    )
    _write_json(artifact_dir / "final_decision.json", result)
    return result


def run_bounded_verifier_loop(
    *,
    stage: str,
    initial_output: str,
    artifact_dir: Path,
    output_suffix: str,
    verify: VerifyFn,
    edit: EditFn,
    config: AgenticLoopConfig,
    redo: RedoFn | None = None,
) -> AgenticLoopResult:
    """Run a bounded stage-local evaluator-optimizer loop.

    One iteration is one Evaluator-to-Editor round. ``max_iterations`` caps the
    rounds; the Evaluator gets one more look after the last one. ``verify`` is
    given every earlier round so the Evaluator can see what the Editor did with
    its proposals. ``redo`` produces the output again from scratch and is
    omitted for stages that cannot do that.

    Besides the cap, the loop stops when it detects no progress: the Evaluator
    repeats a proposal the Editor refused or the same set of edits, two rounds
    in a row change nothing, or the output returns to an earlier version.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    current = initial_output
    attempts: list[AgenticAttempt] = []
    rounds: list[AgenticRound] = []
    seen_versions = [_normalized_for_change_check(current)]
    refused: set[tuple[str, str]] = set()
    previous_signature: tuple[Any, ...] | None = None
    rewrite_count = 0
    stalled_rounds = 0

    def finish(stop_reason: AgenticStopReason) -> AgenticLoopResult:
        return _finish(
            stage=stage,
            output=current,
            stop_reason=stop_reason,
            rewrite_count=rewrite_count,
            attempts=attempts,
            artifact_dir=artifact_dir,
        )

    _write_text(artifact_dir / f"attempt_0_output{output_suffix}", current)

    for attempt in range(config.max_iterations + 1):
        raw_decision, verifier_usage = _split_usage(
            verify(current, attempt, list(rounds))
        )
        decision = raw_decision
        repeats_refusal = False
        nothing_to_change = False
        if raw_decision.action == "targeted_edits":
            usable = actionable_edits(raw_decision)
            # Edits that change nothing are the Evaluator saying the output
            # needs no change, so treat them as acceptance.
            nothing_to_change = bool(raw_decision.edits) and not usable
            fresh = [item for item in usable if _edit_key(item) not in refused]
            repeats_refusal = bool(usable) and not fresh
            if len(fresh) != len(raw_decision.edits):
                decision = raw_decision.model_copy(update={"edits": fresh})
        attempt_record = AgenticAttempt(
            attempt=attempt,
            decision=decision,
            verifier_usage=verifier_usage,
        )
        attempts.append(attempt_record)
        _write_json(artifact_dir / f"attempt_{attempt}_verifier.json", decision)
        if decision is not raw_decision:
            _write_json(
                artifact_dir / f"attempt_{attempt}_verifier_raw.json",
                raw_decision,
            )
        if verifier_usage:
            _write_json_data(
                artifact_dir / f"attempt_{attempt}_verifier_usage.json",
                verifier_usage,
            )

        if decision.action == "accept" or nothing_to_change:
            return finish("accepted")
        if decision.action == "reject":
            return finish("rejected")

        is_full_redo = decision.action == "full_redo"
        if is_full_redo:
            if redo is None:
                return finish("full_redo_unavailable")
            if not decision.redo_reason.strip():
                return finish("invalid_decision")
        else:
            if decision.confidence < config.min_retry_confidence:
                return finish("low_confidence_retry")
            if repeats_refusal:
                return finish("repeated_issue")
            if not decision.edits:
                return finish("invalid_decision")

        signature = _decision_signature(decision)
        if (
            config.stop_on_repeated_issue
            and attempt > 0
            and signature == previous_signature
        ):
            return finish("repeated_issue")
        previous_signature = signature

        if attempt >= config.max_iterations:
            return finish("max_iterations")

        next_attempt = attempt + 1
        round_record = AgenticRound(
            attempt=attempt,
            action=decision.action,
            proposed_edits=list(decision.edits),
        )
        if is_full_redo:
            candidate, editor_usage = _split_usage(redo(current, decision, next_attempt))
        else:
            response, editor_usage = _split_usage(edit(current, decision, next_attempt))
            candidate, outcomes = apply_editor_verdicts(current, decision, response)
            round_record.outcomes = outcomes
            round_record.editor_notes = response.notes
            refused.update(
                (item.current_text, item.replacement_text)
                for item in outcomes
                if item.status == "refused"
            )
        if editor_usage:
            attempt_record.rewrite_usage = editor_usage
            _write_json_data(
                artifact_dir / f"attempt_{next_attempt}_rewrite_usage.json",
                editor_usage,
            )

        normalized = _normalized_for_change_check(candidate)
        changed = normalized != seen_versions[-1]
        round_record.changed_output = changed
        attempt_record.round = round_record
        rounds.append(round_record)
        _write_json(artifact_dir / f"attempt_{next_attempt}_editor.json", round_record)

        if not changed:
            stalled_rounds += 1
            if is_full_redo or stalled_rounds >= 2:
                return finish("no_progress")
            continue
        if normalized in seen_versions:
            return finish("oscillation")

        stalled_rounds = 0
        seen_versions.append(normalized)
        rewrite_count += 1
        current = candidate
        _write_text(artifact_dir / f"attempt_{next_attempt}_output{output_suffix}", current)
        if is_full_redo:
            _write_text(
                artifact_dir / f"attempt_{next_attempt}_full_redo{output_suffix}",
                current,
            )

    return finish("max_iterations")
