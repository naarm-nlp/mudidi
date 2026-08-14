# Canonical evaluation results

This directory contains the tracked evaluation reports used for the main
MUDIDI benchmark analysis. Prediction trees remain under `outputs/benchmark/`;
the files here are derived CSV reports and small provenance artifacts.

## Result sets

| Directory | Meaning | Producer |
|---|---|---|
| `statistics/` | Descriptive statistics for the canonical MUDIDI dictionaries, pages, Stage 1 gold text, language-script annotations, typography markup, and Stage 2 MDF fields | `examples/evaluation/run_statistics.sh` |
| `stage1_flat_per_lang_script_eval/` | Current Stage 1 flat-transcription reports, including global and per-language/script metrics | `examples/evaluation/run_stage1_benchmark_per_lang_script_eval.sh` |
| `stage2_mdf_lang_script_eval/` | Current Stage 2 MDF evaluation using oracle/gold Stage 1 inputs, with projection-based per-language/script reports | `examples/evaluation/run_stage2_benchmark_per_lang_script_eval.sh` |
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

## Dataset statistics

`statistics/` is calculated from `dataset/MUDIDI/dictionaries`. The current
snapshot covers 31 dictionary directories, 85 Stage 1 gold pages, and 10 Stage
2 MDF pages. Stage 1 pages are counted from `*_stage1_GOLD_flat.txt` files;
Stage 2 pages are counted from `*.mdf.txt` files. A page present in both stages
contributes once to each count.

The directory contains three reports:

| File | Contents |
|---|---|
| `dictionary_statistics.json` | Comprehensive schema-versioned report with dataset-level totals, per-dictionary aggregates, per-page records, page artifact flags, per-page language-script grapheme mappings, typography counts, and MDF tag counts |
| `dictionary_statistics_per_language_script_detailed.csv` | One row for each observed `language`, `page`, and `language_script` combination in Stage 1 gold |
| `dictionary_statistics_summary.csv` | One row per dictionary language identifier, pooled across its pages and language scripts |

Here, `language` is the dictionary directory identifier, such as
`Chukchi-Russian`. `language_script` comes from the validated Stage 1
`*_lang.json` associated with the page's `*_stage1_GOLD_flat.txt`; Stage 2
language-projection files are not used.

### Detailed CSV columns

| Column | Meaning |
|---|---|
| `language` | Dictionary directory identifier |
| `page` | Page identifier, such as `page_26` |
| `language_script` | Stage 1 language-script span label, such as `Chukchi-Cyrillic Extended` |
| `gold_grapheme_count` | Evaluation-normalized gold graphemes assigned to that page and language-script label |

### Summary CSV columns

| Column | Meaning |
|---|---|
| `language` | Dictionary directory identifier |
| `stage1_page_count` | Pages containing a `*_stage1_GOLD_flat.txt` file |
| `stage2_page_count` | Pages containing a `*.mdf.txt` file |
| `rows` | Stage 1 TSV body rows, excluding `header` and `footer` rows, summed across pages with an available TSV |
| `columns` | Distinct non-metadata Stage 1 TSV `column_id` values across the dictionary, such as `single`, `left`, `center`, and `right` |
| `gold_grapheme_count` | Sum of all detailed language-script grapheme counts for the dictionary |
| `bold_tag_count` | Exact opening `<b>` occurrences in raw Stage 1 gold flat text, summed across pages |
| `italic_tag_count` | Exact opening `<i>` occurrences in raw Stage 1 gold flat text, summed across pages |
| `tags` | Total Stage 2 MDF field-marker occurrences across available pages |
| `unique_tags` | Number of distinct Stage 2 MDF field markers across the dictionary |
| `tag_counts` | JSON object mapping each MDF marker to its pooled occurrence count |

Gold graphemes use the same projection pipeline as Stage 1 per-language
evaluation: markup is removed; text is normalized and case-folded; whitespace
and Unicode punctuation are removed; the remaining text is segmented into
Unicode grapheme clusters; and the validated raw-text spans are projected onto
those clusters. Graphemes labelled `meta` or `space` are excluded. Consequently,
the detailed counts sum exactly to each dictionary's summary
`gold_grapheme_count`. Blank CSV metrics and JSON `null` values mean that the
required Stage 1 or Stage 2 source artifact is unavailable; they do not mean a
measured zero. In particular, rows and columns cannot be recovered from flat
text after its table structure has been removed. A Stage 1 page without a TSV
therefore has `null` page-level rows and columns. Dictionary summaries pool
rows and columns from the TSV-backed pages that remain, or stay blank if the
dictionary has no Stage 1 TSV files at all.

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

Regenerate the dataset statistics with:

```bash
bash examples/evaluation/run_statistics.sh
```

Set `DATASET_DIR` or `OUTPUT_DIR` to run against another compatible dictionary
tree or write the reports elsewhere.

## Scope boundary

Outputs whose experiment or path name contains `agentic` come from side
experiments and are deliberately excluded from the canonical evaluation set.
Debug, partial, spot-check, and archived runs are also non-canonical. Local
obsolete results may be kept under ignored archive directories, but should not
be added back to this root result inventory.
