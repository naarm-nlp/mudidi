# MUDIDI examples

Minimal scripts and validated YAML configurations for production inference and benchmarking.

## Prerequisites

1. `uv sync` and API keys in `.env` (see root [README](../README.md)).
2. Download the [MUDIDI dataset](https://huggingface.co/datasets/DavidSamuell/mudidi) to `dataset/MUDIDI/` (for directory mode and evaluation).
3. For PDF mode: place a full dictionary scan under `inputs/` (see comment in `inference/run_pdf_mode.sh`).

## Scripts

| Script | What it does |
| --- | --- |
| [`inference/run_directory_mode.sh`](inference/run_directory_mode.sh) | Stage 1 + 2 on **Evenki-Russian** snippet pages |
| [`inference/run_pdf_mode.sh`](inference/run_pdf_mode.sh) | Stage 1 + 2 on **Carolinian-English** PDF in `inputs/` |
| [`evaluation/run_stage1_eval.sh`](evaluation/run_stage1_eval.sh) | Flat transcription vs gold (`page_1`) |
| [`evaluation/run_stage2_eval.sh`](evaluation/run_stage2_eval.sh) | MDF vs gold (`page_1`) |

Run all scripts from the **repository root**.

## Typical workflow

```bash
bash examples/inference/run_directory_mode.sh
bash examples/evaluation/run_stage1_eval.sh
bash examples/evaluation/run_stage2_eval.sh
```

Pass common overrides through any script, e.g. `bash examples/inference/run_directory_mode.sh --overwrite`. Advanced settings live in the referenced YAML files.

Paper benchmark numbers are frozen in [`evaluations/`](../evaluations/).
