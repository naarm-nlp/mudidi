# CLI migration

| Legacy command or option | Replacement |
|---|---|
| `mudidi run --benchmark ...` | `mudidi benchmark run --config benchmark.yaml` |
| Shell model/ablation matrices | `mudidi benchmark sweep --config sweep.yaml` |
| `mudidi eval stage1 ...` | `mudidi benchmark evaluate stage1 --config evaluation.yaml` |
| `mudidi eval stage2 ...` | `mudidi benchmark evaluate stage2 --config evaluation.yaml` |
| `mudidi-eval-flat` | `mudidi benchmark evaluate stage1` |
| `mudidi-eval-stage2-mdf` | `mudidi benchmark evaluate stage2` |
| Agentic flags | `mudidi run --stage1-agentic` / `--stage2-agentic` and related options, or typed YAML |
| Advanced pipeline/cache/VLM settings | Typed YAML sections |
| `--stage1-mode column` | `pipeline.stage1_mode: column` |
| `--parse-rules-file PATH` | `pipeline.parse_rules_file: PATH` |
| `--toolbox-pdf PATH` | `input.toolbox_pdf: PATH` |
| `--stage1-source predictions` | `pipeline.stage1_source: predictions` |

The command syntax changes deliberately; experiment names, models, stage choices, input sources, and output layouts remain reproducible through canonical YAML.
