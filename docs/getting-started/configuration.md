# YAML configuration

YAML is optional for a minimal run and recommended for repeatable or advanced
work. Common model, stage, and agentic settings can also be supplied explicitly
on the CLI; omitted CLI options never overwrite YAML values.

```bash
uv run mudidi config validate examples/configs/production/directory-inference.yaml
uv run mudidi run --config examples/configs/production/directory-inference.yaml --dry-run
```

Configuration rules:

- `version` is currently `1`.
- `kind` must match the command.
- Paths are relative to the YAML file.
- Unknown fields fail validation.
- Explicit CLI options override YAML; YAML overrides built-in defaults.
- Lists supplied on the CLI replace YAML lists.
- API keys remain in `.env`.

See the generated [configuration schema](../reference/config.md).

## Benchmark sweeps

Use `kind: benchmark_sweep` for repeated experiments. A sweep contains one
typed `benchmark_run` base and either:

- `experiments`: an explicit ordered list of heterogeneous overrides; or
- `axes`: a Cartesian product with an `experiment_name` template and optional
  exclusions.

Overrides use validated dotted field paths such as `models.stage1`,
`runtime.use_alphabet`, or `vlm.model`. Each expanded result must independently
validate as a `BenchmarkRunConfig`.

```yaml
version: 1
kind: benchmark_sweep
name: model-ablation
base:
  version: 1
  kind: benchmark_run
  input: {dataset_dir: ../../../dataset/MUDIDI/dictionaries}
  output: {directory: ../../../outputs/benchmark/example}
  pipeline: {stage: "1"}
axes:
  model:
    - {id: flash, set: {models.stage1: gemini/gemini-3-flash-preview}}
    - {id: pro, set: {models.stage1: gemini/gemini-3.1-pro-preview}}
  alphabet:
    - {id: alpha, set: {runtime.use_alphabet: true}}
    - {id: noalpha, set: {runtime.use_alphabet: false}}
experiment_name: "{model}_{alphabet}"
sweep: {max_runs: 10, failure_policy: continue}
```
