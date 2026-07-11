"""Generic bounded verifier-rewriter loop.

The loop is deliberately small and stage-agnostic. Stage-specific code supplies
the verifier and rewriter callables; this module owns stop criteria and audit
artifacts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, Field


AgenticDecision = Literal["accept", "retry", "reject", "recover"]
AgenticSeverity = Literal["low", "medium", "high"]
AgenticStopReason = Literal[
    "accepted",
    "rejected",
    "max_iterations",
    "unchanged",
    "repeated_issue",
    "low_confidence_retry",
    "vague_retry",
    "catastrophic_recovery",
]

CATASTROPHIC_ISSUE_TYPES = frozenset(
    {
        "wrong_page",
        "page_mismatch",
        "wrong_page_content",
        "hallucinated_page",
        "catastrophic_mismatch",
        "wrong_content",
    }
)

WHOLE_PAGE_RETRY_PHRASES = (
    "complete pass",
    "full page",
    "entire page",
    "from scratch",
    "re-transcribe",
    "retranscribe",
    "whole page",
    "fresh transcription",
)


class AgenticIssue(BaseModel):
    """One verifier finding."""

    type: str = Field(description="Short stable issue type, e.g. reading_order_error.")
    severity: AgenticSeverity = "medium"
    evidence: str = ""
    suggested_fix: str = ""
    line_index: int | None = Field(
        default=None,
        description=(
            "0-based output line index for retry findings. Required whenever the "
            "issue can be localized to one output line."
        ),
    )
    current_text: str = Field(
        default="",
        description=(
            "Exact current output span to change. Required for retry text edits; "
            "leave empty only when the issue is not a direct text replacement."
        ),
    )
    expected_text: str = Field(
        default="",
        description=(
            "Exact replacement span or expected visible text. Required for retry "
            "text edits; use an empty string only when deleting current_text."
        ),
    )


class AgenticVerifierDecision(BaseModel):
    """Structured verifier response used by both stages."""

    decision: AgenticDecision
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[AgenticIssue] = Field(default_factory=list)
    retry_instruction: str = ""


class AgenticLoopConfig(BaseModel):
    """Loop controls shared by Stage 1 and Stage 2 agentic modes."""

    max_iterations: int = Field(default=2, ge=0)
    stop_on_repeated_issue: bool = True
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    require_concrete_retry_issue: bool = True
    prefer_verifier_patches: bool = True


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


@dataclass(frozen=True)
class VerifierPatchResult:
    """Output and issue disposition from deterministic verifier patches."""

    output: str
    applied_issues: tuple[AgenticIssue, ...]
    unresolved_issues: tuple[AgenticIssue, ...]


VerifyReturn = AgenticVerifierDecision | tuple[AgenticVerifierDecision, dict[str, Any]]
RewriteReturn = str | tuple[str, dict[str, Any]]
VerifyFn = Callable[[str, int], VerifyReturn]
RewriteFn = Callable[[str, AgenticVerifierDecision, int], RewriteReturn]


def _normalized_for_change_check(text: str) -> str:
    """Normalize only insignificant edges when detecting no-op rewrites."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _issue_signature(decision: AgenticVerifierDecision) -> tuple[str, ...]:
    """Return stable issue types used to catch repeated verifier loops."""
    return tuple(issue.type for issue in decision.issues if issue.type)


def _has_catastrophic_issue(decision: AgenticVerifierDecision) -> bool:
    """Whether verifier findings indicate a whole-page failure."""
    for issue in decision.issues:
        issue_type = issue.type.casefold()
        if issue_type in CATASTROPHIC_ISSUE_TYPES:
            return True
        evidence = issue.evidence.casefold()
        if any(
            phrase in evidence
            for phrase in (
                "wrong page",
                "different page",
                "page mismatch",
                "entirely different page",
                "not this page",
            )
        ):
            return True
    return False


