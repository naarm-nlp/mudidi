"""Authorization and exact-byte loading for reviewed Stage 2 parse rules."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mudidi.schemas.field_cheatsheet import (
    DictionaryMarkerCheatsheet,
    validate_marker_cheatsheet,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MINT_SENTINEL = object()


class ApprovalMismatchError(ValueError):
    """Raised when an approval no longer matches its immutable snapshot."""


@dataclass(frozen=True, slots=True)
class ApprovedParseRules:
    """Run-bound capability authorizing exact reviewed rules for Pass 2.

    Instances are created through :func:`mint_approved_parse_rules` after the
    approval row and immutable snapshot have been committed.
    """

    run_id: str
    review_id: str
    snapshot_path: Path
    sha256: str
    approved_at: datetime
    _mint_sentinel: object

    def __post_init__(self) -> None:
        if self._mint_sentinel is not _MINT_SENTINEL:
            raise ValueError("ApprovedParseRules must be minted by the approval service")


@dataclass(frozen=True, slots=True)
class LoadedApprovedParseRules:
    """Verified rule model together with the exact bytes that produced it."""

    approval: ApprovedParseRules
    raw_bytes: bytes
    rules: DictionaryMarkerCheatsheet
    sha256: str


def mint_approved_parse_rules(
    *,
    run_id: str,
    review_id: str,
    snapshot_path: Path,
    sha256: str,
    approved_at: datetime,
) -> ApprovedParseRules:
    """Mint a capability for a committed content-addressed approval snapshot."""

    if not run_id.strip() or not review_id.strip():
        raise ValueError("approval run_id and review_id must not be empty")
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("approval sha256 must be a lowercase SHA-256 digest")
    path = snapshot_path.expanduser().resolve()
    if path.parent.name != "approved" or path.name != f"{sha256}.json":
        raise ValueError(
            "approval snapshot must use a content-addressed approved/<sha256>.json path"
        )
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise ValueError("approved_at must be timezone-aware")
    return ApprovedParseRules(
        run_id=run_id,
        review_id=review_id,
        snapshot_path=path,
        sha256=sha256,
        approved_at=approved_at,
        _mint_sentinel=_MINT_SENTINEL,
    )


def load_approved_parse_rules(
    approval: ApprovedParseRules,
) -> LoadedApprovedParseRules:
    """Read once, authenticate, and validate the exact approved snapshot bytes."""

    try:
        raw_bytes = approval.snapshot_path.read_bytes()
    except OSError as exc:
        raise ApprovalMismatchError(
            f"approved parse-rules snapshot cannot be read: {exc}"
        ) from exc
    actual_digest = hashlib.sha256(raw_bytes).hexdigest()
    if actual_digest != approval.sha256:
        raise ApprovalMismatchError(
            "approved parse-rules snapshot digest does not match its approval"
        )
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalMismatchError("approved parse-rules snapshot is not valid JSON") from exc
    try:
        rules = validate_marker_cheatsheet(payload)
    except ValueError as exc:
        raise ApprovalMismatchError(
            f"approved parse-rules snapshot is not schema-valid: {exc}"
        ) from exc
    return LoadedApprovedParseRules(
        approval=approval,
        raw_bytes=raw_bytes,
        rules=rules,
        sha256=actual_digest,
    )
