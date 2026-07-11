from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mudidi.cli.main import build_parser
from mudidi.cli.run import (
    execution_namespace_from_config,
    preview_extraction_config,
    resolve_extraction_config,
    run_benchmark_sweep_command,
    run_evaluation_command,
    run_resolved_command,
)
from mudidi.config.yaml_config import BenchmarkRunConfig, InferenceConfig


def test_resolve_minimal_cli_inference_uses_defaults(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    args = argparse.Namespace(
        pages=str(tmp_path / "pages"),
        output_dir=str(tmp_path / "output"),
        dry_run=True,
    )

    config = resolve_extraction_config(args, kind="inference")

    assert isinstance(config, InferenceConfig)
    assert config.pipeline.stage1_mode == "flat"
    assert config.input.pages == tmp_path / "pages"
    assert config.output.directory == tmp_path / "output"


def test_cli_values_override_yaml_without_overwriting_omitted_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: original-pages
output:
  directory: original-output
models:
  default: provider/yaml
runtime:
  batch_size: 4
""".strip(),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        config=path,
        pages=str(tmp_path / "override-pages"),
        model="provider/cli",
        dry_run=True,
    )

    config = resolve_extraction_config(args, kind="inference")

    assert config.input.pages == tmp_path / "override-pages"
    assert config.models.default == "provider/cli"
    assert config.runtime.batch_size == 4
    assert config.output.directory == tmp_path / "original-output"


def test_agentic_cli_values_override_yaml_and_preserve_omitted_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: pages
output:
  directory: output
agentic:
  stage1: true
  stage2: true
  max_iterations: 4
  evaluator_model: provider/yaml-evaluator
  verifier_patches: true
  require_concrete_retry: true
""".strip(),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--config",
            str(path),
            "--no-stage1-agentic",
            "--agentic-max-iterations",
            "2",
            "--no-agentic-verifier-patches",
            "--no-agentic-concrete-retry-gate",
        ]
    )

    config = resolve_extraction_config(args, kind="inference")

    assert config.agentic.stage1 is False
    assert config.agentic.stage2 is True
    assert config.agentic.max_iterations == 2
    assert config.agentic.evaluator_model == "provider/yaml-evaluator"
    assert config.agentic.verifier_patches is False
    assert config.agentic.require_concrete_retry is False