def _is_whole_page_retry(decision: AgenticVerifierDecision) -> bool:
    """Whether a retry asks for broad re-transcription instead of localized edits."""
    if decision.decision != "retry":
        return False
    instruction = decision.retry_instruction.casefold()
    if any(phrase in instruction for phrase in WHOLE_PAGE_RETRY_PHRASES):
        return True
    if not decision.issues:
        return False
    vague_issues = [
        issue
        for issue in decision.issues
        if not issue.current_text.strip() and not issue.expected_text.strip()
    ]
    return len(vague_issues) >= max(2, len(decision.issues) // 2 + 1)


def normalize_catastrophic_decision(
    decision: AgenticVerifierDecision,
) -> AgenticVerifierDecision:
    """Promote catastrophic reject or whole-page retry decisions to recover."""
    if decision.decision == "recover":
        return decision
    if decision.decision == "reject" and _has_catastrophic_issue(decision):
        return decision.model_copy(update={"decision": "recover"})
    if decision.decision != "retry":
        return decision
    if _has_catastrophic_issue(decision):
        return decision.model_copy(update={"decision": "recover"})
    if not _has_concrete_retry_issue(decision) and _is_whole_page_retry(decision):
        return decision.model_copy(update={"decision": "recover"})
    return decision


def _has_concrete_retry_issue(decision: AgenticVerifierDecision) -> bool:
    """Whether a retry has enough localized evidence to justify rewriting."""
    for issue in decision.issues:
        has_text_evidence = bool(issue.evidence.strip()) and bool(issue.suggested_fix.strip())
        has_span_edit = bool(issue.current_text.strip()) and (
            issue.expected_text.strip() or issue.expected_text == ""
        )
        has_location = issue.line_index is not None and (
            bool(issue.evidence.strip()) or has_span_edit
        )
        has_parseable_fix = issue.line_index is not None and (
            _infer_issue_patch(issue) is not None
        )
        if has_text_evidence or has_span_edit or has_location or has_parseable_fix:
            return True
    return False


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


def _replace_once(text: str, old: str, new: str) -> str | None:
    if not old or old == new:
        return None
    if text.count(old) != 1:
        return None
    return text.replace(old, new, 1)


def _strip_patch_token(text: str) -> str:
    text = text.strip()
    text = text.strip("`")
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.strip()


def _infer_issue_patch(issue: AgenticIssue) -> tuple[str, str] | None:
    old = issue.current_text
    new = issue.expected_text
    if old.strip():
        return old, new

    suggested = issue.suggested_fix.strip()
    arrow_match = re.match(r"^(.{1,120}?)\s*(?:->|→)\s*(.{0,120})$", suggested)
    if arrow_match:
        inferred_old = _strip_patch_token(arrow_match.group(1))
        inferred_new = _strip_patch_token(arrow_match.group(2))
        if inferred_old and inferred_old != inferred_new:
            return inferred_old, inferred_new

    change_match = re.match(
        r"(?i)^change\s+(['\"`])(.{1,120}?)\1\s+to\s+(['\"`])(.{0,120}?)\3",
        suggested,
    )
    if change_match:
        inferred_old = change_match.group(2).strip()
        inferred_new = change_match.group(4).strip()
        if inferred_old and inferred_old != inferred_new:
            return inferred_old, inferred_new

    remove_match = re.match(
        r"(?i)^remove\s+(?:header|line|text)\s+(['\"`])(.{1,120}?)\1",
        suggested,
    )
    if remove_match:
        return remove_match.group(2), ""

    return None


def _apply_verifier_patches(
    output: str,
    decision: AgenticVerifierDecision,
) -> VerifierPatchResult:
    """Apply unambiguous edits and retain every issue that was not fixed."""

    lines = output.splitlines()
    trailing_newline = output.endswith("\n")
    patched_output = output
    applied: list[AgenticIssue] = []
    unresolved: list[AgenticIssue] = []

    for issue in decision.issues:
        patch = _infer_issue_patch(issue)
        if patch is None:
            unresolved.append(issue)
            continue
        current_text, expected_text = patch
        if issue.line_index is not None:
            if issue.line_index < 0 or issue.line_index >= len(lines):
                unresolved.append(issue)
                continue
            patched_line = _replace_once(
                lines[issue.line_index],
                current_text,
                expected_text,
            )
            if patched_line is None:
                unresolved.append(issue)
                continue
            lines[issue.line_index] = patched_line
            if patched_line == "":
                lines.pop(issue.line_index)
            patched_output = "\n".join(lines) + ("\n" if trailing_newline else "")
            applied.append(issue)
            continue

        patched = _replace_once(patched_output, current_text, expected_text)
        if patched is None:
            unresolved.append(issue)
            continue
        patched_output = patched
        lines = patched_output.splitlines()
        trailing_newline = patched_output.endswith("\n")
        applied.append(issue)

    return VerifierPatchResult(
        output=patched_output,
        applied_issues=tuple(applied),
        unresolved_issues=tuple(unresolved),
    )


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
) -> AgenticLoopResult:
    """Run a bounded stage-local verifier-rewriter loop.

    ``max_iterations`` counts rewrite attempts after the initial output. Attempt
    0 is always the normal stage output; attempt 1 is the first correction.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    current = initial_output
    attempts: list[AgenticAttempt] = []
    previous_retry_signature: tuple[str, ...] | None = None
    rewrite_count = 0

    _write_text(artifact_dir / f"attempt_0_output{output_suffix}", current)

    for attempt in range(config.max_iterations + 1):
        raw_decision, verifier_usage = _split_verify_result(verify(current, attempt))
        decision = normalize_catastrophic_decision(raw_decision)
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

        if decision.decision == "accept":
            return _finish(
                stage=stage,
                output=current,
                stop_reason="accepted",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        if decision.decision == "reject":
            return _finish(
                stage=stage,
                output=current,
                stop_reason="rejected",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        is_catastrophic = decision.decision == "recover"

        if not is_catastrophic and decision.confidence < config.min_retry_confidence:
            return _finish(
                stage=stage,
                output=current,
                stop_reason="low_confidence_retry",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        if (
            not is_catastrophic
            and config.require_concrete_retry_issue
            and not _has_concrete_retry_issue(decision)
        ):
            return _finish(
                stage=stage,
                output=current,
                stop_reason="vague_retry",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        signature = _issue_signature(decision)
        if (
            config.stop_on_repeated_issue
            and attempt > 0
            and signature
            and signature == previous_retry_signature
        ):
            return _finish(
                stage=stage,
                output=current,
                stop_reason="repeated_issue",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )
        previous_retry_signature = signature

        if rewrite_count >= config.max_iterations:
            return _finish(
                stage=stage,
                output=current,
                stop_reason="max_iterations",
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        next_attempt = attempt + 1
        rewritten = None
        rewrite_input = current
        rewrite_decision = decision
        needs_rewriter = True
        if not is_catastrophic and config.prefer_verifier_patches:
            patch_result = _apply_verifier_patches(current, decision)
            rewrite_input = patch_result.output
            if patch_result.unresolved_issues:
                decision_updates: dict[str, Any] = {
                    "issues": list(patch_result.unresolved_issues)
                }
                if patch_result.applied_issues:
                    unresolved_instructions = [
                        issue.suggested_fix.strip()
                        for issue in patch_result.unresolved_issues
                        if issue.suggested_fix.strip()
                    ]
                    decision_updates["retry_instruction"] = (
                        "\n".join(unresolved_instructions)
                        or "Resolve only the remaining verifier issues."
                    )
                rewrite_decision = decision.model_copy(update=decision_updates)
            elif patch_result.applied_issues:
                rewritten = patch_result.output
                needs_rewriter = False
        if needs_rewriter:
            rewritten, rewrite_usage = _split_rewrite_result(
                rewrite(rewrite_input, rewrite_decision, next_attempt)
            )
            if rewrite_usage:
                attempt_record.rewrite_usage = rewrite_usage
                _write_json_data(
                    artifact_dir / f"attempt_{next_attempt}_rewrite_usage.json",
                    rewrite_usage,
                )
        if _normalized_for_change_check(rewritten) == _normalized_for_change_check(current):
            stop_reason: AgenticStopReason = (
                "catastrophic_recovery" if is_catastrophic else "unchanged"
            )
            return _finish(
                stage=stage,
                output=current,
                stop_reason=stop_reason,
                rewrite_count=rewrite_count,
                attempts=attempts,
                artifact_dir=artifact_dir,
            )

        rewrite_count += 1
        current = rewritten
        _write_text(artifact_dir / f"attempt_{next_attempt}_output{output_suffix}", current)
        if is_catastrophic:
            _write_text(
                artifact_dir / f"attempt_{next_attempt}_catastrophic{output_suffix}",
                current,
            )

    return _finish(
        stage=stage,
        output=current,
        stop_reason="max_iterations",
        rewrite_count=rewrite_count,
        attempts=attempts,
        artifact_dir=artifact_dir,
    )
