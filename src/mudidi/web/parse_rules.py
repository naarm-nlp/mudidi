"""Crash-conscious parse-rule review, drafting, and approval service."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from mudidi.execution.approval import ApprovedParseRules, mint_approved_parse_rules
from mudidi.schemas.field_cheatsheet import validate_marker_cheatsheet
from mudidi.web.runs import RunStore

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class ReviewStatus(StrEnum):
    """Durable state of a single parse-rule review version."""

    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class ParseRuleReview:
    """Artifact and provenance metadata for one run's review checkpoint."""

    review_id: str
    run_id: str
    version: int
    status: ReviewStatus
    generated_path: Path
    draft_path: Path | None
    approved_snapshot_path: Path | None
    approval_digest: str | None
    validation_error: str | None
    sample_pages: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None


class ParseRuleReviewService:
    """Manage rules in app-owned storage and mint approval capabilities."""

    def __init__(self, *, store: RunStore, data_dir: Path) -> None:
        self.store = store
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def create_generated(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        sample_pages: list[str],
    ) -> ParseRuleReview:
        """Persist generated JSON and durably enter human review."""

        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        return self._create_from_bytes(run_id, raw, sample_pages=sample_pages)

    def import_external(self, run_id: str, path: Path) -> ParseRuleReview:
        """Copy external mutable rules into the same mandatory review flow."""

        try:
            raw = path.expanduser().resolve().read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read external parse rules: {exc}") from exc
        return self._create_from_bytes(run_id, raw, sample_pages=[])

    def get(self, run_id: str) -> ParseRuleReview:
        """Load durable metadata for one run's parse-rule review."""

        return _review_from_row(self.store.get_parse_rule_review(run_id))

    def save_draft(self, run_id: str, payload: dict[str, Any]) -> ParseRuleReview:
        """Validate, normalize, and atomically replace the editable draft."""

        validated = validate_marker_cheatsheet(payload)
        raw = _canonical_bytes(validated.model_dump(mode="json"))
        review = self.get(run_id)
        draft_path = review.generated_path.parent / "parse-rules.draft.json"
        _atomic_write(draft_path, raw)
        return _review_from_row(
            self.store.update_parse_rule_draft(run_id, draft_path=draft_path)
        )

    def load_editable_payload(self, run_id: str) -> dict[str, Any]:
        """Load the current draft or generated JSON for structured editing."""

        review = self.get(run_id)
        source = review.draft_path or review.generated_path
        try:
            payload = json.loads(source.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"parse-rule review content is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("parse-rule review root must be an object")
        return payload

    def approve(self, run_id: str) -> ApprovedParseRules:
        """Freeze reviewed bytes and atomically authorize Stage 2 Pass 2."""

        review = self.get(run_id)
        if review.status is ReviewStatus.APPROVED:
            return self._mint_existing(review)
        source = review.draft_path or review.generated_path
        try:
            payload = json.loads(source.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"parse-rule review content is unreadable: {exc}") from exc
        validated = validate_marker_cheatsheet(payload)
        raw = _canonical_bytes(validated.model_dump(mode="json"))
        digest = hashlib.sha256(raw).hexdigest()
        approved_dir = review.generated_path.parent / "approved"
        approved_dir.mkdir(parents=True, exist_ok=True)
        snapshot = approved_dir / f"{digest}.json"
        _write_immutable(snapshot, raw)
        committed = _review_from_row(
            self.store.commit_parse_rule_approval(
                run_id,
                snapshot_path=snapshot,
                approval_digest=digest,
            )
        )
        # Compatibility output is replaceable; the immutable snapshot is authority.
        _atomic_write(review.generated_path.parent / "parse-rules.json", raw)
        return self._mint_existing(committed)

    def _create_from_bytes(
        self,
        run_id: str,
        raw: bytes,
        *,
        sample_pages: list[str],
    ) -> ParseRuleReview:
        run_dir = self._run_directory(run_id)
        generated_path = run_dir / "parse-rules.generated.json"
        _atomic_write(generated_path, raw)
        validation_error = _validation_error(raw)
        review_id = f"review-{uuid4().hex}"
        row = self.store.create_parse_rule_review(
            review_id=review_id,
            run_id=run_id,
            generated_path=generated_path,
            sample_pages=tuple(sample_pages),
            validation_error=validation_error,
        )
        return _review_from_row(row)

    def _run_directory(self, run_id: str) -> Path:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("invalid run ID for managed parse-rule storage")
        path = self.data_dir / "runs" / run_id / "parse-rules"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _mint_existing(review: ParseRuleReview) -> ApprovedParseRules:
        if not (
            review.approved_snapshot_path
            and review.approval_digest
            and review.approved_at
        ):
            raise ValueError("approved review has incomplete provenance")
        return mint_approved_parse_rules(
            run_id=review.run_id,
            review_id=review.review_id,
            snapshot_path=review.approved_snapshot_path,
            sha256=review.approval_digest,
            approved_at=review.approved_at,
        )


def _validation_error(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw)
        validate_marker_cheatsheet(payload)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        return str(exc)[:500]
    return None


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("content-addressed approval snapshot collision")
        return
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("content-addressed approval snapshot collision")
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _review_from_row(row: dict[str, object]) -> ParseRuleReview:
    draft = row.get("draft_path")
    approved = row.get("approved_snapshot_path")
    approved_at = row.get("approved_at")
    return ParseRuleReview(
        review_id=str(row["review_id"]),
        run_id=str(row["run_id"]),
        version=int(row["version"]),
        status=ReviewStatus(str(row["status"])),
        generated_path=Path(str(row["generated_path"])),
        draft_path=Path(str(draft)) if draft else None,
        approved_snapshot_path=Path(str(approved)) if approved else None,
        approval_digest=str(row["approval_digest"])
        if row.get("approval_digest")
        else None,
        validation_error=str(row["validation_error"])
        if row.get("validation_error")
        else None,
        sample_pages=tuple(json.loads(str(row["sample_pages_json"]))),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        approved_at=datetime.fromisoformat(str(approved_at)) if approved_at else None,
    )
