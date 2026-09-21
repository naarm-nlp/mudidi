"""Unit tests for per-language Stage 1 evaluation (per_language_quality)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mudidi.evaluation.stage1.per_language_metrics import (
    PageLanguageReport,
    PerLanguageMetrics,
    aggregate,
)
from mudidi.evaluation.stage1.per_language_quality import (
    collapsed_clean_text,
    compute_per_language_quality,
    evaluate_per_language,
    lang_map_path_for_gold,
)
from mudidi.evaluation.stage1.stage1_metrics import Stage1Metrics
from mudidi.evaluation.stage1.stage1_reports import Stage1ReportWriter
from mudidi.schemas.language_span import (
    META,
    SPACE,
    LanguageSpan,
    PageLanguageMap,
    SpanMapError,
    sha256_of,
)

GOLD = "akɔɔtee small crab in wait\namãrɛ the remains of\n"


def _build_map(raw: str) -> PageLanguageMap:
    """Label the first word of each line as the source (Canala), the rest English."""
    spans = []
    index = 0
    length = len(raw)
    line_start = True
    while index < length:
        if raw[index].isspace():
            stop = index
            while stop < length and raw[stop].isspace():
                stop += 1
            spans.append(LanguageSpan(start=index, end=stop, language=SPACE))
            if "\n" in raw[index:stop]:
                line_start = True
            index = stop
        else:
            stop = index
            while stop < length and not raw[stop].isspace():
                stop += 1
            spans.append(
                LanguageSpan(
                    start=index,
                    end=stop,
                    language="Canala" if line_start else "English",
                )
            )
            line_start = False
            index = stop
    return PageLanguageMap(
        dictionary="Canala-English",
        page=12,
        source_text_sha=sha256_of(raw),
        labeled_via="heuristic",
        spans=spans,
    )


def _write_case(tmp_path: Path, gold: str, pred: str):
    gold_path = tmp_path / "page_12_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_12_stage1.txt"
    map_path = tmp_path / "page_12_lang.json"
    gold_path.write_text(gold, encoding="utf-8")
    pred_path.write_text(pred, encoding="utf-8")
    raw = gold_path.read_text(encoding="utf-8")  # sha must match what eval reads
    _build_map(raw).save(map_path)
    return pred_path, gold_path, map_path


def test_consistency_oracle(tmp_path):
    # Arrange: pred corrupts one English word ("small" -> "smxll").
    pred = GOLD.replace("small", "smxll")
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, pred)
    # Act
    report = evaluate_per_language(pred_path, gold_path, map_path, page_id="page_12")
    # Assert: per-language grapheme counts pool to the blended totals.
    total_edits = sum(m.total_grapheme_edits for m in report.per_language.values())
    total_gold = sum(m.total_graphemes_gold for m in report.per_language.values())
    assert total_edits == report.blended_grapheme_edits
    assert total_gold == report.blended_graphemes_gold
    assert report.blended_grapheme_edits > 0  # the corruption registered


def test_english_only_corruption_isolated(tmp_path):
    pred = GOLD.replace("small", "smxll")
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, pred)
    report = evaluate_per_language(pred_path, gold_path, map_path)
    assert report.per_language["English"].total_grapheme_edits > 0
    assert report.per_language["English"].gcer > 0
    assert report.per_language["Canala"].total_grapheme_edits == 0
    assert report.per_language["Canala"].gcer == 0.0


def test_source_only_corruption_isolated(tmp_path):
    pred = GOLD.replace("akɔɔtee", "akxxtee")
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, pred)
    report = evaluate_per_language(pred_path, gold_path, map_path)
    assert report.per_language["Canala"].total_grapheme_edits > 0
    assert report.per_language["English"].total_grapheme_edits == 0


def test_word_error_partition(tmp_path):
    pred = GOLD.replace("small", "smxll")  # one English word substitution
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, pred)
    report = evaluate_per_language(pred_path, gold_path, map_path)
    assert report.per_language["English"].total_word_edits == 1
    assert report.per_language["Canala"].total_word_edits == 0
    total_word_edits = sum(m.total_word_edits for m in report.per_language.values())
    assert total_word_edits == 1


def test_clean_prediction_is_zero(tmp_path):
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, GOLD)
    report = evaluate_per_language(pred_path, gold_path, map_path)
    assert report.blended_grapheme_edits == 0
    for metrics in report.per_language.values():
        assert metrics.gcer == 0.0
        assert metrics.wer == 0.0


def test_gold_words_follow_language_span_boundaries(tmp_path):
    raw_gold = "<b>अळोप</b>(alop)English\n"
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    pred_path.write_text(raw_gold, encoding="utf-8")
    PageLanguageMap(
        dictionary="Gojri-English",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=[
            LanguageSpan(start=0, end=11, language="Gojri-Devanagari"),
            LanguageSpan(start=11, end=17, language="Gojri-Latin"),
            LanguageSpan(start=17, end=24, language="English-Latin"),
            LanguageSpan(start=24, end=25, language=SPACE),
        ],
    ).save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path)

    assert report.per_language["Gojri-Devanagari"].total_words_gold == 1
    assert report.per_language["Gojri-Latin"].total_words_gold == 1
    assert report.per_language["English-Latin"].total_words_gold == 1


def test_no_whitespace_script_spans_are_independent_gold_words(tmp_path):
    raw_gold = "字カナword\n"
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    pred_path.write_text(raw_gold, encoding="utf-8")
    PageLanguageMap(
        dictionary="Japanese-English",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=[
            LanguageSpan(start=0, end=1, language="Japanese-Kanji"),
            LanguageSpan(start=1, end=3, language="Japanese-Katakana"),
            LanguageSpan(start=3, end=7, language="English-Latin"),
            LanguageSpan(start=7, end=8, language=SPACE),
        ],
    ).save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path)

    assert report.per_language["Japanese-Kanji"].total_words_gold == 1
    assert report.per_language["Japanese-Katakana"].total_words_gold == 1
    assert report.per_language["English-Latin"].total_words_gold == 1


def test_span_word_edit_is_attributed_to_own_language(tmp_path):
    raw_gold = "<b>अळोप</b>(alop)English\n"
    pred_text = "<b>अलो</b>(alop)English\n"
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    pred_path.write_text(pred_text, encoding="utf-8")
    PageLanguageMap(
        dictionary="Gojri-English",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=[
            LanguageSpan(start=0, end=11, language="Gojri-Devanagari"),
            LanguageSpan(start=11, end=17, language="Gojri-Latin"),
            LanguageSpan(start=17, end=24, language="English-Latin"),
            LanguageSpan(start=24, end=25, language=SPACE),
        ],
    ).save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path)

    assert report.per_language["Gojri-Devanagari"].total_word_edits == 1
    assert report.per_language["Gojri-Latin"].total_word_edits == 0
    assert report.per_language["English-Latin"].total_word_edits == 0


def test_empty_prediction_oracle_holds(tmp_path):
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, "")
    report = evaluate_per_language(pred_path, gold_path, map_path)
    total_edits = sum(m.total_grapheme_edits for m in report.per_language.values())
    assert total_edits == report.blended_grapheme_edits
    assert report.blended_grapheme_edits > 0  # everything deleted


def test_sha_mismatch_refuses(tmp_path):
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, GOLD)
    gold_path.write_text(
        GOLD + "tampered\n", encoding="utf-8"
    )  # gold changed after labelling
    with pytest.raises(SpanMapError):
        evaluate_per_language(pred_path, gold_path, map_path)


def test_compute_core_matches_file_wrapper(tmp_path):
    # The strings-core entry should match the file wrapper (the path the flat
    # evaluator uses when feeding pair.gold.text / pair.pred.text).
    pred = GOLD.replace("small", "smxll")
    pred_path, gold_path, map_path = _write_case(tmp_path, GOLD, pred)
    raw_gold = gold_path.read_text(encoding="utf-8")
    lang_map = PageLanguageMap.load(map_path)
    core = compute_per_language_quality(
        collapsed_clean_text(gold_path),
        collapsed_clean_text(pred_path),
        raw_gold,
        lang_map,
        page_id="page_12",
    )
    wrapper = evaluate_per_language(pred_path, gold_path, map_path, page_id="page_12")
    assert core.blended_grapheme_edits == wrapper.blended_grapheme_edits
    assert (
        core.per_language["English"].total_grapheme_edits
        == wrapper.per_language["English"].total_grapheme_edits
    )


def test_aggregate_pools_counts():
    def _report(edits, gold):
        return PageLanguageReport(
            per_language={
                "English": PerLanguageMetrics(
                    language="English",
                    total_grapheme_edits=edits,
                    total_graphemes_gold=gold,
                )
            }
        )

    pooled = aggregate([_report(2, 10), _report(3, 30)])
    assert pooled["English"].total_grapheme_edits == 5
    assert pooled["English"].total_graphemes_gold == 40
    assert pooled["English"].gcer == 5 / 40


def test_per_language_script_summary_omits_total_columns_but_detailed_keeps_them(
    tmp_path,
):
    metrics = Stage1Metrics(
        page_id="Test-English/page_1",
        per_language=PageLanguageReport(
            per_language={
                "English-Latin": PerLanguageMetrics(
                    language="English-Latin",
                    total_grapheme_edits=2,
                    total_graphemes_gold=10,
                    total_graphemes_pred=11,
                    total_word_edits=1,
                    total_words_gold=5,
                )
            }
        ),
    )
    other_language_metrics = Stage1Metrics(
        page_id="Other-English/page_1",
        per_language=PageLanguageReport(
            per_language={
                "English-Latin": PerLanguageMetrics(
                    language="English-Latin",
                    total_grapheme_edits=4,
                    total_graphemes_gold=10,
                    total_graphemes_pred=10,
                    total_word_edits=2,
                    total_words_gold=5,
                )
            }
        ),
    )
    writer = Stage1ReportWriter(include_per_language_script=True)
    summary_path = tmp_path / "summary.csv"
    detailed_path = tmp_path / "detailed.csv"

    writer.generate_per_language_script_summary_csv(
        {"exp": [metrics, other_language_metrics]}, summary_path
    )
    writer.generate_per_language_script_detailed_csv(
        {"exp": [metrics, other_language_metrics]}, detailed_path
    )

    with summary_path.open(encoding="utf-8", newline="") as handle:
        summary_reader = csv.DictReader(handle)
        summary_cols = summary_reader.fieldnames or []
        summary_rows = list(summary_reader)
    with detailed_path.open(encoding="utf-8", newline="") as handle:
        detailed_reader = csv.DictReader(handle)
        detailed_cols = detailed_reader.fieldnames or []
        detailed_rows = list(detailed_reader)

    assert not any(col.startswith("total_") for col in summary_cols)
    assert "language" in summary_cols
    assert "gold_word_count" in summary_cols
    assert "gold_grapheme_count" in summary_cols
    assert "gold_word_count" in detailed_cols
    assert "gold_grapheme_count" in detailed_cols
    assert "language" in detailed_cols
    assert "page" in detailed_cols
    assert "page_id" not in detailed_cols
    assert "page" not in summary_cols
    assert {(row["language"], row["language_script"]) for row in summary_rows} == {
        ("Other-English", "English-Latin"),
        ("Test-English", "English-Latin"),
    }
    assert {(row["language"], row["language_script"]) for row in detailed_rows} == {
        ("Other-English", "English-Latin"),
        ("Test-English", "English-Latin"),
    }
    assert {row["page"] for row in detailed_rows} == {"page_1"}


# ---------------------------------------------------------------------------
# lang_map_path_for_gold
# ---------------------------------------------------------------------------


def test_lang_map_path_for_gold_follows_dataset_convention():
    gold_path = Path(
        "dataset/MUDIDI/dictionaries/Japanese-English/Stage 1 Gold OCR/"
        "page_137/page_137_stage1_GOLD_flat.txt"
    )
    expected = gold_path.parent / "page_137_lang.json"
    assert lang_map_path_for_gold(gold_path) == expected


def test_lang_map_path_for_gold_accepts_str():
    assert lang_map_path_for_gold("a/b/c_gold.txt") == Path("a/b/b_lang.json")


# ---------------------------------------------------------------------------
# Punctuation / whitespace: physically stripped from scoring (symmetric).
# ---------------------------------------------------------------------------


def _build_two_span_map(
    raw: str, boundary: int, first: str, second: str
) -> PageLanguageMap:
    """Label ``raw[:boundary]`` as *first* and ``raw[boundary:]`` as *second*."""
    spans = [LanguageSpan(start=0, end=boundary, language=first)]
    if boundary < len(raw):
        spans.append(LanguageSpan(start=boundary, end=len(raw), language=second))
    return PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw),
        labeled_via="heuristic",
        spans=spans,
    )


def test_punctuation_excluded_does_not_affect_gcer_or_wer():
    # Arrange: gold has punctuation attached to each language-script chunk;
    # pred has none of it -- a real OCR normalization difference.
    raw_gold = "字, word.\n"
    gold_clean = "字, word."  # normalize_line_text output (no tags to strip)
    pred_clean = "字 word"  # pred drops the punctuation entirely
    lang_map = _build_two_span_map(raw_gold, 2, "Japanese-Kanji", "English-Latin")
    # Act
    report = compute_per_language_quality(
        gold_clean, pred_clean, raw_gold, lang_map, page_id="punct"
    )
    # Assert: punctuation removed symmetrically -> zero edits either side.
    assert report.per_language["Japanese-Kanji"].gcer == 0.0
    assert report.per_language["Japanese-Kanji"].wer == 0.0
    assert report.per_language["English-Latin"].gcer == 0.0
    assert report.per_language["English-Latin"].wer == 0.0
    assert report.blended_grapheme_edits == 0


def test_whitespace_not_counted_as_graphemes():
    # A page with only whitespace-separated real content and a clean prediction
    # should have grapheme totals matching content length only (no space graphemes).
    raw_gold = "ab cd\n"
    gold_clean = "ab cd"
    pred_clean = "ab cd"
    lang_map = _build_two_span_map(raw_gold, 3, "English-Latin", "English-Latin")
    report = compute_per_language_quality(
        gold_clean, pred_clean, raw_gold, lang_map, page_id="ws"
    )
    # 4 letters total ("ab" + "cd"); the space and newline contribute nothing.
    assert report.per_language["English-Latin"].total_graphemes_gold == 4
    assert "space" not in report.per_language


# ---------------------------------------------------------------------------
# SPACE/META: excluded from the final report, not physically stripped pre-align.
# ---------------------------------------------------------------------------


def test_space_and_meta_buckets_dropped_from_report():
    raw_gold = "[NK] 字 word\n"
    gold_clean = "[NK] 字 word"
    pred_clean = "[NK] 字 word"  # pred correctly transcribes the meta marker too
    spans = [
        LanguageSpan(start=0, end=4, language=META),  # "[NK]"
        LanguageSpan(start=4, end=5, language=SPACE),
        LanguageSpan(start=5, end=6, language="Japanese-Kanji"),  # "字"
        LanguageSpan(start=6, end=7, language=SPACE),
        LanguageSpan(start=7, end=11, language="English-Latin"),  # "word"
        LanguageSpan(start=11, end=12, language=SPACE),
    ]
    lang_map = PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=spans,
    )
    report = compute_per_language_quality(
        gold_clean, pred_clean, raw_gold, lang_map, page_id="meta"
    )
    # Assert: no reserved buckets ever surface in the report.
    assert SPACE not in report.per_language
    assert META not in report.per_language
    assert set(report.per_language) == {"Japanese-Kanji", "English-Latin"}
    # Assert: blended totals are the sum of the real buckets only (meta's "NK"
    # graphemes -- punctuation brackets are stripped, "NK" survives -- excluded).
    assert report.blended_graphemes_gold == (
        report.per_language["Japanese-Kanji"].total_graphemes_gold
        + report.per_language["English-Latin"].total_graphemes_gold
    )
    assert report.blended_grapheme_edits == 0


def test_meta_corruption_does_not_leak_into_neighboring_buckets():
    # Arrange: pred corrupts the meta marker content ("NK" -> "NX") but
    # transcribes the real-language content perfectly.
    raw_gold = "[NK] 字 word\n"
    gold_clean = "[NK] 字 word"
    pred_clean = "[NX] 字 word"
    spans = [
        LanguageSpan(start=0, end=4, language=META),
        LanguageSpan(start=4, end=5, language=SPACE),
        LanguageSpan(start=5, end=6, language="Japanese-Kanji"),
        LanguageSpan(start=6, end=7, language=SPACE),
        LanguageSpan(start=7, end=11, language="English-Latin"),
        LanguageSpan(start=11, end=12, language=SPACE),
    ]
    lang_map = PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=spans,
    )
    report = compute_per_language_quality(
        gold_clean, pred_clean, raw_gold, lang_map, page_id="meta-corrupt"
    )
    # Assert: the meta corruption is invisible -- dropped bucket, not attributed
    # to Kanji/Latin, and not counted in the blended (real-bucket-only) totals.
    assert "meta" not in report.per_language
    assert report.per_language["Japanese-Kanji"].total_grapheme_edits == 0
    assert report.per_language["English-Latin"].total_grapheme_edits == 0
    assert report.blended_grapheme_edits == 0


# ---------------------------------------------------------------------------
# Markup tags: already excluded by construction (collapsed_clean_text strips
# them before scoring) -- this is a lock-in regression test, not new logic.
# ---------------------------------------------------------------------------


def _tagged_kanji_word_lang_map(raw: str) -> PageLanguageMap:
    """``<b>字</b> <i>word</i>\\n`` -- Kanji tagged span, space, Latin tagged span."""
    spans = [
        LanguageSpan(start=0, end=8, language="Japanese-Kanji"),  # "<b>字</b>"
        LanguageSpan(start=8, end=9, language=SPACE),  # " "
        LanguageSpan(
            start=9, end=len(raw), language="English-Latin"
        ),  # "<i>word</i>\n"
    ]
    return PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw),
        labeled_via="heuristic",
        spans=spans,
    )


def test_markup_tags_excluded_and_do_not_shift_attribution(tmp_path):
    # Arrange: raw gold has <b>/<i> tags physically present (as the flat gold
    # files do); span map labels the tagged span same as its content.
    raw_gold = "<b>字</b> <i>word</i>\n"
    lang_map = _tagged_kanji_word_lang_map(raw_gold)
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    # Pred reproduces the same typography -- collapsed_clean_text strips tags
    # from both sides before any scoring happens.
    pred_path.write_text("<b>字</b> <i>word</i>\n", encoding="utf-8")
    lang_map.save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path, page_id="markup")

    # Assert: exactly the tag-stripped content graphemes are counted -- no tag
    # characters ("<", "b", ">", "/", "i") leaked into either bucket.
    assert report.per_language["Japanese-Kanji"].total_graphemes_gold == 1  # "字"
    assert report.per_language["English-Latin"].total_graphemes_gold == 4  # "word"
    assert report.per_language["Japanese-Kanji"].total_grapheme_edits == 0
    assert report.per_language["English-Latin"].total_grapheme_edits == 0
    assert report.blended_grapheme_edits == 0


def test_markup_tag_boundary_corruption_isolated_to_correct_bucket(tmp_path):
    # A corruption right after a stripped closing tag must attribute to the
    # *following* content's language, not leak backward across the tag.
    raw_gold = "<b>字</b> <i>word</i>\n"
    lang_map = _tagged_kanji_word_lang_map(raw_gold)
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    pred_path.write_text("<b>字</b> <i>wxrd</i>\n", encoding="utf-8")  # corrupt "word"
    lang_map.save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path, page_id="markup-2")

    assert report.per_language["Japanese-Kanji"].total_grapheme_edits == 0
    assert report.per_language["English-Latin"].total_grapheme_edits > 0


# ---------------------------------------------------------------------------
# Compound Language-Script tags (e.g. "Japanese-Kanji", "English-Latin") --
# nothing assumes plain language names.
# ---------------------------------------------------------------------------


def test_compound_language_script_tags_isolated(tmp_path):
    raw_gold = "字 カナ word\n"
    spans = [
        LanguageSpan(start=0, end=1, language="Japanese-Kanji"),  # "字"
        LanguageSpan(start=1, end=2, language=SPACE),
        LanguageSpan(start=2, end=4, language="Japanese-Katakana"),  # "カナ"
        LanguageSpan(start=4, end=5, language=SPACE),
        LanguageSpan(start=5, end=9, language="English-Latin"),  # "word"
        LanguageSpan(start=9, end=10, language=SPACE),
    ]
    lang_map = PageLanguageMap(
        dictionary="Test-Test",
        page=1,
        source_text_sha=sha256_of(raw_gold),
        labeled_via="heuristic",
        spans=spans,
    )
    gold_path = tmp_path / "page_1_stage1_GOLD_flat.txt"
    pred_path = tmp_path / "page_1_stage1.txt"
    map_path = tmp_path / "page_1_lang.json"
    gold_path.write_text(raw_gold, encoding="utf-8")
    # Corrupt only the Katakana content.
    pred_path.write_text("字 xナ word\n", encoding="utf-8")
    lang_map.save(map_path)

    report = evaluate_per_language(pred_path, gold_path, map_path, page_id="compound")

    assert set(report.per_language) == {
        "Japanese-Kanji",
        "Japanese-Katakana",
        "English-Latin",
    }
    assert report.per_language["Japanese-Katakana"].total_grapheme_edits > 0
    assert report.per_language["Japanese-Kanji"].total_grapheme_edits == 0
    assert report.per_language["English-Latin"].total_grapheme_edits == 0
