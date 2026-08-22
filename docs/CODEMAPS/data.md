<!-- Updated: 2026-07-30 -->

# Data Model & Storage

Pipeline, dataset, and evaluation artifacts are file-based. The local web
dashboard additionally stores run metadata, events, presets, and encrypted
credential records in SQLite; uploaded inputs and generated outputs remain in
managed local directories.

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
| `ExtractionResult` | `extraction_result.py` | Direct MDF text with page provenance |
| `TranscriptionResponse` | `transcription.py` | Stage 1 structured line output |
| `FlatTranscriptionResponse` | `transcription.py` | Flat-mode Stage 1 output |
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
  mdf_parsing_guide.json, mdf_parsing_guide_usage.json
  stage-1/<page_stem>/page_*_stage1.txt
  stage-2/<page_stem>/page_*.mdf.txt, *_stage2_raw.txt, *_usage.json
  stage-{1,2}/<page_stem>/agentic/<stage>/...  # verifier/rewrite audit artifacts
```

### Benchmark

```
<output_dir>/
  stage-1/<experiment>/<page_stem>/...
  stage-2/<experiment>/mdf_parsing_guide.json
  stage-2/<experiment>/<page_stem>/...
```

## Evaluation Outputs (`evaluations/`)

| Directory | Contents |
|-----------|----------|
| `stage1_flat_per_lang_script_eval/` | Stage 1 global and per-language/script metrics |
| `stage2_mdf_lang_script_eval/` | Stage 2 oracle/gold-Stage-1 MDF metrics |
| `stage2_mdf_lang_script_eval_stage1-gold/` | Stage 2 comparison generated from explicitly gold Stage 1 inputs |
| `stage2_mdf_eval_e2e_lexical_repair/` | End-to-end Stage 2 metrics after lexical repair |
| `stage2_mdf_eval_no_typography/` | No-typography Stage 2 comparison |

CSV columns include: dictionary, page, experiment, character/word quality, markup scores, and MDF field metrics. Per-language-script reports include gold word and grapheme counts.
See `evaluations/README.md` for producer scripts and provenance boundaries.

## Config Files

| File | Location | Purpose |
|------|----------|---------|
| `prompts/manifest.json` | `src/mudidi/assets/` | Prompt metadata and external template paths |
| `prompts/stage_1/*.txt` | `src/mudidi/assets/` | Readable Stage 1 prompt templates |
| `prompts/stage_2/*.txt` | `src/mudidi/assets/` | Readable Stage 2 prompt templates |
| `dictionary_languages.yaml` | per-run or dataset | Language pair config |
| `mdf_parsing_guide.json` | output dir | Pass 1 discovered MDF markers |
| `.env` | project root | API keys (GEMINI, OPENROUTER, MATHPIX, …) |

## Stage 1 Eval Cache

`evaluation/stage1/stage1_eval_cache.py` — SHA-256 content fingerprint cache to skip re-evaluation of unchanged predictions.

## Web Dashboard Storage

`web/runs.py` owns the SQLite schema and repository for runs, events, reviews,
and presets. `web/credentials.py` stores encrypted provider credentials in the
same local database. Run-owned input bundles and output artifacts are stored on
disk so the CLI-compatible pipeline continues to operate on normal files.

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
