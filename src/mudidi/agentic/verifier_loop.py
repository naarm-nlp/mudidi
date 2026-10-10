"""Generic bounded verifier-rewriter loop.

The loop is deliberately small and stage-agnostic. Stage-specific code supplies
the verifier and rewriter callables; this module owns stop criteria and audit
artifacts.

The verifier chooses one action. ``targeted_edits`` proposes localized changes
that the rewriter model checks against the source and applies itself; no edit
is applied by code. ``full_redo`` asks the rewriter to produce the whole output
again.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


AgenticAction = Literal["accept", "targeted_edits", "full_redo", "reject"]
AgenticSeverity = Literal["low", "medium", "high"]
AgenticStopReason = Literal[
    "accepted",
    "rejected",
    "max_iterations",
    "unchanged",
    "repeated_issue",
    "low_confidence_retry",
    "invalid_decision",
    "full_redo_unavailable",
]


class AgenticEdit(BaseModel):
    """One localized change proposed by the verifier."""

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
    """Structured verifier response used by both stages."""

    action: AgenticAction = Field(
        description=(
            "accept: the output is good enough. targeted_edits: specific lines "
            "need the changes listed in edits. full_redo: the output is too "
            "wrong for localized edits and must be produced again. reject: "
            "correction is unsafe."
        ),
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
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


class AgenticLoopConfig(BaseModel):
    """Loop controls shared by Stage 1 and Stage 2 agentic modes."""

    max_iterations: int = Field(default=2, ge=0)
    stop_on_repeated_issue: bool = True
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)


class AgenticAttempt(BaseModel):
    """Audit record for one verifier pass."""

    attempt: int
    decision: AgenticVerifierDecision
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
RewriteReturn = str | tuple[str, dict[str, Any]]
VerifyFn = Callable[[str, int], VerifyReturn]
RewriteFn = Callable[[str, AgenticVerifierDecision, int], RewriteReturn]


def _normalized_for_change_check(text: str) -> str:
    """Normalize only insignificant edges when detecting no-op rewrites."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def actionable_edits(decision: AgenticVerifierDecision) -> list[AgenticEdit]:
    """Return the proposed edits that ask for an actual change.

    An edit that names no text, or whose replacement equals the current text,
    gives the rewriter nothing to do and is dropped.
    """
    return [
        edit
        for edit in decision.edits
        if (edit.current_text.strip() or edit.replacement_text.strip())
        and edit.current_text != edit.replacement_text
    ]


def _decision_signature(decision: AgenticVerifierDecision) -> tuple[Any, ...]:
    """Return a stable identity used to catch a verifier repeating itself."""
    if decision.action == "full_redo":
        return ("full_redo",)
    return tuple(
        sorted(
            (edit.line_index, edit.current_text, edit.replacement_text)
            for edit in decision.edits
        )
    )


def _split_verify_result(result: VerifyReturn) -> tuple[AgenticVerifierDecision, dict[str, Any] | None]:
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, None


def _split_rewrite_result(result: RewriteReturn) -> tuple[str, dict[str, Any] | None]:
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
    rewrite: RewriteFn,
    config: AgenticLoopConfig,
    allow_full_redo: bool = True,
) -> AgenticLoopResult:
    """Run a bounded stage-local verifier-rewriter loop.

    ``max_iterations`` counts rewrite attempts after the initial output. Attempt
    0 is always the normal stage output; attempt 1 is the first correction.
    ``allow_full_redo`` is False for stages whose rewriter cannot produce the
    output again from scratch.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    current = initial_output
    attempts: list[AgenticAttempt] = []
    previous_signature: tuple[Any, ...] | None = None
    rewrite_count = 0

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
        raw_decision, verifier_usage = _split_verify_result(verify(current, attempt))
        decision = raw_decision
        if raw_decision.action == "targeted_edits":
            usable = actionable_edits(raw_decision)
            if len(usable) != len(raw_decision.edits):
                decision = raw_decision.model_copy(update={"edits": usable})
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

        if decision.action == "accept":
            return finish("accepted")
        if decision.action == "reject":
            return finish("rejected")

        is_full_redo = decision.action == "full_redo"
        if is_full_redo:
            if not allow_full_redo:
                return finish("full_redo_unavailable")
            if not decision.redo_reason.strip():
                return finish("invalid_decision")
        else:
            if decision.confidence < config.min_retry_confidence:
                return finish("low_confidence_retry")
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

        if rewrite_count >= config.max_iterations:
            return finish("max_iterations")

        next_attempt = attempt + 1
        rewritten, rewrite_usage = _split_rewrite_result(
            rewrite(current, decision, next_attempt)
        )
        if rewrite_usage:
            attempt_record.rewrite_usage = rewrite_usage
            _write_json_data(
                artifact_dir / f"attempt_{next_attempt}_rewrite_usage.json",
                rewrite_usage,
            )
        if _normalized_for_change_check(rewritten) == _normalized_for_change_check(current):
            return finish("unchanged")

        rewrite_count += 1
        current = rewritten
        _write_text(artifact_dir / f"attempt_{next_attempt}_output{output_suffix}", current)
        if is_full_redo:
            _write_text(
                artifact_dir / f"attempt_{next_attempt}_full_redo{output_suffix}",
                current,
            )

    return finish("max_iterations")
