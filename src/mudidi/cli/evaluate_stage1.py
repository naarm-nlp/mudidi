"""
CLI: evaluate Stage 1 flat transcription (``evaluate_stage1`` / ``mudidi-eval-flat``).

Usage:
  uv run mudidi-eval-flat \\
      --samples-dir assets/dictionaries/samples \\
      --all-experiments -o evaluations/stage1_flat_eval
"""

from __future__ import annotations

import argparse
import os
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Literal, Tuple

from mudidi.evaluation.stage1.flat_evaluator import FlatEvalTask, FlatStage1Evaluator
from mudidi.evaluation.stage1.stage1_eval_cache import (
    Stage1EvalCache,
)
from mudidi.evaluation.stage1.stage1_metrics import Stage1Metrics
from mudidi.evaluation.stage1.stage1_reports import Stage1ReportWriter

FLAT_CACHE_FILE_NAME = "stage1_flat_eval_cache.json"
FLAT_OCR_HINT_SUMMARY_FILE_NAME = "stage1_flat_eval_ocr_hint_summary.csv"
FLAT_OCR_HINT_DETAILED_FILE_NAME = "stage1_flat_eval_ocr_hint_detailed.csv"
FLAT_PER_LANGUAGE_SCRIPT_DETAILED_FILE_NAME = (
    "stage1_flat_eval_per_language_script_detailed.csv"
)
FLAT_PER_LANGUAGE_SCRIPT_SUMMARY_FILE_NAME = (
    "stage1_flat_eval_per_language_script_summary.csv"
)
FLAT_OCR_HINT_PER_LANGUAGE_SCRIPT_DETAILED_FILE_NAME = (
    "stage1_flat_eval_ocr_hint_per_language_script_detailed.csv"
)
FLAT_OCR_HINT_PER_LANGUAGE_SCRIPT_SUMMARY_FILE_NAME = (
    "stage1_flat_eval_ocr_hint_per_language_script_summary.csv"
)

MetricsProfile = Literal["full", "minimal"]
CharacterAlignmentMode = Literal["collapsed", "quick_match"]


def _parallel_eval_worker(
    payload: tuple[
        str,
        str,
        str,
        str,
        MetricsProfile,
        CharacterAlignmentMode,
        float,
        bool,
    ],
) -> tuple[str, str, Path, Path, Stage1Metrics]:
    """Evaluate one flat page (picklable entry point for ProcessPoolExecutor)."""
    (
        experiment,
        pred_s,
        gold_s,
        page_id,
        metrics_profile,
        character_alignment,
        ath,
        per_language_script,
    ) = payload
    evaluator = FlatStage1Evaluator(
        metrics_profile=metrics_profile,
        character_alignment=character_alignment,
        alignment_threshold=ath,
        per_language_script=per_language_script,
    )
    pred_path = Path(pred_s)
    gold_path = Path(gold_s)
    metrics = evaluator.evaluate(
        pred_path,
        gold_path,
        page_id=page_id,
    )
    return experiment, page_id, pred_path, gold_path, metrics

# Specialized OCR backends (folder names without "flat"; preds use *_stage1_flat.txt).
DEFAULT_VLM_OCR_EXPERIMENTS: tuple[str, ...] = (
    "MinerU2.5-Pro",
    "PaddleOCR-VL-1.5",
    "Mathpix-OCR",
)

# Partial/legacy one-dictionary slots that should not be included in benchmark
# aggregate discovery. They remain evaluable only when explicitly requested by
# name outside the default benchmark helpers.
DEFAULT_EXCLUDED_STAGE1_BENCHMARK_EXPERIMENTS: frozenset[str] = frozenset(
    {
        "GLM-OCR",
        "qwen3vl235_flat_noalpha_ocr",
    }
)


