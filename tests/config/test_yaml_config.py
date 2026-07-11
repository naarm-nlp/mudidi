from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi.config.yaml_config import (
    BenchmarkRunConfig,
    InferenceConfig,
    load_yaml_config,
    merge_explicit_overrides,
    validate_config_paths,
)


def test_load_inference_config_resolves_paths_from_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    path = config_dir / "inference.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: ../inputs/dictionary.pdf
output:
  directory: ../outputs/run
pipeline:
  stage: all
""".strip(),
        encoding="utf-8",
    )

    config = load_yaml_config(path, expected_kind="inference")

    assert isinstance(config, InferenceConfig)
    assert config.input.pages == (config_dir / "../inputs/dictionary.pdf").resolve()
    assert config.output.directory == (config_dir / "../outputs/run").resolve()
    assert config.pipeline.stage1_mode == "flat"


def test_yaml_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: dictionary.pdf
output:
  directory: output
surprise: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="surprise"):
        load_yaml_config(path)


def test_yaml_config_rejects_removed_catastrophic_recovery_option(
    tmp_path: Path,
) -> None:
    path = tmp_path / "removed-agentic-option.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: dictionary.pdf
output:
  directory: output
agentic:
  stage1: true
  catastrophic_recovery: false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="catastrophic_recovery"):
        load_yaml_config(path)


def test_yaml_config_rejects_removed_patch_limit_option(tmp_path: Path) -> None:
    path = tmp_path / "removed-patch-limit.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: dictionary.pdf
output:
  directory: output
agentic:
  stage1: true
  max_patches_per_attempt: 16
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="max_patches_per_attempt"):
        load_yaml_config(path)


def test_yaml_config_rejects_command_kind_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.yaml"
    path.write_text(
        """
version: 1
kind: benchmark_run
input:
  dataset_dir: dataset/MUDIDI/dictionaries
output:
  directory: outputs/benchmark
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires kind 'inference'"):
        load_yaml_config(path, expected_kind="inference")


def test_explicit_cli_overrides_replace_yaml_values() -> None:
    config = BenchmarkRunConfig.model_validate(
        {
            "version": 1,
            "kind": "benchmark_run",
            "input": {
                "dataset_dir": "/dataset",
                "languages": ["Evenki-Russian", "Chukchi-Russian"],
            },
            "output": {"directory": "/outputs"},
        }
    )

    assert config.pipeline.stage1_source == "gold"

    merged = merge_explicit_overrides(
        config,
        {
            "input.languages": ["Yiddish-English"],
            "models.default": "provider/model",
        },
    )

    assert merged.input.languages == ["Yiddish-English"]
    assert merged.models.default == "provider/model"


def test_advanced_vlm_python_paths_resolve_from_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    path = config_dir / "vlm.yaml"
    path.write_text(
        """
version: 1
kind: benchmark_run
input:
  pages: ../inputs/page.pdf
output:
  directory: ../outputs
pipeline:
  strategy: vlm_ocr
  stage: "1"
vlm:
  model: paddleocr-vl-1.5
  paddle_server_python: ../venv/bin/python
  glm_server_python: ../glm/bin/python
""".strip(),
        encoding="utf-8",
    )

    config = load_yaml_config(path, expected_kind="benchmark_run")

    assert config.vlm.paddle_server_python == (config_dir / "../venv/bin/python").resolve()
    assert config.vlm.glm_server_python == (config_dir / "../glm/bin/python").resolve()


def test_stage1_evaluation_accepts_advanced_evaluation_settings(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.yaml"
    path.write_text(
        """
version: 1
kind: stage1_evaluation
input:
  predicted: prediction.txt
  gold: gold.txt
output:
  directory: reports
evaluation:
  experiment_name_contains: agentic
  include_vlm_ocr: true
  stage1_output_subdir: custom-stage-1
  metrics: full
  alignment_threshold: 0.75
  overwrite: true
""".strip(),
        encoding="utf-8",
    )

    config = load_yaml_config(path, expected_kind="stage1_evaluation")

    assert config.evaluation.experiment_name_contains == "agentic"
    assert config.evaluation.include_vlm_ocr is True
    assert config.evaluation.stage1_output_subdir == "custom-stage-1"
    assert config.evaluation.metrics == "full"
    assert config.evaluation.alignment_threshold == 0.75
    assert config.evaluation.overwrite is True


def test_path_validation_rejects_missing_input_but_not_output(tmp_path: Path) -> None:
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {"pages": tmp_path / "missing.pdf"},
            "output": {"directory": tmp_path / "new-output"},
        }
    )

    with pytest.raises(ValueError, match="input.pages does not exist"):
        validate_config_paths(config)


def test_path_validation_checks_page_specs(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {
                "pages": pages,
                "dictionary_pages": "9-2",
            },
            "output": {"directory": tmp_path / "output"},
        }
    )

    with pytest.raises(ValueError, match="input.dictionary_pages"):
        validate_config_paths(config)


def test_path_validation_requires_page_selection_for_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "dictionary.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {"pages": pdf},
            "output": {"directory": tmp_path / "output"},
        }
    )

    with pytest.raises(ValueError, match="dictionary_pages is required"):
        validate_config_paths(config)


def test_evaluation_rejects_mixed_single_file_and_batch_inputs(tmp_path: Path) -> None:
    path = tmp_path / "mixed.yaml"
    path.write_text(
        """
version: 1
kind: stage1_evaluation
input:
  predicted: prediction.txt
  gold: gold.txt
  dataset_dir: dataset
  pred_root: predictions
output:
  directory: reports
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="either predicted.gold or dataset_dir.pred_root"):
        load_yaml_config(path)


def test_benchmark_config_accepts_mathpix_stage1_strategy(tmp_path: Path) -> None:
    path = tmp_path / "mathpix.yaml"
    path.write_text(
        """
version: 1
kind: benchmark_run
input:
  dataset_dir: dataset
output:
  directory: output
pipeline:
  stage: "1"
  strategy: mathpix_ocr
runtime:
  experiment_name: Mathpix-OCR
mathpix:
  poll_interval_seconds: 2
  max_wait_seconds: 300
""".strip(),
        encoding="utf-8",
    )

    config = load_yaml_config(path)

    assert config.pipeline.strategy == "mathpix_ocr"
    assert config.mathpix.poll_interval_seconds == 2
    assert config.mathpix.max_wait_seconds == 300


def test_stage1_predictions_root_resolves_relative_to_config(tmp_path: Path) -> None:
    path = tmp_path / "stage2.yaml"
    path.write_text(
        """
version: 1
kind: benchmark_run
input:
  dataset_dir: dataset
  stage1_predictions_root: predictions/stage-1
output:
  directory: output
pipeline:
  stage: "2"
  stage1_source: predictions
runtime:
  experiment_name: source-slot
  stage2_experiment_name: stage2-slot
""".strip(),
        encoding="utf-8",
    )

    config = load_yaml_config(path)

    assert config.input.stage1_predictions_root == (
        tmp_path / "predictions/stage-1"
    ).resolve()
