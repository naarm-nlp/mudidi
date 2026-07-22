"""Contracts for the dedicated Stage 2 E2E per-language/script runner."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "examples/evaluation/run_stage2_e2e_per_lang_script_eval.sh"


def test_stage2_e2e_per_lang_script_runner_has_safe_defaults() -> None:
    contents = SCRIPT.read_text(encoding="utf-8")

    assert os.access(SCRIPT, os.X_OK)
    assert 'E2E="1"' in contents
    assert 'RUN_PROJECTION="${RUN_PROJECTION:-0}"' in contents
    assert "outputs/benchmark/stage-2-e2e" in contents
    assert "evaluations/stage2_mdf_eval_e2e" in contents
    assert "run_stage2_benchmark_per_lang_script_eval.sh" in contents
    assert '"$@"' in contents


def test_stage2_e2e_per_lang_script_runner_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
