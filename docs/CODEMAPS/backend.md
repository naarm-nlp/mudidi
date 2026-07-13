<!-- Generated: 2026-07-11 | Files scanned: 255 | Token estimate: ~900 -->

# Pipeline & CLI Architecture

> MUDIDI has no HTTP backend. This codemap covers the processing pipeline (CLI → extraction → LLM/OCR).

## CLI Dispatch

```
mudidi main (cli/main.py)
  ├─ run → typed inference config → extract.py
  ├─ benchmark run → typed benchmark config → extract.py
  ├─ benchmark sweep → expand/validate all runs → sequential extract.py calls
  ├─ benchmark evaluate
  │    ├─ stage1 → evaluate_stage1.py:main
  │    └─ stage2 → evaluate_stage2_mdf.py:main
  └─ config validate → config/yaml_config.py
```

## `mudidi run` Argument Flow

```
main.py (single public parser, sparse CLI overrides)
  → load/merge typed YAML (config/yaml_config.py)
  → execution_namespace_from_config (temporary compatibility adapter)
  → extract.py (orchestration driver)
```

## Benchmark Sweep Flow

```
benchmark_sweep YAML
  → benchmark_sweep.py (axes/explicit expansion, filters, max-runs guard)
  → BenchmarkRunConfig validation + input/prerequisite preview for every run
  → sequential execution (failure_policy: continue|stop)
  → <output>/sweeps/<name>/sweep_manifest.json
```

## Extraction Orchestration (`cli/extract.py`)

Responsibilities:
- Materialize page inputs (snippets dir, PDF split via pdftk, rasterize via PyMuPDF)
- Collect intro / alphabet / OCR-hint / neighbor context
- Select strategy: `TwoStageLLMExtraction`, VLM OCR batch, or Mathpix OCR batch
- ThreadPoolExecutor page concurrency with rate-limit backoff
- Write manifests, usage JSON, stage outputs

## Extraction Strategies

| Strategy | File | Stages |
|----------|------|--------|
| `TwoStageLLMExtraction` | `extraction/llm_two_stage.py` | Stage 1 structured transcript → Pass 1 parse rules → Pass 2 MDF |
| VLM OCR | `extraction/vlm_ocr.py` | Stage 1 only via `ocr/vlm/runner.py` |
| Mathpix OCR | `extraction/mathpix_ocr.py` | Stage 1 Mathpix Convert API → flat text + reusable OCR hints |
| `ExtractionStrategy` ABC | `extraction/base.py` | Extension point |

## LLM Pipeline

```
Stage 1: prompts.py → client.complete_structured(TranscriptionResponse)
Pass 1:  pass_1.py → load_or_discover_parse_rules → mdf_parsing_guide.json
Pass 2:  pass_2.py → extract_direct_mdf → page MDF text
Client:  client.py → litellm.completion (retries, reasoning, cache keys)
```

Optional agentic loops run after Stage 1/2 output. Stage 1 automatically allows catastrophic recovery (full-page re-transcription); artifacts are written below each page's `agentic/` directory.

## OCR / VLM Backends

```
ocr/base.py (OCRBackend ABC)
  ├─ mathpix.py, mathpix_convert.py
  ├─ adapters/ (flat export from backend JSON)
  └─ vlm/
       ├─ registry.py (mineru2.5-pro, paddleocr-vl-1.5, glm-ocr)
       ├─ runner.py, completion.py
       └─ local servers: paddle_genai_server.py, glm_vllm_server.py
```

## Evaluation CLIs

| Evaluator | Entry | Metrics |
|-----------|-------|---------|
| `FlatStage1Evaluator` | `evaluate_stage1.py` | char quality, markup, read order, per-language spans |
| `MdfEvaluator` | `evaluate_stage2_mdf.py` | field alignment, marker equiv, similarity, read order |

Supporting: `mdf_lexical_repair.py`, `mdf_stage1_projection.py`, `stage1_task_discovery.py`

## Key Files (by concern)

| Concern | Files |
|---------|-------|
| Run config | `config/run_config.py`, `config/output_paths.py`, `config/prompt_cache.py` |
| Model args | `cli/model_args.py` |
| Page I/O | `utils/pdf_split.py`, `utils/pdf_render.py`, `utils/stage1_input.py` |
| Stage 2 I/O | `utils/stage2_direct_mdf_io.py`, `utils/parse_rules_pages.py` |
| MDF utils | `utils/mdf_export.py`, `utils/mdf_compare.py` |
| Context | `utils/page_context.py`, `utils/dictionary_languages.py` |

## Output Artifacts

```
inference:  <output>/stage-1/<page>/, stage-2/<page>/, mdf_parsing_guide.json
benchmark:  <output>/stage-1/<experiment>/<page>/, stage-2/<experiment>/
per page:   *_usage.json, *_stage2_raw.txt, *.mdf.txt, run_usage.json
```
