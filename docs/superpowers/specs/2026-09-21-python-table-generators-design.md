# Python Table Generators Migration Design

> **Superseded on 2026-09-22.** This document records the initial R-to-Python
> migration. The subsequent MUDIDI results migration changed the scientific
> inputs and contracts: Stage 1 aggregate tables now consume the terminal
> pooled `__aggregate__` report records, the language-script tables aggregate
> detailed edit/count totals, and Stage 2 uses `Entry_F1` throughout. References
> below to macro means, `Record_Accuracy`, or leaving the evaluator unchanged
> describe the earlier migration and are retained only as historical context.

## Goal

Replace every R-based paper-table generator under `tables/scripts/` with Python while preserving the existing quantitative output contracts, except for the explicitly corrected Stage 1 OCR-hint protocol.

The migration makes table generation reproducible in the repository's existing Python toolchain, removes the stale `tables/temporary/` dependency, and keeps each table independently regenerable.

## Scope

The following generators move from `.R` to same-stem `.py` scripts:

- `dictionary_info_table`
- `dictionary_stats_table`
- `stage1_summary_table`
- `stage1_script_table`
- `stage1_dictionary_table`
- `stage1_ocr_hint_table`
- `stage2_summary_table`
- `stage2_dictionary_table`
- `stage2_gold_cheatsheet_table`
- `stage2_e2e_summary_table`
- `stage2_no_typography_table`

`dictionary_language_script_words.py` remains the producer for `tables/results/dictionary_language_script_words.tsv`. `inter_annotator_agreement.py` is not a table generator and is outside this migration.

## Explicit exclusions

- Do not modify `overleaf-paper/latex/acl_revision_v2.tex`.
- Do not refresh or re-evaluate manuscript narrative claims.
- Do not change the Stage 2 evaluator or regenerate Stage 2 evaluation CSVs.
- Do not change table layout, labels, captions, ordering, rounding, or bolding except where the corrected OCR-hint protocol requires different copy or values.
- Do not add a compatibility `tables/temporary/` path.

## Architecture

Keep one directly executable Python script per table generator. This preserves the existing operational boundary: contributors can regenerate one table without invoking unrelated generators.

Add a small shared module under `tables/scripts/` for behavior genuinely common to multiple scripts:

- repository-root resolution from `__file__`
- default and overridden output-directory resolution
- CSV and TSV loading
- required-column validation
- unique-key validation
- atomic UTF-8 text output
- common model display names and condition parsing where the contracts are identical

The shared module must not become a generic table framework. Table selection, aggregation, ordering, LaTeX layout, captions, and labels remain in the owning scripts because those policies differ materially.

Every script has:

- typed functions
- import-safe behavior
- a `main()` entry point
- `if __name__ == "__main__": main()`
- `argparse` support for `--repo-root` and `--output-dir`
- actionable validation errors before writing output

The default invocation remains simple:

```bash
uv run python tables/scripts/stage1_summary_table.py
```

## Input and output contracts

### Dictionary metadata

`dictionary_info_table.py` reads:

- `tables/results/dictionary_language_script_words_manual.tsv`

It preserves the existing hand-authored linguistic metadata, exclusions, ordering, row text, caption, and label. The tracked results path replaces the removed `tables/temporary/` path.

`dictionary_stats_table.py` reads:

- `evaluations/statistics/dictionary_statistics_summary.csv`

It preserves current null rendering, integer formatting, ordering, caption, and label.

### Stage 1

`stage1_summary_table.py`, `stage1_dictionary_table.py`, and `stage1_script_table.py` preserve the current experiment allowlists, section ordering, metric directions, aggregation, rounding, bolding, pagination, captions, and labels.

`stage1_script_table.py` reads the manual mapping only from:

- `tables/results/dictionary_language_script_words_manual.tsv`

Its ambiguous `(dictionary, language, words)` join must be validated. Exact duplicate lookup keys are allowed only when every matching manual row has the same effective script and decision; conflicting duplicate mappings fail. This prevents silent first-row selection.

### Fixed OCR-hint protocol

`stage1_ocr_hint_table.py` implements one protocol only:

- baseline experiment: `gemini31pro_flat_alpha`
- OCR-hint experiment: `gemini31pro_flat_alpha_ocr`
- alphabet enabled for every row
- 30 dictionaries on both sides

Inputs:

- baseline: `evaluations/stage1_flat_per_lang_script_eval/stage1_flat_eval_summary.csv`
- hint: `evaluations/stage1_flat_per_lang_script_eval/stage1_flat_eval_ocr_hint_summary.csv`

The generator rejects unexpected experiment identifiers, duplicate dictionary rows, missing dictionaries, extra dictionaries, disabled alphabet flags, disabled OCR-hint flags in hint rows, or enabled OCR-hint flags in baseline rows.

