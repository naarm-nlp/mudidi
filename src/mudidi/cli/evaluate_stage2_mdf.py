"""
CLI: evaluate Stage 2 MDF extraction against gold.

Usage:
  uv run mudidi-eval-stage2-mdf -p pred.mdf.txt -g gold.mdf.txt
  uv run mudidi-eval-stage2-mdf \\
      --samples-dir assets/dictionaries/samples \\
      --experiment-name gemini31pro_high_mdf_intro_notoolbox \\
      --experiment-name gemini31pro_high_mdf_intro_toolbox \\
      -o evaluations/stage2_mdf_eval
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import OrderedDict
from pathlib import Path

from mudidi.evaluation.stage2.mdf_evaluator import MdfEvaluator

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_AGGREGATE_PAGE_ID = "__aggregate__"
_NON_METRIC_COLS = frozenset({"experiment", "page_id"})
_PREFERRED_METRIC_ORDER = (
    "Record_Accuracy",
    "MDF_Fields_F1",
    "ReadOrderEdit",
    "Field_Value_GCER",
    "Field_Value_WER",
    "Headword_GCER",
    "Gloss_GCER",
)


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _metric_columns(rows: list[dict[str, str]]) -> list[str]:
    """Numeric summary columns shared between new and baseline CSV schemas."""
    if not rows:
        return list(_PREFERRED_METRIC_ORDER)
    available = [col for col in rows[0].keys() if col not in _NON_METRIC_COLS]
    ordered = [col for col in _PREFERRED_METRIC_ORDER if col in available]
    ordered.extend(col for col in sorted(available) if col not in ordered)
    return ordered


def _metric_delta(new_value: str | None, baseline_value: str | None) -> str:
    new_num = _safe_float(new_value)
    base_num = _safe_float(baseline_value)
    if new_num is None or base_num is None:
        return ""
    return f"{new_num - base_num:.6f}"


def _write_baseline_comparison_csv(
    *,
    new_summary: Path,
    baseline_summary: Path,
    baseline_experiment: str,
    output_path: Path,
) -> None:
    """Write per-page metric deltas against a baseline summary CSV."""
    baseline_rows: dict[str, dict[str, str]] = {}
    with baseline_summary.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("experiment") != baseline_experiment:
                continue
            page_id = row.get("page_id", "")
            if page_id == _AGGREGATE_PAGE_ID:
                continue
            baseline_rows[page_id] = row

    new_rows: list[dict[str, str]] = []
    with new_summary.open(encoding="utf-8", newline="") as handle:
        new_rows.extend(csv.DictReader(handle))

    page_rows = [
        row for row in new_rows if row.get("page_id") not in ("", _AGGREGATE_PAGE_ID)
    ]
    metrics = _metric_columns(page_rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "page_id",
            "baseline_experiment",
            "experiment",
            *(
                col
                for metric in metrics
                for col in (f"baseline_{metric}", metric, f"delta_{metric}")
            ),
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in page_rows:
            baseline = baseline_rows.get(row.get("page_id", ""))
            out: dict[str, str] = {
                "page_id": row.get("page_id", ""),
                "baseline_experiment": baseline_experiment,
                "experiment": row.get("experiment", ""),
            }
            for metric in metrics:
                new_value = row.get(metric, "")
                baseline_value = baseline.get(metric, "") if baseline else ""
                out[f"baseline_{metric}"] = baseline_value
                out[metric] = new_value
                out[f"delta_{metric}"] = (
                    _metric_delta(new_value, baseline_value) if baseline else ""
                )
            writer.writerow(out)


def main(
    argv: list[str] | None = None,
    *,
    config: object | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Stage 2 MDF against gold")
    parser.add_argument("-p", "--predicted", help="Predicted .mdf.txt")
    parser.add_argument("-g", "--gold", help="Gold .mdf.txt")
    parser.add_argument("--samples-dir", help="Legacy samples root for batch mode")
    parser.add_argument(
        "--dataset-dir",
        help="MUDIDI dataset root containing per-language dictionary folders.",
    )
    parser.add_argument(
        "--pred-root",
        help="Prediction root matching <language>/stage-2/<experiment>/<page>/ layout.",
    )
    parser.add_argument(
        "--experiment-name",
        dest="experiment_names",
        action="append",
        default=None,
    )
    parser.add_argument("--languages", nargs="+", default=None)
    parser.add_argument("--all-experiments", action="store_true")
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument(
        "--baseline-summary",
        default=None,
        help="Existing stage2_mdf_eval_summary.csv to compare against.",
    )
    parser.add_argument(
        "--baseline-experiment",
        default=None,
        help="Experiment name inside --baseline-summary to compare against.",
    )
    parser.add_argument(
        "--comparison-output",
        default=None,
        help="Optional comparison CSV path. Defaults under --output-dir.",
    )
    parser.add_argument(
        "--record-threshold",
        type=float,
        default=0.6,
        help="Record fingerprint similarity threshold (default: 0.6)",
    )
    parser.add_argument(
        "--line-threshold",
        type=float,
        default=0.7,
        help="Line value similarity threshold (default: 0.7)",
    )
    parser.add_argument(
        "--marker-sub-list",
        dest="marker_sub_list",
        default=None,
        help="Optional path to mdf_marker_sub_list.yaml",
    )
    parser.add_argument(
        "--dictionary-languages",
        dest="dictionary_languages",
        default=None,
        help="Optional dictionary_languages.yaml for per-language MDF metrics.",
    )
    if config is None:
        args = parser.parse_args(argv)
    else:
        from mudidi.config.yaml_config import Stage2EvaluationConfig

        if not isinstance(config, Stage2EvaluationConfig):
            raise TypeError("Stage 2 evaluation requires Stage2EvaluationConfig")
        options = config.evaluation
        args = argparse.Namespace(
            predicted=str(config.input.predicted) if config.input.predicted else None,
            gold=str(config.input.gold) if config.input.gold else None,
            samples_dir=None,
            dataset_dir=(
                str(config.input.dataset_dir) if config.input.dataset_dir else None
            ),
            pred_root=str(config.input.pred_root) if config.input.pred_root else None,
            experiment_names=list(options.experiment_names) or None,
            languages=config.input.languages,
            all_experiments=options.all_experiments,
            output_dir=str(config.output.directory),
            baseline_summary=(
                str(options.baseline_summary) if options.baseline_summary else None
            ),
            baseline_experiment=options.baseline_experiment,
            comparison_output=(
                str(options.comparison_output) if options.comparison_output else None
            ),
            record_threshold=options.record_threshold,
            line_threshold=options.line_threshold,
            marker_sub_list=(
                str(options.marker_sub_list) if options.marker_sub_list else None
            ),
            dictionary_languages=(
                str(options.dictionary_languages)
                if options.dictionary_languages
                else None
            ),
        )

    evaluator = MdfEvaluator(
        record_threshold=args.record_threshold,
        line_threshold=args.line_threshold,
        marker_sub_list_path=args.marker_sub_list,
        dictionary_languages_path=args.dictionary_languages,
    )

    if args.predicted:
        pred = Path(args.predicted)
        gold = Path(args.gold) if args.gold else None
        if not pred.is_file() or gold is None or not gold.is_file():
            logger.error("Single-file mode requires existing -p and -g paths")
            return 1
        page_id = pred.parent.name
        results = [evaluator.evaluate(pred, gold, page_id=page_id)]
        out = Path(args.output_dir) if args.output_dir else pred.parent
        text = evaluator.generate_text_report(results, out / "stage2_mdf_evaluation_report.txt")
        evaluator.generate_json_report(results, out / "stage2_mdf_evaluation_report.json")
        print(text)
        print(f"\nReports saved to: {out}")
        return 0

    experiments = None if args.all_experiments else args.experiment_names
    if args.dataset_dir or args.pred_root:
        if not args.dataset_dir or not args.pred_root:
            parser.error("--dataset-dir and --pred-root must be used together")
        dataset = Path(args.dataset_dir)
        pred_root = Path(args.pred_root)
        if not dataset.is_dir():
            logger.error("Dataset directory not found: %s", dataset)
            return 1
        if not pred_root.is_dir():
            logger.error("Prediction root not found: %s", pred_root)
            return 1
        tasks = evaluator.discover_dataset_tasks(
            dataset,
            pred_root,
            experiments=experiments,
            languages=args.languages,
        )
        default_out = pred_root / "stage2_mdf_eval"
    else:
        if not args.samples_dir:
            parser.error("Provide -p/-g, --samples-dir, or --dataset-dir with --pred-root")
        samples = Path(args.samples_dir)
        if not samples.is_dir():
            logger.error("Samples directory not found: %s", samples)
            return 1
        tasks = evaluator.discover_tasks(
            samples,
            experiments=experiments,
            languages=args.languages,
        )
        default_out = samples / "stage2_mdf_eval"

    if not tasks:
        logger.error("No stage-2 gold/pred MDF pairs found")
        return 1

    out = Path(args.output_dir) if args.output_dir else default_out
    out.mkdir(parents=True, exist_ok=True)

    results_by_exp: OrderedDict[str, list] = OrderedDict()
    for task in tasks:
        logger.info("  [eval] %s :: %s", task.experiment, task.page_id)
        metrics = evaluator.evaluate(task.pred_path, task.gold_path, page_id=task.page_id)
        results_by_exp.setdefault(task.experiment, []).append(metrics)

    for exp, pages in results_by_exp.items():
        exp_out = out / exp
        text = evaluator.generate_text_report(pages, exp_out / "stage2_mdf_evaluation_report.txt")
        evaluator.generate_json_report(pages, exp_out / "stage2_mdf_evaluation_report.json")
        print(f"\n### Experiment: {exp} ({len(pages)} page(s)) ###")
        print(text)

    summary_csv = out / "stage2_mdf_eval_summary.csv"
    evaluator.generate_summary_csv(results_by_exp, summary_csv)
    per_lang_script_detailed_csv = out / "stage2_mdf_eval_per_language_script_detailed.csv"
    evaluator.generate_per_language_script_detailed_csv(
        results_by_exp,
        per_lang_script_detailed_csv,
    )
    per_lang_script_summary_csv = out / "stage2_mdf_eval_per_language_script_summary.csv"
    evaluator.generate_per_language_script_summary_csv(
        results_by_exp,
        per_lang_script_summary_csv,
    )
    print(f"\nSummary CSV: {summary_csv}")
    print(f"Per-language-script detailed CSV: {per_lang_script_detailed_csv}")
    print(f"Per-language-script summary CSV: {per_lang_script_summary_csv}")
    print(f"Reports under: {out}")

    if args.baseline_summary or args.baseline_experiment:
        if not args.baseline_summary or not args.baseline_experiment:
            parser.error("--baseline-summary and --baseline-experiment must be used together")
        baseline_summary = Path(args.baseline_summary)
        if not baseline_summary.is_file():
            logger.error("Baseline summary not found: %s", baseline_summary)
            return 1
        comparison_csv = (
            Path(args.comparison_output)
            if args.comparison_output
            else out / "stage2_mdf_eval_vs_baseline.csv"
        )
        _write_baseline_comparison_csv(
            new_summary=summary_csv,
            baseline_summary=baseline_summary,
            baseline_experiment=args.baseline_experiment,
            output_path=comparison_csv,
        )
        print(f"Comparison CSV: {comparison_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
