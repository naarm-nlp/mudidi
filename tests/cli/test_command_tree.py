from __future__ import annotations

import pytest

from mudidi.cli.main import build_parser


def test_public_command_tree_parses_production_run() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--pages",
            "dictionary.pdf",
            "--output-dir",
            "output",
            "--dry-run",
        ]
    )

    assert args.command == "run"
    assert args.pages == "dictionary.pdf"
    assert args.dry_run is True


def test_public_command_tree_parses_common_input_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--pages",
            "dictionary.pdf",
            "--output-dir",
            "output",
            "--intro",
            "introduction.pdf",
            "--intro-pages",
            "1-3",
            "--alphabet",
            "alphabet.txt",
            "--dictionary-languages",
            "languages.yaml",
        ]
    )

    assert args.intro == "introduction.pdf"
    assert args.intro_pages == "1-3"
    assert args.alphabet == "alphabet.txt"
    assert args.dictionary_languages == "languages.yaml"


def test_public_command_tree_parses_agentic_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--pages",
            "pages",
            "--output-dir",
            "output",
            "--stage1-agentic",
            "--stage2-agentic",
            "--agentic-max-iterations",
            "3",
            "--agentic-evaluator-model",
            "provider/evaluator",
            "--agentic-rewriter-model",
            "provider/rewriter",
            "--agentic-reasoning",
            "medium",
            "--agentic-evaluator-reasoning",
            "high",
            "--agentic-rewriter-reasoning",
            "low",
            "--agentic-min-retry-confidence",
            "0.7",
            "--agentic-max-patches-per-attempt",
            "8",
            "--no-agentic-verifier-patches",
            "--no-agentic-concrete-retry-gate",
        ]
    )

    assert args.agentic_stage1 is True
    assert args.agentic_stage2 is True
    assert args.agentic_max_iterations == 3
    assert args.agentic_evaluator_model == "provider/evaluator"
    assert args.agentic_rewriter_model == "provider/rewriter"
    assert args.agentic_reasoning == "medium"
    assert args.agentic_evaluator_reasoning == "high"
    assert args.agentic_rewriter_reasoning == "low"
    assert args.agentic_min_retry_confidence == 0.7
    assert args.agentic_max_patches_per_attempt == 8
    assert args.agentic_verifier_patches is False
    assert args.agentic_require_concrete_retry is False


@pytest.mark.parametrize(
    "flag",
    ["--agentic-catastrophic-recovery", "--no-agentic-catastrophic-recovery"],
)
def test_public_command_tree_rejects_removed_catastrophic_recovery_flag(
    flag: str,
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["run", "--pages", "pages", "--output-dir", "output", flag]
        )


def test_omitted_agentic_options_remain_sparse() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["run", "--pages", "pages", "--output-dir", "output"]
    )

    assert not any(name.startswith("agentic_") for name in vars(args))


def test_public_command_tree_parses_benchmark_samples_override() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "benchmark",
            "run",
            "--samples-dir",
            "samples",
            "--output-dir",
            "output",
        ]
    )

    assert args.samples_dir == "samples"


def test_public_command_tree_parses_benchmark_evaluation() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "benchmark",
            "evaluate",
            "stage1",
            "--config",
            "stage1-evaluation.yaml",
        ]
    )

    assert args.command == "benchmark"
    assert args.benchmark_command == "evaluate"
    assert args.evaluation_stage == "stage1"


def test_public_command_tree_parses_benchmark_sweep() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "benchmark",
            "sweep",
            "--config",
            "stage1-sweep.yaml",
            "--experiment",
            "gemini_alpha",
            "--select",
            "model=gemini",
            "--max-runs",
            "20",
            "--dry-run",
        ]
    )

    assert args.benchmark_command == "sweep"
    assert args.experiment == ["gemini_alpha"]
    assert args.select == ["model=gemini"]
    assert args.max_runs == 20
    assert args.dry_run is True


def test_unknown_run_option_fails_immediately() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--unknown-option"])
