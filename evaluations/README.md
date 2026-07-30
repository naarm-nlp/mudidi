# Canonical evaluation results

This directory contains the tracked evaluation reports used for the main
MUDIDI benchmark analysis. Prediction trees remain under `outputs/benchmark/`;
the files here are derived CSV reports and small provenance artifacts.

## Result sets

| Directory | Meaning | Producer |
|---|---|---|
| `stage1_flat_per_lang_script_eval/` | Current Stage 1 flat-transcription reports, including global and per-language/script metrics | `examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh` |
| `stage2_mdf_lang_script_eval/` | Current Stage 2 MDF evaluation using oracle/gold Stage 1 inputs, with projection-based per-language/script reports | `examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh` |
| `stage2_mdf_lang_script_eval_stage1-gold/` | Earlier gold-Stage-1 comparison snapshot in the legacy single-summary schema | retained for historical comparison |
| `stage2_mdf_eval_e2e_lexical_repair/` | End-to-end Stage 2 predictions after lexical repair, plus repair audit | `examples/evaluation/run_stage2_e2e_lexical_repair.sh` |
| `stage2_mdf_eval_no_typography/` | Focused Stage 2 no-typography experiment and baseline comparison | `examples/evaluation/run_stage2_no_typography_eval.sh` |

`stage2_mdf_lang_script_eval/` is the current Stage 2 oracle result set. The
similarly named `stage2_mdf_lang_script_eval_stage1-gold/` directory is an
older snapshot with a different report schema; it is not the output directory
used by the current evaluation script.

The helper
`examples/evaluation/run_stage2_mdf_stage1_lang_projection.sh` regenerates
language/script projection metadata consumed by the current Stage 2 evaluator.
Its projection CSV is regeneratable support data rather than a separately
published canonical result set.

## Reproduction

Run scripts from the repository root. Every script accepts environment
variables such as `DATASET_DIR`, `PRED_ROOT`, and `OUTPUT_DIR`, so a
reproducibility check can target a temporary directory without overwriting the
tracked reports:

```bash
OUTPUT_DIR=evaluations/reproduction-stage1 \
  bash examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh
```

Compare regenerated CSVs with the matching canonical directory after the run.
Some directories intentionally contain both aggregate and per-model or
specialized reports; these are retained to preserve experiment provenance.

## Scope boundary

Outputs whose experiment or path name contains `agentic` come from side
experiments and are deliberately excluded from the canonical evaluation set.
Debug, partial, spot-check, and archived runs are also non-canonical. Local
obsolete results may be kept under ignored archive directories, but should not
be added back to this root result inventory.
