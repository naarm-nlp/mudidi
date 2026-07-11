# Benchmark extraction

Stage 1 benchmark:

```bash
uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage1-full-sweep.yaml \
  --dry-run
```

End-to-end Stage 2 benchmark:

```bash
uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage2-e2e-full-sweep.yaml \
  --dry-run
```

Remove `--dry-run` after reviewing the expanded runs and destinations. The
Stage 1 sweep expands to 16 experiments × 30 languages (480 entry runs). The
Stage 2 sweep expands to 4 models × 4 context choices × 10 languages (160 entry
runs).

## Selecting part of a sweep

Run one named experiment:

```bash
uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage1-full-sweep.yaml \
  --experiment gemini31pro_flat_alpha
```

Filter an axis-based sweep:

```bash
uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage2-e2e-full-sweep.yaml \
  --select model=gemini31pro \
  --select context=intro_toolbox
```

Every expanded run is validated before the first API call. `sweep.max_runs`
guards against accidental Cartesian explosions, while `failure_policy`
controls whether later experiments continue after a failure. Execution is
sequential in v1 so model rate limits and output manifests remain predictable.

Sweep status is recorded at:

```text
<output.directory>/sweeps/<sweep-name>/sweep_manifest.json
```

## Provenance of the canonical sweeps

The canonical files were reconstructed from the complete repeated experiment
slots under `outputs/benchmark`, then checked against the shell scripts. Debug,
spot-check, partial, and agentic-only slots were excluded.

The Stage 1 output does not exactly match the current historical shell file:
the output contains `gemini31pro_flat_alpha_ocr`, while the current script names
a `gemini3flash_flat_alpha_typography` run that is not present as a complete
30-language output slot. The canonical sweep follows the on-disk manifests.
Mathpix runs first and publishes per-page Markdown hints used by the historical
`gemini31pro_flat_alpha_ocr` experiment.

The Stage 2 E2E sweep stages the `gemini31pro_flat_alpha` prediction tree from
the Stage 1 output root before each Stage 2 run. This replaces the `rsync`
preparation formerly embedded in `run_stage2_e2e.sh`.
