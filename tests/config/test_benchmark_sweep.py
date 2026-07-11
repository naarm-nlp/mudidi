from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.config.benchmark_sweep import expand_benchmark_sweep
from mudidi.config.yaml_config import BenchmarkSweepConfig, load_yaml_config


def _write_sweep(path: Path, body: str) -> Path:
    path.write_text(body.strip(), encoding="utf-8")
    return path


def test_axis_sweep_expands_cartesian_product_with_names(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    path = _write_sweep(
        tmp_path / "sweep.yaml",
        f"""
version: 1
kind: benchmark_sweep
name: stage1-matrix
base:
  version: 1
  kind: benchmark_run
  input:
    dataset_dir: {dataset}
  output:
    directory: output
  pipeline:
    stage: "1"
axes:
  model:
    - id: flash
      set:
        models.stage1: provider/flash
    - id: pro
      set:
        models.stage1: provider/pro
  alphabet:
    - id: alpha
      set:
        runtime.use_alphabet: true
    - id: noalpha
      set:
        runtime.use_alphabet: false
experiment_name: "{{model}}_{{alphabet}}"
sweep:
  max_runs: 8
""",
    )

    config = load_yaml_config(path, expected_kind="benchmark_sweep")
    assert isinstance(config, BenchmarkSweepConfig)

    runs = expand_benchmark_sweep(config)

    assert [run.name for run in runs] == [
        "flash_alpha",
        "flash_noalpha",
        "pro_alpha",
        "pro_noalpha",
    ]
    assert runs[0].config.models.stage1 == "provider/flash"
    assert runs[1].config.runtime.use_alphabet is False
    assert runs[0].config.output.directory == (tmp_path / "output").resolve()


def test_sweep_exclusions_remove_matching_axis_combination(tmp_path: Path) -> None:
    path = _write_sweep(
        tmp_path / "sweep.yaml",
        """
version: 1
kind: benchmark_sweep
name: exclusions
base:
  version: 1
  kind: benchmark_run
  input:
    dataset_dir: dataset
  output:
    directory: output
axes:
  model:
    - {id: flash, set: {models.stage1: provider/flash}}
    - {id: pro, set: {models.stage1: provider/pro}}
  alphabet:
    - {id: alpha, set: {runtime.use_alphabet: true}}
    - {id: noalpha, set: {runtime.use_alphabet: false}}
experiment_name: "{model}_{alphabet}"
exclude:
  - {model: pro, alphabet: noalpha}
""",
    )
    config = load_yaml_config(path)

    assert [run.name for run in expand_benchmark_sweep(config)] == [
        "flash_alpha",
        "flash_noalpha",
        "pro_alpha",
    ]


def test_explicit_sweep_supports_heterogeneous_typed_runs(tmp_path: Path) -> None:
    path = _write_sweep(
        tmp_path / "sweep.yaml",
        """
version: 1
kind: benchmark_sweep
name: explicit-runs
base:
  version: 1
  kind: benchmark_run
  input:
    dataset_dir: dataset
  output:
    directory: output
  pipeline:
    stage: "1"
experiments:
  - id: llm
    set:
      models.stage1: provider/model
      runtime.use_alphabet: true
  - id: glm
    set:
      pipeline.strategy: vlm_ocr
      vlm.model: glm-ocr
      runtime.use_alphabet: false
""",
    )
    config = load_yaml_config(path)

    runs = expand_benchmark_sweep(config)

    assert [run.name for run in runs] == ["llm", "glm"]
    assert runs[1].config.pipeline.strategy == "vlm_ocr"
    assert runs[1].config.vlm.model == "glm-ocr"


def test_sweep_rejects_unknown_override_path(tmp_path: Path) -> None:
    path = _write_sweep(
        tmp_path / "sweep.yaml",
        """
version: 1
kind: benchmark_sweep
name: invalid
base:
  version: 1
  kind: benchmark_run
  input: {dataset_dir: dataset}
  output: {directory: output}
experiments:
  - id: broken
    set:
      models.not_a_field: value
""",
    )
    config = load_yaml_config(path)

    with pytest.raises(ValidationError, match="not_a_field"):
        expand_benchmark_sweep(config)


def test_sweep_rejects_duplicate_names_and_run_limit(tmp_path: Path) -> None:
    duplicate_path = _write_sweep(
        tmp_path / "duplicate.yaml",
        """
version: 1
kind: benchmark_sweep
name: duplicate
base:
  version: 1
  kind: benchmark_run
  input: {dataset_dir: dataset}
  output: {directory: output}
experiments:
  - {id: same, set: {models.stage1: one}}
  - {id: same, set: {models.stage1: two}}
""",
    )

    with pytest.raises(ValidationError, match="duplicate"):
        load_yaml_config(duplicate_path)

    limited_path = _write_sweep(
        tmp_path / "limited.yaml",
        """
version: 1
kind: benchmark_sweep
name: limited
base:
  version: 1
  kind: benchmark_run
  input: {dataset_dir: dataset}
  output: {directory: output}
axes:
  model:
    - {id: one, set: {models.stage1: one}}
    - {id: two, set: {models.stage1: two}}
experiment_name: "{model}"
sweep:
  max_runs: 1
""",
    )
    limited = load_yaml_config(limited_path)

    with pytest.raises(ValueError, match="max_runs"):
        expand_benchmark_sweep(limited)


def test_sweep_rejects_selection_with_no_remaining_runs(tmp_path: Path) -> None:
    path = _write_sweep(
        tmp_path / "empty.yaml",
        """
version: 1
kind: benchmark_sweep
name: empty
base:
  version: 1
  kind: benchmark_run
  input: {dataset_dir: dataset}
  output: {directory: output}
axes:
  model:
    - {id: only, set: {models.stage1: one}}
experiment_name: "{model}"
exclude:
  - {model: only}
""",
    )
    config = load_yaml_config(path)

    with pytest.raises(ValueError, match="produced no runs"):
        expand_benchmark_sweep(config)