def list_stage1_experiments_from_pred_root(
    pred_root: Path,
    *,
    languages: list[str] | None,
    name_contains: str | None,
    stage1_output_subdir: str = "stage-1",
) -> list[str]:
    """Return sorted experiment folder names under benchmark ``pred_root`` layout."""
    needle = name_contains.lower() if name_contains else None
    names: set[str] = set()
    for lang_dir in sorted(pred_root.iterdir()):
        if not lang_dir.is_dir():
            continue
        if languages and lang_dir.name not in languages:
            continue
        stage1_root = lang_dir / stage1_output_subdir
        if not stage1_root.is_dir():
            continue
        for child in stage1_root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                if child.name in DEFAULT_EXCLUDED_STAGE1_BENCHMARK_EXPERIMENTS:
                    continue
                if needle is None or needle in child.name.lower():
                    names.add(child.name)
    return sorted(names)


def find_flat_and_vlm_ocr_experiments_from_pred_root(
    pred_root: Path,
    *,
    languages: list[str] | None,
    stage1_output_subdir: str = "stage-1",
) -> list[str]:
    """LLM flat ablations plus specialized OCR folders from benchmark pred root."""
    flat = list_stage1_experiments_from_pred_root(
        pred_root,
        languages=languages,
        name_contains="flat",
        stage1_output_subdir=stage1_output_subdir,
    )
    on_disk = set(
        list_stage1_experiments_from_pred_root(
            pred_root,
            languages=languages,
            name_contains=None,
            stage1_output_subdir=stage1_output_subdir,
        )
    )
    ocr = [name for name in DEFAULT_VLM_OCR_EXPERIMENTS if name in on_disk]
    return sorted(set(flat) | set(ocr))


def list_stage1_experiments(
    samples_dir: Path,
    *,
    languages: list[str] | None,
    name_contains: str | None,
    stage1_output_subdir: str = "stage-1",
) -> list[str]:
    """Return sorted experiment folder names under ``outputs/<stage1_output_subdir>``."""
    needle = name_contains.lower() if name_contains else None
    names: set[str] = set()
    for lang_dir in sorted(samples_dir.iterdir()):
        if not lang_dir.is_dir():
            continue
        if languages and lang_dir.name not in languages:
            continue
        stage1_root = lang_dir / "outputs" / stage1_output_subdir
        if not stage1_root.is_dir():
            continue
        for child in stage1_root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                if child.name in DEFAULT_EXCLUDED_STAGE1_BENCHMARK_EXPERIMENTS:
                    continue
                if needle is None or needle in child.name.lower():
                    names.add(child.name)
    return sorted(names)


def find_flat_and_vlm_ocr_experiments(
    samples: Path,
    *,
    languages: list[str] | None,
    stage1_output_subdir: str = "stage-1",
) -> list[str]:
    """LLM flat ablations plus specialized OCR folders (excludes column Gemini)."""
    flat = list_stage1_experiments(
        samples,
        languages=languages,
        name_contains="flat",
        stage1_output_subdir=stage1_output_subdir,
    )
    on_disk = set(
        list_stage1_experiments(
            samples,
            languages=languages,
            name_contains=None,
            stage1_output_subdir=stage1_output_subdir,
        )
    )
    ocr = [name for name in DEFAULT_VLM_OCR_EXPERIMENTS if name in on_disk]
    return sorted(set(flat) | set(ocr))


def split_results_by_ocr_hint(
    results_by_exp: "OrderedDict[str, list[Stage1Metrics]]",
    samples_dir: Path,
    *,
    stage1_output_subdir: str = "stage-1",
    pred_root: Path | None = None,
) -> tuple[OrderedDict[str, list[Stage1Metrics]], OrderedDict[str, list[Stage1Metrics]]]:
    """Partition cached results into non-OCR-hint vs OCR-hint LLM experiments."""
    without_hint: OrderedDict[str, list[Stage1Metrics]] = OrderedDict()
    with_hint: OrderedDict[str, list[Stage1Metrics]] = OrderedDict()
    for experiment, metrics in results_by_exp.items():
        languages: set[str] = set()
        for metric in metrics:
            parsed = Stage1ReportWriter._parse_page_id(metric.page_id)
            if parsed is not None:
                languages.add(parsed[0])
        uses_ocr_hint = False
        for language in languages:
            _, ocr_hint = Stage1ReportWriter._load_run_config_flags(
                samples_dir,
                language,
                experiment,
                stage1_output_subdir=stage1_output_subdir,
                pred_root=pred_root,
            )
            if ocr_hint:
                uses_ocr_hint = True
                break
        target = with_hint if uses_ocr_hint else without_hint
        target[experiment] = metrics
    return without_hint, with_hint


