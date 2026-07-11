from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from mudidi.cli import run as run_cli


def test_run_forwards_agentic_flags(monkeypatch, tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    run_cli.register_run_arguments(parser)
    args = parser.parse_args(
        [
            "--pages",
            str(tmp_path / "pages"),
            "--output-dir",
            str(tmp_path / "out"),
            "--stage1-agentic",
            "--stage1-typography",
            "--stage2-agentic",
            "--agentic-max-iterations",
            "3",
            "--agentic-evaluator-model",
            "provider/eval",
            "--agentic-rewriter-model",
            "provider/rewrite",
            "--agentic-reasoning",
            "medium",
            "--agentic-evaluator-reasoning",
            "high",
            "--agentic-rewriter-reasoning",
            "low",
            "--agentic-min-retry-confidence",
            "0.7",
            "--no-agentic-verifier-patches",
            "--no-agentic-concrete-retry-gate",
        ]
    )

    captured: dict[str, list[str]] = {}

    def fake_extract_main() -> int:
        captured["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(run_cli, "configure_prompts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("mudidi.cli.extract.main", fake_extract_main)

    assert run_cli.run_from_args(args, []) == 0

    forwarded = captured["argv"]
    assert "--stage1-typography" in forwarded
    assert "--stage1-agentic" in forwarded
    assert "--stage2-agentic" in forwarded
    assert forwarded[forwarded.index("--agentic-max-iterations") + 1] == "3"
    assert forwarded[forwarded.index("--agentic-evaluator-model") + 1] == "provider/eval"
    assert forwarded[forwarded.index("--agentic-rewriter-model") + 1] == "provider/rewrite"
    assert forwarded[forwarded.index("--agentic-reasoning") + 1] == "medium"
    assert forwarded[forwarded.index("--agentic-evaluator-reasoning") + 1] == "high"
    assert forwarded[forwarded.index("--agentic-rewriter-reasoning") + 1] == "low"
    assert forwarded[forwarded.index("--agentic-min-retry-confidence") + 1] == "0.7"
    assert "--no-agentic-verifier-patches" in forwarded
    assert "--no-agentic-concrete-retry-gate" in forwarded


@pytest.mark.parametrize(
    "flag",
    ["--agentic-catastrophic-recovery", "--no-agentic-catastrophic-recovery"],
)
def test_run_rejects_removed_catastrophic_recovery_flag(flag: str) -> None:
    parser = argparse.ArgumentParser()
    run_cli.register_run_arguments(parser)

    with pytest.raises(SystemExit):
        parser.parse_args([flag])


def test_run_rejects_removed_patch_limit_option() -> None:
    parser = argparse.ArgumentParser()
    run_cli.register_run_arguments(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["--agentic-max-patches-per-attempt", "7"])


def test_run_rejects_removed_patch_only_verifier_flag() -> None:
    parser = argparse.ArgumentParser()
    run_cli.register_run_arguments(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["--stage1-agentic-patch-verifier"])


@pytest.mark.parametrize(
    "args",
    [
        ["--agentic-max-rewrite-delta-ratio", "0.5"],
        ["--no-agentic-max-rewrite-delta-gate"],
    ],
)
def test_run_rejects_removed_rewrite_delta_flags(args: list[str]) -> None:
    parser = argparse.ArgumentParser()
    run_cli.register_run_arguments(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(args)
