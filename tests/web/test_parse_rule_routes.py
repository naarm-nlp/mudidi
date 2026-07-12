"""HTTP tests for the human parse-rule editor and approval action."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.parse_rules import ReviewStatus
from mudidi.web.runs import RunStatus


def _review_app(tmp_path: Path) -> tuple[object, TestClient, str]:
    app = create_app(data_dir=tmp_path)
    run_id = "review-route-run"
    store = app.state.run_store
    store.create_run(run_id)
    store.transition(run_id, RunStatus.VALIDATED)
    store.transition(run_id, RunStatus.QUEUED)
    store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)
    app.state.parse_rule_reviews.create_generated(
        run_id,
        {
            "dictionary_name": "Example dictionary",
            "markers": [
                {"marker": "lx", "description": "Headword"},
                {"marker": "ge", "description": "English gloss"},
            ],
            "rules": ["Begin each entry with a headword."],
            "abbreviations": {"n.": "noun"},
        },
        sample_pages=["1", "3"],
    )
    return app, TestClient(app), run_id


def test_parse_rule_editor_renders_complete_schema(tmp_path: Path) -> None:
    _app, client, run_id = _review_app(tmp_path)

    response = client.get(f"/runs/{run_id}/parse-rules")

    assert response.status_code == 200
    assert "Review parse rules" in response.text
    assert "Example dictionary" in response.text
    assert 'value="lx"' in response.text
    assert "Begin each entry with a headword." in response.text
    assert 'value="n."' in response.text
    assert "Approve and continue" in response.text


def test_structured_editor_saves_normalized_draft(tmp_path: Path) -> None:
    app, client, run_id = _review_app(tmp_path)

    response = client.post(
        f"/runs/{run_id}/parse-rules/draft",
        data=[
            ("dictionary_name", "Edited dictionary"),
            ("marker_code", "\\lx"),
            ("marker_description", "Lexeme"),
            ("marker_code", "ge"),
            ("marker_description", "Gloss"),
            ("rule", "Keep printed order."),
            ("abbreviation_key", "v."),
            ("abbreviation_value", "verb"),
        ],
    )

    assert response.status_code == 200
    assert "Draft saved" in response.text
    payload = app.state.parse_rule_reviews.load_editable_payload(run_id)
    assert payload["dictionary_name"] == "Edited dictionary"
    assert payload["markers"][0]["marker"] == "lx"


def test_invalid_structured_draft_remains_in_review(tmp_path: Path) -> None:
    app, client, run_id = _review_app(tmp_path)

    response = client.post(
        f"/runs/{run_id}/parse-rules/draft",
        data=[
            ("dictionary_name", "Example"),
            ("marker_code", "lx"),
            ("marker_description", "Headword"),
            ("marker_code", "\\lx"),
            ("marker_description", "Duplicate"),
        ],
    )

    assert response.status_code == 422
    assert "duplicate marker" in response.text
    assert app.state.run_store.get_run(run_id).status is RunStatus.AWAITING_PARSE_RULES_REVIEW


def test_explicit_approval_redirects_with_recorded_digest(tmp_path: Path) -> None:
    app, client, run_id = _review_app(tmp_path)

    response = client.post(
        f"/runs/{run_id}/parse-rules/approve",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{run_id}"
    review = app.state.parse_rule_reviews.get(run_id)
    assert review.status is ReviewStatus.APPROVED
    assert review.approval_digest is not None
    assert app.state.run_store.get_run(run_id).status is RunStatus.RUNNING_STAGE2
