<!-- Generated: 2026-07-11 | Files scanned: 255 | Token estimate: ~850 -->

# Data Model & Storage

> File-based storage only — no database. All artifacts are JSON, TXT, TSV, CSV, YAML, or images.

## Dataset Layout (`dataset/MUDIDI/`)

```
dataset/MUDIDI/
  dictionaries/<Lang-Pair>/
    Dictionary pages/          # source page images
    Alphabet list/               # alphabet reference
    Stage 1 Gold OCR/            # per-page gold transcripts
      page_<N>/
        page_<N>_stage1.txt      # flat gold
        page_<N>_stage1.tsv      # column gold (optional)
        page_<N>_language_spans.json  # per-lang spans
    Stage 2 Gold MDF/            # per-page MDF gold (where present)
  parquet/<slug>/                # parquet exports per dictionary
```

~35 language pairs (Chukchi-Russian, Japanese-English, Malay-English, …).

## Core Schemas (`schemas/`)

| Schema | File | Purpose |
|--------|------|---------|
| `DictionaryEntry` | `entry.py` | Stage 2 JSON row (main/subentry/sense) |
| `DictionaryPage` | `entry.py` | Page container with `mdf_text`, entries |
| `TranscriptionResponse` | `entry.py` | Stage 1 structured line output |
| `FlatTranscriptionResponse` | `entry.py` | Flat-mode Stage 1 output |
| `FieldMapPrompt` | `field_map.py` | MDF field mapping for Pass 2 |
| `DictionaryMarkerCheatsheet` | `field_cheatsheet.py` | Pass 1 marker discovery result |
| `DictionaryLanguagesConfig` | `dictionary_languages.py` | Source/target language YAML |
| `PageLanguageMap` | `language_span.py` | Char-level language/script spans |
| `OCRPageResult` | `ocr_result.py` | OCR block/line/bbox structure |

## Run Output Layout

### Inference

```
<output_dir>/
  run_config.json, run_manifest.json, run_usage.json
  parse-rules.json, parse-rules_usage.json
  stage-1/<page_stem>/page_*_stage1.txt
  stage-2/<page_stem>/page_*.mdf.txt, *_stage2_raw.txt, *_usage.json
  stage-{1,2}/<page_stem>/agentic/<stage>/...  # verifier/rewrite audit artifacts
```

### Benchmark

```
<output_dir>/
  stage-1/<experiment>/<page_stem>/...
  stage-2/<experiment>/parse-rules.json
  stage-2/<experiment>/<page_stem>/...
```

## Evaluation Outputs (`evaluations/`)

| Directory | Contents |
|-----------|----------|
| `stage1_flat_eval/` | Global Stage 1 summary/detailed CSV |
| `stage1_flat_per_lang-script_eval/` | Per-language/script Stage 1 metrics |
| `stage2_mdf_eval/` | Stage 2 MDF metrics |
| `stage2_mdf_eval_e2e/` | End-to-end Stage 2 eval |
| `stage2_mdf_per_lang-script_eval/` | Per-language Stage 2 |
| `stage2_mdf_stage1_lang_projection/` | Stage 1→Stage 2 projection eval |

CSV columns include: dictionary, page, experiment, character/word quality, markup scores, and MDF field metrics. Per-language-script reports include gold word and grapheme counts.

## Config Files

| File | Location | Purpose |
|------|----------|---------|
| `PROMPT.json` | `assets/` (packaged) | LLM prompt templates |
| `dictionary_languages.yaml` | per-run or dataset | Language pair config |
| `parse-rules.json` | output dir | Pass 1 discovered MDF markers |
| `.env` | project root | API keys (GEMINI, OPENROUTER, MATHPIX, …) |

## Stage 1 Eval Cache

`evaluation/stage1/stage1_eval_cache.py` — SHA-256 content fingerprint cache to skip re-evaluation of unchanged predictions.

## Data Relationships

```
Dictionary (Lang-Pair)
  └─ Pages (page_<N>)
       ├─ Stage 1 gold transcript (.txt/.tsv)
       ├─ Stage 1 language spans (.json)
       ├─ Stage 2 gold MDF (.mdf.txt)
       └─ Predictions (under outputs/<experiment>/)
            └─ Eval reports join pred ↔ gold by (dict, page, experiment)
```

## Migration

`scripts/migrate_legacy_outputs.py` — converts old output directory layouts to current schema.