It emits both outputs from validated in-memory data:

- `stage1_ocr_hint_per_dictionary.tex`
- `stage1_ocr_hint_summary.tex`

The per-dictionary table keeps three-decimal metrics. The aggregate table keeps the manuscript-compatible fixed-configuration description and three-decimal means/deltas so small effects are not erased by two-decimal rounding. Bolding is decided from full-precision values; exact ties remain unbolded.

The two OCR files are rendered completely before either is replaced. Output writes are atomic per file, so a rendering or validation failure leaves tracked files unchanged.

### Stage 2

The five Stage 2 Python generators preserve existing source CSV selection, experiment parsing, aggregate and per-dictionary ordering, condition semantics, rounding, captions, and labels.

The generated files retain `Ent. Acc.` because the current CSV column is `Record_Accuracy`. The migration does not relabel those current values as Entry F1.

## Stage 2 metric decision

The paper's prose defines entry matching with TP, FP, and FN and therefore should ultimately report **Entry F1**.

The current implementation exposes `MdfPageMetrics.record_accuracy` as `self.record.recall`, or $TP/(TP+FN)$. The current `Record_Accuracy` CSV values are therefore entry recall, not conventional accuracy and not F1. A correct Entry F1 cutover requires a separate evaluator change and regeneration of every Stage 2 result set and derived table. This migration preserves current Stage 2 values and labels to avoid silently changing scientific results.

## Error handling

Each generator validates all required inputs before producing output. Failures name:

- the missing path or column
- the unexpected or duplicate key
- the missing/extra experiment, model, condition, or dictionary
- the output target when writing fails

No script catches broad exceptions or continues with partial data. Numeric parsing failures retain the source file and row context.

## Output compatibility

Before deleting the R scripts, capture their outputs from the tracked inputs using a temporary compatibility mapping for the two stale manual-TSV paths.

For the ten non-OCR generators, Python output must match the existing LaTeX content except for the first-line generator provenance changing from `.R` to `.py`. Any other difference requires explicit review and correction before the R source is removed.

For the OCR generator, expected differences are:

- fixed Gemini 3.1 Pro plus alphabet row selection
- current values from `stage1_flat_eval_ocr_hint_summary.csv`
- fixed-protocol caption/configuration text
- aggregate means and deltas at three decimal places
- better-score bolding implied by those values
- `.py` provenance

## Tests

Add focused pytest coverage under `tests/tables/`.

### Shared behavior

- missing required columns fail before output
- duplicate unique keys fail with source context
- atomic writes produce complete UTF-8 files
- `--repo-root` and `--output-dir` permit isolated generation

### OCR behavior

- only `gemini31pro_flat_alpha` and `gemini31pro_flat_alpha_ocr` are accepted
- all 30 dictionaries are required on both sides
- alphabet and OCR-hint flags match the protocol
- joins are dictionary-exact
- means and deltas use the 30 dictionary rows
- metric-direction bolding and exact ties are correct
- both output files remain absent/unchanged on validation failure

### End-to-end generator contract

Run every Python generator against the tracked evaluation inputs with a temporary output directory. Compare every generated file byte-for-byte with the committed `tables/tables/*.tex` artifact.

This contract catches ordering, rounding, caption, label, and LaTeX escaping drift without modifying the tracked outputs during tests.

## Documentation and clean cutover

Update `tables/README.md` to name `.py` commands and current paths. Update generated provenance comments to `tables/scripts/<name>.py`.

Delete all 11 superseded `.R` files after Python parity passes. Do not retain wrappers, aliases, or deprecated entry points.

## Verification

1. Establish the clean baseline: full project suite passes before edits.
2. Run focused table-generator tests during implementation.
3. Generate all tables into a temporary directory and compare with committed artifacts.
4. Regenerate `tables/tables/` through the Python scripts.
5. Review every output diff against the compatibility rules above.
6. Run Ruff on the new Python modules and tests.
7. Run the complete project test suite.
8. Commit the verified migration on its feature branch.

## Acceptance criteria

- No `.R` table generator remains under `tables/scripts/`.
- Each former R generator has a directly executable Python replacement.
- Shared code is limited to actual cross-generator contracts.
- All scripts run through the repository's Python environment without R.
- Both manual-TSV consumers read `tables/results/dictionary_language_script_words_manual.tsv`.
- OCR-hint outputs use fixed Gemini 3.1 Pro plus alphabet for all 30 dictionaries.
- Ten non-OCR outputs differ from their R versions only in generator provenance.
- All 12 committed LaTeX artifacts are reproducible byte-for-byte by the Python generators.
- Focused generator tests, Ruff, and the complete project suite pass.
- The manuscript and Stage 2 evaluator remain unchanged.