"""Behavioral tests for web-safe execution contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.execution.approval import (
    ApprovalMismatchError,
    ApprovedParseRules,
    load_approved_parse_rules,
    mint_approved_parse_rules,
)
from mudidi.execution.cancellation import CancellationToken, ExecutionCancelled
from mudidi.execution.events import PageCompleted, StageStarted, parse_execution_event
from mudidi.schemas.field_cheatsheet import validate_marker_cheatsheet


def _rules_payload() -> dict[str, object]:
    return {
        "markers": [
            {"marker": "\\lx", "description": "Headword"},
            {"marker": "ge", "description": "English gloss"},
        ],
        "rules": ["Begin every record with \\lx."],
        "abbreviations": {"n.": "noun"},
    }


def _approved_snapshot(tmp_path: Path) -> tuple[ApprovedParseRules, bytes]:
    raw = json.dumps(_rules_payload(), sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "approved" / f"{digest}.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    approval = mint_approved_parse_rules(
        run_id="run-123",
        review_id="review-2",
        snapshot_path=path,
        sha256=digest,
        approved_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    return approval, raw


def test_events_round_trip_through_discriminated_parser() -> None:
    event = StageStarted(
        run_id="run-123",
        sequence=1,
        stage="stage1",
        occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
    )

    parsed = parse_execution_event(event.model_dump(mode="json"))

    assert parsed == event


def test_page_event_rejects_non_positive_page_number() -> None:
    with pytest.raises(ValidationError):
        PageCompleted(
            run_id="run-123",
            sequence=2,
            stage="stage1",
            page=0,
            occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
        )


def test_cancellation_token_raises_only_after_cancel() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()

    token.cancel()

    with pytest.raises(ExecutionCancelled):
        token.raise_if_cancelled()


def test_marker_validation_normalizes_backslashes() -> None:
    validated = validate_marker_cheatsheet(_rules_payload())

    assert [marker.marker for marker in validated.markers] == ["lx", "ge"]


@pytest.mark.parametrize("marker", ["", "two words", "1lx", "lx!"])
def test_marker_validation_rejects_invalid_codes(marker: str) -> None:
    payload = _rules_payload()
    payload["markers"] = [{"marker": marker, "description": "Bad"}]

    with pytest.raises(ValueError, match="marker"):
        validate_marker_cheatsheet(payload)


def test_marker_validation_rejects_normalized_duplicates() -> None:
    payload = _rules_payload()
    payload["markers"] = [
        {"marker": "lx", "description": "Headword"},
        {"marker": "\\lx", "description": "Duplicate"},
    ]

    with pytest.raises(ValueError, match="duplicate marker"):
        validate_marker_cheatsheet(payload)


def test_approved_rules_load_exact_verified_snapshot(tmp_path: Path) -> None:
    approval, raw = _approved_snapshot(tmp_path)

    loaded = load_approved_parse_rules(approval)

    assert loaded.raw_bytes == raw
    assert loaded.rules.markers[0].marker == "lx"
    assert loaded.sha256 == approval.sha256


def test_approved_rules_reject_tampered_snapshot(tmp_path: Path) -> None:
    approval, _ = _approved_snapshot(tmp_path)
    approval.snapshot_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ApprovalMismatchError, match="digest"):
        load_approved_parse_rules(approval)


def test_approved_rules_require_content_addressed_managed_filename(
    tmp_path: Path,
) -> None:
    raw = json.dumps(_rules_payload()).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "mdf_parsing_guide.json"
    path.write_bytes(raw)

    with pytest.raises(ValueError, match="content-addressed"):
        mint_approved_parse_rules(
            run_id="run-123",
            review_id="review-2",
            snapshot_path=path,
            sha256=digest,
            approved_at=datetime.now(UTC),
        )