def experiment_names_for_eval(
    args: argparse.Namespace,
    samples: Path | None = None,
    *,
    pred_root: Path | None = None,
) -> list[str] | None:
    """Resolve which experiment folders to include in batch eval-flat."""
    subdir = getattr(args, "stage1_output_subdir", "stage-1")
    if pred_root is not None:
        def list_experiments(**kw: object) -> list[str]:
            return list_stage1_experiments_from_pred_root(pred_root, **kw)

        def find_flat_vlm(**kw: object) -> list[str]:
            return find_flat_and_vlm_ocr_experiments_from_pred_root(pred_root, **kw)
    else:
        if samples is None:
            raise ValueError("samples required when pred_root is not set")

        def list_experiments(**kw: object) -> list[str]:
            return list_stage1_experiments(samples, **kw)

        def find_flat_vlm(**kw: object) -> list[str]:
            return find_flat_and_vlm_ocr_experiments(samples, **kw)

    if args.include_vlm_ocr:
        found = find_flat_vlm(languages=args.languages, stage1_output_subdir=subdir)
        if args.experiment_names:
            allowed = set(args.experiment_names)
            found = [name for name in found if name in allowed]
        if not found:
            print("No flat or VLM OCR experiments found on disk.")
        else:
            print(f"Experiments (flat + VLM OCR): {found}")
        return found
    if args.experiment_name_contains:
        found = list_experiments(
            languages=args.languages,
            name_contains=args.experiment_name_contains,
            stage1_output_subdir=subdir,
        )
        if args.experiment_names:
            allowed = set(args.experiment_names)
            found = [name for name in found if name in allowed]
        if not found:
            print(
                f"No experiments matching name filter {args.experiment_name_contains!r}."
            )
        else:
            print(f"Experiments (name contains {args.experiment_name_contains!r}): {found}")
        return found
    if args.all_experiments:
        return None
    return args.experiment_names


