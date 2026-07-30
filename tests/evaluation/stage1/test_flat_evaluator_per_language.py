"""Wiring tests: ``FlatStage1Evaluator(per_language_script=...)``.

Confirms the CLI-facing ``--per-language-script`` flag (see
``mudidi.cli.evaluate_stage1``) is threaded correctly into per-page evaluation:
flag on + a co-located gold ``*_lang.json`` populates ``Stage1Metrics.per_language``;
flag on + no ``*_lang.json`` (most dictionaries today) leaves it ``None`` without
failing the page; flag off never attempts per-language-script eval at all.

Note: importing ``mudidi.evaluation.stage1.flat_evaluator`` pulls in
``mudidi.evaluation.stage1.alignment`` -> ``quick_match`` -> ``scipy.optimize``.
On environments where the prebuilt scipy PROPACK extension fails to load (a
known issue on some macOS/arm64 setups -- the exact reason
``per_language_quality.py`` was written scipy-free), this whole module is
skipped rather than aborting the test session, since it's an environment
issue unrelated to the per-language-script wiring under test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

flat_evaluator = pytest.importorskip(
    "mudidi.evaluation.stage1.flat_evaluator",
    reason="requires a working scipy.optimize (PROPACK extension) build",
    exc_type=ImportError,
)
FlatStage1Evaluator = flat_evaluator.FlatStage1Evaluator

from mudidi.schemas.language_span import (  # noqa: E402
    SPACE,
    LanguageSpan,
    PageLanguageMap,
    sha256_of,
)

GOLD_TEXT = "字 word\n"


def _lang_map_for_gold_text(raw: str) -> PageLanguageMap:
    spans = [
        LanguageSpan(start=0, end=1, language="Japanese-Kanji"),  # "字"
        LanguageSpan(start=1, end=2, language=SPACE),
        LanguageSpan(start=2, end=6, language="English-Latin"),  # "word"
        LanguageSpan(start=6, end=7, language=SPACE),
    ]
    return PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw),
        labeled_via="heuristic",
        spans=spans,
    )


def _write_dataset_page(tmp_path: Path, *, with_lang_map: bool, pred_text: str = GOLD_TEXT):
    """Lay out ``<stem>/<stem>_stage1_GOLD_flat.txt`` (+ optional ``_lang.json``)."""
    page_dir = tmp_path / "page_1"
    page_dir.mkdir()
    gold_path = page_dir / "page_1_stage1_GOLD_flat.txt"
    pred_path = page_dir / "page_1_stage1.txt"
    gold_path.write_text(GOLD_TEXT, encoding="utf-8")
    pred_path.write_text(pred_text, encoding="utf-8")
    if with_lang_map:
        map_path = page_dir / "page_1_lang.json"
        _lang_map_for_gold_text(gold_path.read_text(encoding="utf-8")).save(map_path)
    return pred_path, gold_path


def test_per_language_script_flag_on_with_lang_map_populates_metrics(tmp_path):
    pred_path, gold_path = _write_dataset_page(tmp_path, with_lang_map=True)
    evaluator = FlatStage1Evaluator(
        character_alignment="collapsed", per_language_script=True
    )

    metrics = evaluator.evaluate(pred_path, gold_path, page_id="page_1")

    assert metrics.per_language is not None
    assert set(metrics.per_language.per_language) == {
        "Japanese-Kanji",
        "English-Latin",
    }
    assert metrics.per_language.blended_grapheme_edits == 0


def test_per_language_script_flag_on_without_lang_map_stays_none(tmp_path):
    pred_path, gold_path = _write_dataset_page(tmp_path, with_lang_map=False)
    evaluator = FlatStage1Evaluator(
        character_alignment="collapsed", per_language_script=True
    )

    metrics = evaluator.evaluate(pred_path, gold_path, page_id="page_1")

    # Page still evaluates normally -- whole-page metrics unaffected.
    assert metrics.per_language is None
    assert metrics.character_quality.total_graphemes_gold > 0


def test_per_language_script_flag_off_never_populates(tmp_path):
    pred_path, gold_path = _write_dataset_page(tmp_path, with_lang_map=True)
    evaluator = FlatStage1Evaluator(character_alignment="collapsed")  # flag off (default)

    metrics = evaluator.evaluate(pred_path, gold_path, page_id="page_1")

    assert metrics.per_language is None


def test_per_language_script_flag_on_invalid_span_map_logs_and_skips(tmp_path):
    pred_path, gold_path = _write_dataset_page(tmp_path, with_lang_map=False)
    # An invalid span map: sha bound to different text than the actual gold file.
    map_path = gold_path.parent / "page_1_lang.json"
    _lang_map_for_gold_text("tampered text, not the real gold").save(map_path)
    evaluator = FlatStage1Evaluator(
        character_alignment="collapsed", per_language_script=True
    )

    metrics = evaluator.evaluate(pred_path, gold_path, page_id="page_1")

    # Invalid span map is logged-and-skipped, not raised -- page still evaluates.
    assert metrics.per_language is None
    assert metrics.character_quality.total_graphemes_gold > 0
