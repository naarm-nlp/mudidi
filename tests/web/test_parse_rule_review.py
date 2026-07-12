"""Tests for the mandatory human parse-rule review checkpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mudidi.execution.approval import load_approved_parse_rules
from mudidi.web.parse_rules import ParseRuleReviewService, ReviewStatus
from mudidi.web.runs import RunStatus, RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "mudidi-web.sqlite3")


@pytest.fixture
def service(store: RunStore, tmp_path: Path) -> ParseRuleReviewService:
    return ParseRuleReviewService(store=store, data_dir=tmp_path / "app-data")


def _discovering_run(store: RunStore, run_id: str = "run-1") -> None:
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)


def _rules(marker: str = "lx") -> dict[str, object]:
    return {
        "dictionary_name": "Example dictionary",
        "markers": [
            {"marker": marker, "description": "Headword"},
            {"marker": "ge", "description": "English gloss"},
        ],
        "rules": ["Begin each entry with the headword."],
        "abbreviations": {"n.": "noun"},
    }


def test_generated_rules_enter_durable_review_state(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)

    review = service.create_generated("run-1", _rules(), sample_pages=["1", "3"])

    assert review.status is ReviewStatus.AWAITING_REVIEW
    assert review.generated_path.name == "parse-rules.generated.json"
    assert review.generated_path.exists()
    assert review.sample_pages == ("1", "3")
    assert store.get_run("run-1").status is RunStatus.AWAITING_PARSE_RULES_REVIEW


def test_invalid_generated_rules_still_enter_review_for_repair(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)

    review = service.create_generated(
        "run-1",
        {"dictionary_name": "Broken", "markers": [{"marker": "", "description": ""}]},
        sample_pages=[],
    )

    assert review.status is ReviewStatus.AWAITING_REVIEW
    assert review.validation_error is not None
    assert store.get_run("run-1").status is RunStatus.AWAITING_PARSE_RULES_REVIEW


def test_save_draft_validates_and_normalizes_markers(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)
    service.create_generated("run-1", _rules(), sample_pages=[])

    review = service.save_draft("run-1", _rules("\\lx"))

    saved = json.loads(review.draft_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert saved["markers"][0]["marker"] == "lx"
    assert review.validation_error is None


def test_duplicate_normalized_marker_is_rejected_without_losing_valid_draft(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)
    service.create_generated("run-1", _rules(), sample_pages=[])
    valid = service.save_draft("run-1", _rules())
    original = valid.draft_path.read_bytes()  # type: ignore[union-attr]
    invalid = _rules()
    invalid["markers"] = [
        {"marker": "lx", "description": "Headword"},
        {"marker": "\\lx", "description": "Duplicate"},
    ]

    with pytest.raises(ValueError, match="duplicate marker"):
        service.save_draft("run-1", invalid)

    assert valid.draft_path.read_bytes() == original  # type: ignore[union-attr]


def test_approval_mints_exact_content_addressed_capability(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)
    service.create_generated("run-1", _rules(), sample_pages=[])
    service.save_draft("run-1", _rules("\\lx"))

    approval = service.approve("run-1")
    loaded = load_approved_parse_rules(approval)
    review = service.get("run-1")

    assert approval.snapshot_path.name == f"{approval.sha256}.json"
    assert approval.snapshot_path.parent.name == "approved"
    assert loaded.rules.markers[0].marker == "lx"
    assert review.status is ReviewStatus.APPROVED
    assert review.approval_digest == approval.sha256
    assert store.get_run("run-1").status is RunStatus.RUNNING_STAGE2


def test_same_content_double_approval_is_idempotent(
    store: RunStore,
    service: ParseRuleReviewService,
) -> None:
    _discovering_run(store)
    service.create_generated("run-1", _rules(), sample_pages=[])

    first = service.approve("run-1")
    second = service.approve("run-1")

    assert second.sha256 == first.sha256
    assert second.review_id == first.review_id
    assert list(first.snapshot_path.parent.glob("*.json")) == [first.snapshot_path]


def test_external_rules_are_copied_and_never_approved_implicitly(
    store: RunStore,
    service: ParseRuleReviewService,
    tmp_path: Path,
) -> None:
    _discovering_run(store)
    external = tmp_path / "external-rules.json"
    external.write_text(json.dumps(_rules()), encoding="utf-8")

    review = service.import_external("run-1", external)

    assert review.status is ReviewStatus.AWAITING_REVIEW
    assert review.generated_path != external
    assert review.generated_path.read_bytes() == external.read_bytes()
    assert store.get_run("run-1").status is RunStatus.AWAITING_PARSE_RULES_REVIEW