def main(
    argv: list[str] | None = None,
    *,
    config: object | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Stage 1 flat OCR transcription (eval-flat)",
    )
    parser.add_argument("-p", "--predicted", help="Predicted flat .txt or column .tsv")
    parser.add_argument("-g", "--gold", help="Gold flat .txt")
    parser.add_argument("--samples-dir", help="Legacy samples root for batch mode")
    parser.add_argument(
        "--dataset-dir",
        help="MUDIDI dataset root containing per-language dictionary folders.",
    )
    parser.add_argument(
        "--pred-root",
        help="Prediction root matching <language>/stage-1/<experiment>/<page>/ layout.",
    )
    parser.add_argument(
        "--experiment-name",
        dest="experiment_names",
        action="append",
        default=None,
    )
    parser.add_argument("--languages", nargs="+", default=None)
    parser.add_argument("--all-experiments", action="store_true")
    parser.add_argument(
        "--experiment-name-contains",
        default=None,
        metavar="SUBSTR",
        help=(
            "Only evaluate experiment folders whose name contains SUBSTR "
            "(case-insensitive), e.g. 'flat' for LLM flat ablations."
        ),
    )
    parser.add_argument(
        "--include-vlm-ocr",
        action="store_true",
        help=(
            "Evaluate gemini*flat* experiments plus MinerU / Paddle / GLM OCR. "
            "Does not include column-mode gemini3flash_* or legacy."
        ),
    )
    parser.add_argument(
        "--stage1-output-subdir",
        default="stage-1",
        dest="stage1_output_subdir",
        help="Prediction root under outputs/ (default: stage-1). Use stage-1-ocr for "
        "per-language best OCR-hint runs.",
    )
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument(
        "--metrics",
        choices=("full", "minimal"),
        default="minimal",
    )
    parser.add_argument(
        "--alignment-threshold",
        type=float,
        default=0.6,
        help="Deprecated; quick_match thresholds are fixed (OmniDocBench defaults).",
    )
    parser.add_argument(
        "--character-alignment",
        choices=("collapsed", "quick_match"),
        default="quick_match",
        help=(
            "How to score character/typography metrics: "
            "'quick_match' (default; OmniDocBench line-level Adjacency Search Match) or "
            "'collapsed' (join whole page into one string per side)."
        ),
    )
    parser.add_argument(
        "--per-language-script",
        action="store_true",
        help=(
            "Also compute per-language-script (e.g. Japanese-Kanji, English-Latin) "
            "character/word quality, attributed via each page's gold *_lang.json "
            "span map. Silently skipped for pages/dictionaries without one "
            "(currently only Japanese-English page_137/page_351)."
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Parallel worker processes for page evaluation (default: 1). "
            "Use cpu_count-2 on batch nodes (e.g. 14 on a 16-core allocation)."
        ),
    )
    if config is None:
        args = parser.parse_args(argv)
    else:
        from mudidi.config.yaml_config import Stage1EvaluationConfig

        if not isinstance(config, Stage1EvaluationConfig):
            raise TypeError("Stage 1 evaluation requires Stage1EvaluationConfig")
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
            experiment_name_contains=options.experiment_name_contains,
            include_vlm_ocr=options.include_vlm_ocr,
            stage1_output_subdir=options.stage1_output_subdir,
            output_dir=str(config.output.directory),
            metrics=options.metrics,
            alignment_threshold=options.alignment_threshold,
            character_alignment=options.character_alignment,
            per_language_script=options.per_language_script,
            overwrite=options.overwrite,
            workers=options.workers,
        )
    if args.workers < 1:
        parser.error("--workers must be >= 1")

    evaluator = FlatStage1Evaluator(
        metrics_profile=args.metrics,
        character_alignment=args.character_alignment,
        alignment_threshold=args.alignment_threshold,
        per_language_script=args.per_language_script,
    )

    if args.predicted:
        pred = Path(args.predicted)
        gold = Path(args.gold) if args.gold else None
        if not pred.exists() or not gold or not gold.exists():
            print("Error: -p and -g must exist for single-file mode")
            return 1
        page_id = pred.stem.replace("_stage1_flat", "").replace("_stage1", "")
        results = [
            evaluator.evaluate(
                pred,
                gold,
                page_id=page_id,
            )
        ]
        out = Path(args.output_dir) if args.output_dir else pred.parent
        report_path = out / "stage1_flat_evaluation_report.txt"
        text = evaluator.generate_text_report(results, report_path)
        evaluator.generate_json_report(results, out / "stage1_flat_evaluation_report.json")
        evaluator.generate_csv_reports(results, out)
        print(text)
        print(f"\nReports saved to: {out}")
        return 0

    use_dataset_layout = bool(args.dataset_dir or args.pred_root)
    if use_dataset_layout:
        if not args.dataset_dir or not args.pred_root:
            parser.error("--dataset-dir and --pred-root must be used together")
        dataset = Path(args.dataset_dir)
        pred_root = Path(args.pred_root)
        if not dataset.is_dir():
            print(f"Error: dataset directory not found: {dataset}")
            return 1
        if not pred_root.is_dir():
            print(f"Error: prediction root not found: {pred_root}")
            return 1
        config_root = dataset
    else:
        if not args.samples_dir:
            parser.error(
                "Provide -p/-g, --samples-dir, or --dataset-dir with --pred-root"
            )
        samples = Path(args.samples_dir)
        if not samples.is_dir():
            print(f"Error: samples directory not found: {samples}")
            return 1
        config_root = samples
        pred_root = None
        dataset = None

    mode_flags = sum(
        bool(x)
        for x in (
            args.all_experiments,
            args.experiment_name_contains,
            args.include_vlm_ocr,
        )
    )
    if mode_flags > 1:
        parser.error(
            "Use only one of --all-experiments, --experiment-name-contains, "
            "--include-vlm-ocr."
        )

    out = (
        Path(args.output_dir)
        if args.output_dir
        else (pred_root / "stage1_flat_eval" if pred_root else samples / "stage1_flat_eval")
    )
    out.mkdir(parents=True, exist_ok=True)

    experiment_names = experiment_names_for_eval(
        args,
        samples if not use_dataset_layout else None,
        pred_root=pred_root,
    )
    if experiment_names is not None and not experiment_names:
        return 1

    if use_dataset_layout:
        assert dataset is not None and pred_root is not None
        def discover(langs: list[str] | None) -> list[FlatEvalTask]:
            return evaluator.discover_dataset_tasks(
                dataset,
                pred_root,
                experiments=experiment_names,
                languages=langs,
                stage1_output_subdir=args.stage1_output_subdir,
            )
    else:
        assert samples is not None
        def discover(langs: list[str] | None) -> list[FlatEvalTask]:
            return evaluator.discover_tasks(
                samples,
                experiments=experiment_names,
                languages=langs,
                stage1_output_subdir=args.stage1_output_subdir,
            )

    tasks_export = discover(None)
    tasks_eval = discover(args.languages)
    if not tasks_export:
        print("No flat gold/pred pairs found.")
        return 1

    eval_keys = {(t.experiment, t.page_id) for t in tasks_eval}
    cache = Stage1EvalCache(out / FLAT_CACHE_FILE_NAME)
    cache.load()
    metrics_by_key: Dict[Tuple[str, str], Stage1Metrics] = {}
    n_cached = n_evaled = 0
    ath = evaluator.alignment_threshold
    calign = evaluator.character_alignment

    to_eval: list[FlatEvalTask] = []
    for task in tasks_export:
        key = (task.experiment, task.page_id)
        in_eval = key in eval_keys
        cache_ok = cache.entry_valid(
            task.experiment,
            task.page_id,
            task.pred_path,
            task.gold_path,
            ath,
            calign,
            args.per_language_script,
        )
        if cache_ok and not (in_eval and args.overwrite):
            entry = cache.get_entry(task.experiment, task.page_id)
            if entry is not None:
                metrics_by_key[key] = entry.metrics
                n_cached += 1
                continue
        to_eval.append(task)

    def _store_eval_result(
        experiment: str,
        page_id: str,
        pred_path: Path,
        gold_path: Path,
        metrics: Stage1Metrics,
    ) -> None:
        nonlocal n_evaled
        print(f"  [eval-flat] {experiment} :: {page_id}")
        cache.put(
            experiment,
            page_id,
            pred_path,
            gold_path,
            ath,
            calign,
            metrics,
            args.per_language_script,
        )
        metrics_by_key[(experiment, page_id)] = metrics
        n_evaled += 1

    if to_eval and args.workers > 1:
        worker_payloads = [
            (
                task.experiment,
                str(task.pred_path),
                str(task.gold_path),
                task.page_id,
                args.metrics,
                args.character_alignment,
                ath,
                args.per_language_script,
            )
            for task in to_eval
        ]
        print(
            f"Evaluating {len(to_eval)} page(s) with {args.workers} worker(s) "
            f"(cpus on node: {os.cpu_count()})"
        )
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(_parallel_eval_worker, worker_payloads):
                _store_eval_result(*result)
    else:
        for task in to_eval:
            m = evaluator.evaluate(
                task.pred_path,
                task.gold_path,
                page_id=task.page_id,
            )
            _store_eval_result(
                task.experiment,
                task.page_id,
                task.pred_path,
                task.gold_path,
                m,
            )

    if not use_dataset_layout:
        assert samples is not None
        cache.prune_stale_paths(samples, stage1_output_subdir=args.stage1_output_subdir)
    cache.save()

    paired = []
    for task in tasks_export:
        key = (task.experiment, task.page_id)
        if key in metrics_by_key:
            paired.append((task.experiment, metrics_by_key[key]))

    print(
        f"\nFlat eval cache: {out / FLAT_CACHE_FILE_NAME} — "
        f"reused {n_cached}, computed {n_evaled} page(s)."
    )

    results_by_exp: OrderedDict[str, list[Stage1Metrics]] = OrderedDict()
    for exp, metrics in paired:
        results_by_exp.setdefault(exp, []).append(metrics)

    for exp, results in results_by_exp.items():
        exp_out = out / exp
        text = evaluator.generate_text_report(
            results, exp_out / "stage1_flat_evaluation_report.txt"
        )
        evaluator.generate_json_report(
            results, exp_out / "stage1_flat_evaluation_report.json"
        )
        print(f"\n### Experiment: {exp} ({len(results)} page(s)) ###")
        print(text)

    tasks_for_csv = discover(args.languages)
    results_for_csv: OrderedDict[str, list[Stage1Metrics]] = cache.collect_valid_metrics(
        tasks_for_csv,
        alignment_threshold=ath,
        character_alignment=calign,
        per_language_script=args.per_language_script,
    )
    main_results, ocr_hint_results = split_results_by_ocr_hint(
        results_for_csv,
        config_root,
        stage1_output_subdir=args.stage1_output_subdir,
        pred_root=pred_root,
    )
    split_ocr_hint = args.stage1_output_subdir == "stage-1"
    if split_ocr_hint:
        csv_results = main_results
        n_csv_exps = len(main_results)
        n_csv_pages = sum(len(v) for v in main_results.values())
        print(
            f"Aggregate CSVs: {n_csv_pages} page(s) across {n_csv_exps} experiment(s) "
            f"in main summary (OCR-hint LLM runs excluded)."
        )
        if ocr_hint_results:
            print(
                f"OCR-hint sidecar: {sum(len(v) for v in ocr_hint_results.values())} "
                f"page(s) across {len(ocr_hint_results)} experiment(s) → "
                f"{FLAT_OCR_HINT_SUMMARY_FILE_NAME}"
            )
    else:
        csv_results = results_for_csv
        n_csv_exps = len(results_for_csv)
        n_csv_pages = sum(len(v) for v in results_for_csv.values())
        print(
            f"Aggregate CSVs: {n_csv_pages} page(s) across {n_csv_exps} experiment(s)."
        )
    csv_kwargs = {
        "stage1_output_subdir": args.stage1_output_subdir,
        "pred_root": pred_root,
    }
    evaluator.generate_detailed_csv(
        csv_results,
        config_root,
        out / "stage1_flat_eval_detailed.csv",
        **csv_kwargs,
    )
    evaluator.generate_summary_csv(
        csv_results,
        config_root,
        out / "stage1_flat_eval_summary.csv",
        **csv_kwargs,
    )
    if args.per_language_script:
        evaluator.generate_per_language_script_detailed_csv(
            csv_results,
            out / FLAT_PER_LANGUAGE_SCRIPT_DETAILED_FILE_NAME,
        )
        evaluator.generate_per_language_script_summary_csv(
            csv_results,
            out / FLAT_PER_LANGUAGE_SCRIPT_SUMMARY_FILE_NAME,
        )
    if split_ocr_hint and ocr_hint_results:
        evaluator.generate_detailed_csv(
            ocr_hint_results,
            config_root,
            out / FLAT_OCR_HINT_DETAILED_FILE_NAME,
            **csv_kwargs,
        )
        evaluator.generate_summary_csv(
            ocr_hint_results,
            config_root,
            out / FLAT_OCR_HINT_SUMMARY_FILE_NAME,
            **csv_kwargs,
        )
        if args.per_language_script:
            evaluator.generate_per_language_script_detailed_csv(
                ocr_hint_results,
                out / FLAT_OCR_HINT_PER_LANGUAGE_SCRIPT_DETAILED_FILE_NAME,
            )
            evaluator.generate_per_language_script_summary_csv(
                ocr_hint_results,
                out / FLAT_OCR_HINT_PER_LANGUAGE_SCRIPT_SUMMARY_FILE_NAME,
            )
    print(f"\nReports under: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