def test_common_cli_input_paths_override_yaml_as_absolute_paths(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: original-pages
output:
  directory: original-output
""".strip(),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        config=path,
        intro=tmp_path / "intro.pdf",
        intro_pages="2-4",
        alphabet=tmp_path / "alphabet.txt",
        dictionary_languages=tmp_path / "languages.yaml",
        dry_run=True,
    )

    config = resolve_extraction_config(args, kind="inference")

    assert config.input.introduction == (tmp_path / "intro.pdf").resolve()
    assert config.input.introduction_pages == "2-4"
    assert config.input.alphabet == (tmp_path / "alphabet.txt").resolve()
    assert config.input.dictionary_languages == (tmp_path / "languages.yaml").resolve()


def test_execution_namespace_maps_advanced_yaml_settings(tmp_path: Path) -> None:
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {"pages": tmp_path / "pages"},
            "output": {"directory": tmp_path / "output"},
            "pipeline": {"stage": "1", "stage1_typography": True},
            "models": {"default": "provider/default", "stage1": "provider/s1"},
            "agentic": {"stage1": True, "max_iterations": 3},
            "runtime": {"batch_size": 2, "overwrite": True},
        }
    )

    namespace = execution_namespace_from_config(config)

    assert namespace.input_image == str(tmp_path / "pages")
    assert namespace.output == str(tmp_path / "output")
    assert namespace.stage1_typography is True
    assert namespace.stage1_agentic is True
    assert namespace.agentic_max_iterations == 3
    assert namespace.batch_size == 2
    assert namespace.overwrite is True
    assert namespace.stage1_reasoning_effort == "low"
    assert namespace.stage2_reasoning_effort == "low"


def test_minimal_production_config_does_not_require_optional_alphabet(
    tmp_path: Path,
) -> None:
    config = InferenceConfig.model_validate(
        {
            "kind": "inference",
            "input": {"pages": tmp_path / "pages"},
            "output": {"directory": tmp_path / "output"},
        }
    )

    namespace = execution_namespace_from_config(config)

    assert config.runtime.use_alphabet is False
    assert namespace.no_alphabet is True


def test_dry_run_does_not_invoke_extraction(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(b"not-decoded-in-dry-run")
    args = argparse.Namespace(
        pages=str(pages),
        output_dir=str(tmp_path / "output"),
        dry_run=True,
    )
    monkeypatch.setattr(
        "mudidi.cli.extract.main",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert run_resolved_command(args, parser=argparse.ArgumentParser(), kind="inference") == 0
    output = capsys.readouterr().out
    assert '"kind": "inference"' in output
    assert '"stage1_mode": "flat"' in output
    assert '"page_count": 1' in output
    assert '"derived_output"' in output


def test_dry_run_rejects_missing_inputs(tmp_path: Path) -> None:
    args = argparse.Namespace(
        pages=str(tmp_path / "missing"),
        output_dir=str(tmp_path / "output"),
        dry_run=True,
    )

    with pytest.raises(SystemExit):
        run_resolved_command(args, parser=argparse.ArgumentParser(), kind="inference")


def test_evaluation_cli_values_override_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "evaluation.yaml"
    config_path.write_text(
        """
version: 1
kind: stage1_evaluation
input:
  predicted: original-prediction.txt
  gold: gold.txt
output:
  directory: original-output
evaluation:
  workers: 2
""".strip(),
        encoding="utf-8",
    )
    captured = {}

    def fake_evaluate(*, config):
        captured["config"] = config
        return 0

    monkeypatch.setattr("mudidi.cli.evaluate_stage1.main", fake_evaluate)
    args = argparse.Namespace(
        config=config_path,
        predicted=str(tmp_path / "override.txt"),
        output_dir=str(tmp_path / "override-output"),
        experiment_name=["cli-one", "cli-two"],
        evaluation_stage="stage1",
    )

    result = run_evaluation_command(
        args,
        parser=argparse.ArgumentParser(),
        kind="stage1_evaluation",
    )

    assert result == 0
    config = captured["config"]
    assert config.input.predicted == (tmp_path / "override.txt").resolve()
    assert config.output.directory == (tmp_path / "override-output").resolve()
    assert config.evaluation.experiment_names == ["cli-one", "cli-two"]
    assert config.evaluation.workers == 2


def test_benchmark_sweep_dry_run_expands_without_execution_or_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    dataset = tmp_path / "dataset"
    pages = dataset / "Evenki-Russian" / "Dictionary pages"
    pages.mkdir(parents=True)
    (pages / "page_1.png").write_bytes(b"preview-only")
    output = tmp_path / "output"
    config_path = tmp_path / "sweep.yaml"
    config_path.write_text(
        f"""
version: 1
kind: benchmark_sweep
name: smoke
base:
  version: 1
  kind: benchmark_run
  input:
    dataset_dir: {dataset}
  output:
    directory: {output}
  pipeline:
    stage: "1"
experiments:
  - {{id: first, set: {{models.stage1: provider/first}}}}
  - {{id: second, set: {{models.stage1: provider/second}}}}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mudidi.cli.run.execute_extraction_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("must not execute")),
        raising=False,
    )
    args = argparse.Namespace(
        config=config_path,
        experiment=None,
        select=None,
        max_runs=None,
        dry_run=True,
    )

    result = run_benchmark_sweep_command(args, parser=argparse.ArgumentParser())

    assert result == 0
    output_text = capsys.readouterr().out
    assert '"run_count": 2' in output_text
    assert '"entry_run_count": 2' in output_text
    assert '"name": "first"' in output_text
    assert not output.exists()


def test_benchmark_sweep_executes_selected_runs_and_writes_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    pages = dataset / "Evenki-Russian" / "Dictionary pages"
    pages.mkdir(parents=True)
    (pages / "page_1.png").write_bytes(b"preview-only")
    output = tmp_path / "output"
    config_path = tmp_path / "sweep.yaml"
    config_path.write_text(
        f"""
version: 1
kind: benchmark_sweep
name: selected
base:
  version: 1
  kind: benchmark_run
  input: {{dataset_dir: {dataset}}}
  output: {{directory: {output}}}
  pipeline: {{stage: "1"}}
experiments:
  - {{id: first, set: {{models.stage1: provider/first}}}}
  - {{id: second, set: {{models.stage1: provider/second}}}}
""".strip(),
        encoding="utf-8",
    )
    executed = []

    def fake_execute(config):
        executed.append(config.runtime.experiment_name)
        return 0

    monkeypatch.setattr(
        "mudidi.cli.run.execute_extraction_config",
        fake_execute,
        raising=False,
    )
    args = argparse.Namespace(
        config=config_path,
        experiment=["second"],
        select=None,
        max_runs=None,
        dry_run=False,
    )

    result = run_benchmark_sweep_command(args, parser=argparse.ArgumentParser())

    assert result == 0
    assert executed == ["second"]
    manifest = output / "sweeps" / "selected" / "sweep_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["runs"][0]["name"] == "second"
    assert payload["runs"][0]["status"] == "complete"


def test_benchmark_preview_rejects_missing_stage1_prediction_slot(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    pages = dataset / "Evenki-Russian" / "Dictionary pages"
    pages.mkdir(parents=True)
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    config = BenchmarkRunConfig.model_validate(
        {
            "kind": "benchmark_run",
            "input": {
                "dataset_dir": dataset,
                "stage1_predictions_root": predictions,
            },
            "output": {"directory": tmp_path / "output"},
            "pipeline": {"stage": "2", "stage1_source": "predictions"},
            "runtime": {
                "experiment_name": "missing-slot",
                "stage2_experiment_name": "stage2-slot",
            },
        }
    )

    with pytest.raises(ValueError, match="missing Stage 1 prediction prerequisites"):
        preview_extraction_config(config)
