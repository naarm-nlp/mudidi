from __future__ import annotations

from pathlib import Path

import pytest

from mudidi.config.benchmark_sweep import expand_benchmark_sweep
from mudidi.config.yaml_config import BenchmarkSweepConfig, load_yaml_config


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIGS = sorted((ROOT / "examples" / "configs").rglob("*.yaml"))


def test_canonical_example_configs_exist() -> None:
    names = {path.name for path in EXAMPLE_CONFIGS}
    assert names >= {
        "directory-inference.yaml",
        "pdf-inference.yaml",
        "stage1-full-sweep.yaml",
        "stage2-e2e-full-sweep.yaml",
        "stage1-evaluation.yaml",
        "stage2-evaluation.yaml",
    }


@pytest.mark.parametrize("path", EXAMPLE_CONFIGS, ids=lambda path: path.name)
def test_canonical_example_config_validates(path: Path) -> None:
    config = load_yaml_config(path)
    assert config.version == 1


def test_stage1_full_sweep_matches_complete_benchmark_output_matrix() -> None:
    config = load_yaml_config(
        ROOT / "examples/configs/benchmark/stage1-full-sweep.yaml"
    )
    assert isinstance(config, BenchmarkSweepConfig)

    runs = expand_benchmark_sweep(config)

    assert len(config.base.input.languages or []) == 30
    assert {run.name for run in runs} == {
        "Mathpix-OCR",
        "MinerU2.5-Pro",
        "PaddleOCR-VL-1.5",
        "GLM-OCR-flat_alpha",
        "GLM-OCR-flat_noalpha",
        "gemini3flash_flat_alpha",
        "gemini3flash_flat_noalpha",
        "gemini31pro_flat_alpha",
        "gemini31pro_flat_noalpha",
        "gemini31pro_flat_alpha_ocr",
        "gpt55_flat_alpha",
        "gpt55_flat_noalpha",
        "claudeopus47_flat_alpha",
        "claudeopus47_flat_noalpha",
        "qwen3vl235_flat_alpha",
        "qwen3vl235_flat_noalpha",
    }


def test_stage2_full_sweep_matches_complete_e2e_benchmark_matrix() -> None:
    config = load_yaml_config(
        ROOT / "examples/configs/benchmark/stage2-e2e-full-sweep.yaml"
    )
    assert isinstance(config, BenchmarkSweepConfig)

    runs = expand_benchmark_sweep(config)

    assert len(config.base.input.languages or []) == 10
    assert len(runs) == 16
    assert {
        "gemini31pro_high_mdf_intro_toolbox_from_gemini31pro_flat_alpha",
        "gpt55_high_mdf_nointro_notoolbox_from_gemini31pro_flat_alpha",
        "claudeopus47_high_mdf_intro_notoolbox_from_gemini31pro_flat_alpha",
        "qwen3vl235_high_mdf_nointro_toolbox_from_gemini31pro_flat_alpha",
    } <= {run.name for run in runs}
    assert all(run.config.pipeline.stage == "2" for run in runs)
    assert all(run.config.input.stage1_predictions_root for run in runs)
