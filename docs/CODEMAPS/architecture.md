<!-- Generated: 2026-07-11 | Files scanned: 255 | Token estimate: ~750 -->

# MUDIDI Architecture

## System Type

Python library + CLI for multilingual dictionary digitization. Single-package layout (`src/mudidi/`), not a monorepo or web service.

## Purpose

Two-stage pipeline: **Stage 1** transcribes scanned dictionary pages; **Stage 2** parses transcripts into [SIL Toolbox MDF](https://software.sil.org/toolbox/) records.

## High-Level Data Flow

```
Page images/PDF  ──► Stage 1 (LLM or VLM OCR)  ──► flat .txt / column .tsv
                              │
                              ▼
Stage 1 transcript ──► Stage 2 Pass 1 (parse-rule discovery) ──► parse-rules.json
                              │
                              ▼
              Stage 2 Pass 2 (direct MDF) ──► per-page .mdf.txt
                              │
                              ▼
         Evaluation (stage1 flat / stage2 MDF metrics) ──► CSV reports
```

## Entry Points

| Command | Module | Role |
|---------|--------|------|
| `mudidi run` | `cli/main.py` → typed inference config → `cli/extract.py` | Production inference |
| `mudidi benchmark run` | `cli/main.py` → typed benchmark config → `cli/extract.py` | Benchmark extraction |
| `mudidi benchmark sweep` | `config/benchmark_sweep.py` → typed benchmark runs | Multi-experiment benchmark matrix |
| `mudidi benchmark evaluate stage1` | `cli/evaluate_stage1.py` | Flat transcription benchmark |
| `mudidi benchmark evaluate stage2` | `cli/evaluate_stage2_mdf.py` | MDF record benchmark |
| `mudidi config validate` | `config/yaml_config.py` | Offline YAML validation |

## Core Layers

```
CLI (cli/)
  └─► Config (config/yaml_config.py, run_config.py, output_paths.py)
        └─► Extraction strategies (extraction/)
              ├─ TwoStageLLMExtraction (llm_two_stage.py)  [default]
              ├─ VLM OCR batch (vlm_ocr.py)
              └─ Mathpix OCR batch (mathpix_ocr.py)
                    └─ OCR backends (ocr/) + LLM client (llm/)
                          └─ Schemas (schemas/) + Utils (utils/)
```

## Run Modes

- **Inference**: user pages dir or PDF → `outputs/<run>/stage-1/`, `stage-2/`
- **Benchmark**: `dataset/MUDIDI/dictionaries/<Lang-Pair>/` sample layout, gold comparison
- **Sweep**: explicit runs or Cartesian axes → validated `BenchmarkRunConfig` list → sequential execution + sweep manifest

## Stage Control (`--stage`)

`1` | `2` | `all` | `2-pass-1` | `2-pass-2`

## Adjacent Systems (outside package)

| Path | Role |
|------|------|
| `annotation/` | Label Studio NER, language-span labeling, tier-2 recovery |
| `label-studio/` | LS project setup, sync, gold export |
| `scripts/` | Data prep, migration, audit utilities |
| `evaluations/` | Published benchmark CSV results |
| `dataset/MUDIDI/` | Gold pages, parquet exports |

## Agentic Extension

`agentic/verifier_loop.py` — bounded verifier-rewriter loop used by `TwoStageLLMExtraction` for Stage 1/2 quality retries. The verifier may request safe exact patches; Stage 1 automatically discards a catastrophically corrupted transcript and re-transcribes its page image.

## Key Design Boundaries

- Prompt templates: `assets/PROMPT.json` → `llm/prompt_store.py`
- Domain models: `schemas/` (Pydantic)
- Provider calls: single gateway `llm/client.py` (litellm)
- Evaluation: separate `evaluation/stage1/` and `evaluation/stage2/` packages
