
# Reproducing tables in the paper

The `scripts` folder contains Python scripts that reproduce the paper's LaTeX
tables from the checked-in evaluation artifacts. By default, each generator
finds the repository root from its own location and writes to `tables/tables`.
Run a generator from any working directory with:

```console
uv run python tables/scripts/stage1_summary_table.py
```

For isolated generation, pass `--repo-root PATH` and `--output-dir PATH`.

* `dictionary_info_table.py` generates `dictionary_info.tex`.
* `dictionary_stats_table.py` generates `dictionary_stats.tex`.
* `stage1_summary_table.py` generates `stage1_summary.tex` from each
  experiment report's terminal pooled `__aggregate__` record.
* `stage1_script_table.py` generates the seven language-script tables in
  `stage1_by_script.tex` from the detailed count-level Stage 1 results.
* `stage1_dictionary_table.py` generates `stage1_by_dictionary.tex`.
* `stage1_ocr_hint_table.py` generates
  `stage1_ocr_hint_per_dictionary.tex` and `stage1_ocr_hint_summary.tex`.
  Per-dictionary rows come from the summaries; aggregate rows come from the
  reports' pooled `__aggregate__` records.
* `stage2_summary_table.py` generates `stage2_summary.tex`.
* `stage2_dictionary_table.py` generates `stage2_by_dictionary.tex`.
* `stage2_gold_cheatsheet_table.py` generates
  `stage2_gold_cheatsheet.tex`.
* `stage2_e2e_summary_table.py` generates `stage2_e2e_summary.tex`.
* `stage2_no_typography_table.py` generates `stage2_no_typography.tex`.
  All Stage 2 result tables use Entry F1 rather than the obsolete record
  recall labelled as `Record_Accuracy`.
* `inter_annotator_agreement.py` estimates Stage 2 inter-annotator agreement.
* `dictionary_language_script_words.py` generates `dictionary_language_script_words.tsv` in `tables/results`; that file was used to create the manually corrected `dictionary_language_script_words_manual.tsv`.
