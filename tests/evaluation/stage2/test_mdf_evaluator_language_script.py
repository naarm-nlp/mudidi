import json
import csv
from pathlib import Path

from mudidi.evaluation.stage2.mdf_evaluator import MdfEvaluator


def _write_page(
    tmp_path: Path,
    *,
    gold: str,
    pred: str,
    projection_fields: list[dict],
) -> tuple[Path, Path]:
    gold_dir = tmp_path / "gold" / "page_1"
    pred_dir = tmp_path / "pred" / "page_1"
    gold_dir.mkdir(parents=True)
    pred_dir.mkdir(parents=True)
    gold_path = gold_dir / "page_1.mdf.txt"
    pred_path = pred_dir / "page_1.mdf.txt"
    gold_path.write_text(gold, encoding="utf-8")
    pred_path.write_text(pred, encoding="utf-8")
    projection_path = gold_dir / "page_1_mdf_lang_projection.json"
    projection_path.write_text(
        json.dumps(
            {
                "dictionary": "Test-English",
                "page": 1,
                "page_id": "page_1",
                "projection_version": "test",
                "fields": projection_fields,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pred_path, gold_path


def test_stage2_evaluator_reports_language_script_quality_for_values(
    tmp_path: Path,
) -> None:
    pred_path, gold_path = _write_page(
        tmp_path,
        gold="\\lx λόγος\n\\ge word\n",
        pred="\\lx λόγος\n\\ge wurd\n",
        projection_fields=[
            {"line_index": 0, "primary_language": "Greek-Greek"},
            {"line_index": 1, "primary_language": "English-Latin"},
        ],
    )

    metrics = MdfEvaluator().evaluate(pred_path, gold_path, page_id="page_1")

    assert set(metrics.language_script_quality) == {"English-Latin", "Greek-Greek"}
    assert metrics.language_script_quality["Greek-Greek"].gcer == 0.0
    assert metrics.language_script_quality["English-Latin"].total_grapheme_edits == 1
    assert metrics.language_script_quality["English-Latin"].total_graphemes_gold == 4


def test_stage2_language_script_quality_counts_missing_gold_line_as_deletion(
    tmp_path: Path,
) -> None:
    pred_path, gold_path = _write_page(
        tmp_path,
        gold="\\lx λόγος\n\\ge word\n",
        pred="\\lx λόγος\n",
        projection_fields=[
            {"line_index": 0, "primary_language": "Greek-Greek"},
            {"line_index": 1, "primary_language": "English-Latin"},
        ],
    )

    metrics = MdfEvaluator().evaluate(pred_path, gold_path, page_id="page_1")

    english = metrics.language_script_quality["English-Latin"]
    assert english.total_grapheme_edits == 4
    assert english.total_graphemes_gold == 4
    assert english.gcer == 1.0


def test_stage2_language_script_quality_skips_meta_projection(tmp_path: Path) -> None:
    pred_path, gold_path = _write_page(
        tmp_path,
        gold="\\sn 1\n\\lx λόγος\n",
        pred="\\sn 2\n\\lx λόγος\n",
        projection_fields=[
            {"line_index": 0, "primary_language": "meta"},
            {"line_index": 1, "primary_language": "Greek-Greek"},
        ],
    )

    metrics = MdfEvaluator().evaluate(pred_path, gold_path, page_id="page_1")

    assert set(metrics.language_script_quality) == {"Greek-Greek"}
    assert metrics.language_script_quality["Greek-Greek"].gcer == 0.0


def test_stage2_language_script_csvs_are_long_format(tmp_path: Path) -> None:
    pred_path, gold_path = _write_page(
        tmp_path,
        gold="\\lx λόγος\n\\ge word\n",
        pred="\\lx λόγος\n\\ge wurd\n",
        projection_fields=[
            {"line_index": 0, "primary_language": "Greek-Greek"},
            {"line_index": 1, "primary_language": "English-Latin"},
        ],
    )
    evaluator = MdfEvaluator()
    metrics = evaluator.evaluate(
        pred_path,
        gold_path,
        page_id="Greek-English/page_1",
    )
    results_by_exp = {"exp": [metrics]}
    overall_path = tmp_path / "stage2_mdf_eval_summary.csv"
    detailed_path = tmp_path / "stage2_mdf_eval_per_language_script_detailed.csv"
    summary_path = tmp_path / "stage2_mdf_eval_per_language_script_summary.csv"

    evaluator.generate_summary_csv(results_by_exp, overall_path)
    evaluator.generate_per_language_script_detailed_csv(results_by_exp, detailed_path)
    evaluator.generate_per_language_script_summary_csv(results_by_exp, summary_path)

    with overall_path.open(encoding="utf-8", newline="") as handle:
        overall_cols = csv.DictReader(handle).fieldnames or []
    with detailed_path.open(encoding="utf-8", newline="") as handle:
        detailed_reader = csv.DictReader(handle)
        detailed_cols = detailed_reader.fieldnames or []
        detailed_rows = list(detailed_reader)
    with summary_path.open(encoding="utf-8", newline="") as handle:
        summary_reader = csv.DictReader(handle)
        summary_cols = summary_reader.fieldnames or []
        summary_rows = list(summary_reader)

    assert not any(col.startswith("LangScript_") for col in overall_cols)
    assert overall_cols[:3] == ["experiment", "language", "page"]
    assert "page_id" not in overall_cols
    assert "gold_grapheme_count" in detailed_cols
    assert "gold_word_count" in detailed_cols
    assert "gold_grapheme_count" in summary_cols
    assert "gold_word_count" in summary_cols
    assert detailed_cols[:4] == ["experiment", "language", "page", "language_script"]
    assert summary_cols[:3] == ["experiment", "language", "language_script"]
    assert {(row["language"], row["page"], row["language_script"]) for row in detailed_rows} == {
        ("Greek-English", "page_1", "English-Latin"),
        ("Greek-English", "page_1", "Greek-Greek"),
    }
    assert {(row["language"], row["language_script"]) for row in summary_rows} == {
        ("Greek-English", "English-Latin"),
        ("Greek-English", "Greek-Greek"),
    }
