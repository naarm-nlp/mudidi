"""MUDIDI command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path


def _add_sparse_agentic_arguments(parser: argparse.ArgumentParser) -> None:
    """Register agentic YAML overrides without implicit defaults."""
    group = parser.add_argument_group("agentic verifier-rewriter options")
    group.add_argument(
        "--stage1-agentic",
        action=argparse.BooleanOptionalAction,
        dest="agentic_stage1",
        default=argparse.SUPPRESS,
        help="Enable or disable bounded Stage 1 verification and rewriting.",
    )
    group.add_argument(
        "--stage2-agentic",
        action=argparse.BooleanOptionalAction,
        dest="agentic_stage2",
        default=argparse.SUPPRESS,
        help="Enable or disable bounded Stage 2 verification and rewriting.",
    )
    group.add_argument(
        "--agentic-max-iterations",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum rewrite attempts for each enabled agentic stage.",
    )
    group.add_argument(
        "--agentic-evaluator-model",
        default=argparse.SUPPRESS,
        help="Model used for verifier calls; defaults to the current stage model.",
    )
    group.add_argument(
        "--agentic-rewriter-model",
        default=argparse.SUPPRESS,
        help="Model used for correction calls; defaults to the current stage model.",
    )
    group.add_argument(
        "--agentic-reasoning",
        choices=["none", "low", "medium", "high"],
        default=argparse.SUPPRESS,
        help="Shared reasoning effort for verifier and rewriter calls.",
    )
    group.add_argument(
        "--agentic-evaluator-reasoning",
        choices=["none", "low", "medium", "high"],
        default=argparse.SUPPRESS,
        help="Verifier reasoning effort; overrides --agentic-reasoning.",
    )
    group.add_argument(
        "--agentic-rewriter-reasoning",
        choices=["none", "low", "medium", "high"],
        default=argparse.SUPPRESS,
        help="Rewriter reasoning effort; overrides --agentic-reasoning.",
    )
    group.add_argument(
        "--agentic-min-retry-confidence",
        type=float,
        default=argparse.SUPPRESS,
        help="Minimum verifier confidence required before a rewrite.",
    )
    group.add_argument(
        "--agentic-max-patches-per-attempt",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum exact patches accepted in one correction round.",
    )
    group.add_argument(
        "--agentic-verifier-patches",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="Enable or disable exact verifier patches before model rewriting.",
    )
    group.add_argument(
        "--agentic-concrete-retry-gate",
        action=argparse.BooleanOptionalAction,
        dest="agentic_require_concrete_retry",
        default=argparse.SUPPRESS,
        help="Require or waive localized evidence before retrying.",
    )


def _add_sparse_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Register common YAML overrides without implicit defaults."""
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--pages", default=argparse.SUPPRESS)
    parser.add_argument("--dict-pages", dest="dict_pages", default=argparse.SUPPRESS)
    parser.add_argument("--intro", default=argparse.SUPPRESS)
    parser.add_argument("--intro-pages", dest="intro_pages", default=argparse.SUPPRESS)
    parser.add_argument("--alphabet", default=argparse.SUPPRESS)
    parser.add_argument("--ocr-text", dest="ocr_text", default=argparse.SUPPRESS)
    parser.add_argument(
        "--dictionary-languages",
        dest="dictionary_languages",
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--toolbox-pdf", dest="toolbox_pdf", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", dest="output_dir", default=argparse.SUPPRESS)
    parser.add_argument(
        "--stage",
        choices=["1", "2", "all", "2-pass-1", "2-pass-2"],
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--model", default=argparse.SUPPRESS)
    parser.add_argument("--stage-1-model", dest="stage_1_model", default=argparse.SUPPRESS)
    parser.add_argument(
        "--stage-2-pass-1-model",
        dest="stage_2_pass_1_model",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--stage-2-pass-2-model",
        dest="stage_2_pass_2_model",
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--overwrite", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=False)
    _add_sparse_agentic_arguments(parser)


def _add_sparse_evaluation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--predicted", "-p", default=argparse.SUPPRESS)
    parser.add_argument("--gold", "-g", default=argparse.SUPPRESS)
    parser.add_argument("--dataset-dir", default=argparse.SUPPRESS)
    parser.add_argument("--pred-root", default=argparse.SUPPRESS)
    parser.add_argument("--samples-dir", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", "-o", default=argparse.SUPPRESS)
    parser.add_argument("--languages", nargs="+", default=argparse.SUPPRESS)
    parser.add_argument("--experiment-name", action="append", default=argparse.SUPPRESS)
    parser.add_argument("--all-experiments", action="store_true", default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    """Build the complete public MUDIDI command parser."""
    parser = argparse.ArgumentParser(
        prog="mudidi",
        description="Dictionary OCR and MDF extraction (inference and benchmark modes).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run production inference.")
    _add_sparse_run_arguments(run_parser)
    run_parser.set_defaults(_handler=_run_inference)

    benchmark = subparsers.add_parser("benchmark", help="Benchmark workflows.")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_run = benchmark_sub.add_parser("run", help="Run benchmark extraction.")
    _add_sparse_run_arguments(benchmark_run)
    benchmark_run.add_argument("--dataset-dir", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--samples-dir", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--languages", nargs="+", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--experiment-name", default=argparse.SUPPRESS)
    benchmark_run.set_defaults(_handler=_run_benchmark)

    benchmark_sweep = benchmark_sub.add_parser(
        "sweep", help="Run a typed benchmark experiment sweep."
    )
    benchmark_sweep.add_argument("--config", type=Path, required=True)
    benchmark_sweep.add_argument("--experiment", action="append")
    benchmark_sweep.add_argument("--select", action="append")
    benchmark_sweep.add_argument("--max-runs", type=int)
    benchmark_sweep.add_argument("--dry-run", action="store_true")
    benchmark_sweep.set_defaults(_handler=_run_benchmark_sweep)

    evaluate = benchmark_sub.add_parser("evaluate", help="Evaluate predictions.")
    evaluate_sub = evaluate.add_subparsers(dest="evaluation_stage", required=True)
    stage1_parser = evaluate_sub.add_parser("stage1")
    _add_sparse_evaluation_arguments(stage1_parser)
    stage1_parser.add_argument("--experiment-name-contains", default=argparse.SUPPRESS)
    stage1_parser.add_argument("--include-vlm-ocr", action="store_true", default=argparse.SUPPRESS)
    stage1_parser.add_argument("--stage1-output-subdir", default=argparse.SUPPRESS)
    stage1_parser.add_argument("--metrics", choices=["full", "minimal"], default=argparse.SUPPRESS)
    stage1_parser.add_argument("--alignment-threshold", type=float, default=argparse.SUPPRESS)
    stage1_parser.add_argument(
        "--character-alignment",
        choices=["collapsed", "quick_match"],
        default=argparse.SUPPRESS,
    )
    stage1_parser.add_argument("--per-language-script", action="store_true", default=argparse.SUPPRESS)
    stage1_parser.add_argument("--overwrite", action="store_true", default=argparse.SUPPRESS)
    stage1_parser.add_argument("--workers", type=int, default=argparse.SUPPRESS)
    stage1_parser.set_defaults(_handler=_run_evaluation)

    stage2_parser = evaluate_sub.add_parser("stage2")
    _add_sparse_evaluation_arguments(stage2_parser)
    stage2_parser.add_argument("--baseline-summary", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--baseline-experiment", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--comparison-output", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--record-threshold", type=float, default=argparse.SUPPRESS)
    stage2_parser.add_argument("--line-threshold", type=float, default=argparse.SUPPRESS)
    stage2_parser.add_argument("--marker-sub-list", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--dictionary-languages", default=argparse.SUPPRESS)
    stage2_parser.set_defaults(_handler=_run_evaluation)

    config = subparsers.add_parser("config", help="Configuration utilities.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="Validate a YAML config.")
    validate.add_argument("config", type=Path)
    validate.set_defaults(_handler=_validate_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one fully parsed MUDIDI command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args._handler(args, parser)


def _run_inference(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_resolved_command

    return run_resolved_command(args, parser=parser, kind="inference")


def _run_benchmark(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_resolved_command

    return run_resolved_command(args, parser=parser, kind="benchmark_run")


def _run_benchmark_sweep(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    from mudidi.cli.run import run_benchmark_sweep_command

    return run_benchmark_sweep_command(args, parser=parser)


def _run_evaluation(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_evaluation_command

    kind = f"{args.evaluation_stage}_evaluation"
    return run_evaluation_command(args, parser=parser, kind=kind)


def _validate_config(args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    from mudidi.config.yaml_config import load_yaml_config, validate_config_paths

    config = load_yaml_config(args.config)
    validate_config_paths(config)
    print(f"Valid MUDIDI config: {config.kind} (version {config.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
