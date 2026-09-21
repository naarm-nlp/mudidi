
# Reproducing tables in the paper

The `scripts` folder contains Python scripts that reproduce the paper's LaTeX
tables from the checked-in evaluation artifacts. By default, each generator
finds the repository root from its own location and writes to `tables/tables`.
Run a generator from any working directory with:

```console
uv run python tables/scripts/stage1_summary_table.py
```

For isolated generation, pass `--repo-root PATH` and `--output-dir PATH`.

* `dictionary_info_table.py` generates `dictionary_info.tex` (Table 3).
* `dictionary_stats_table.py` generates `dictionary_stats.tex` (Table 4).
* `stage1_summary_table.py` generates `stage1_summary.tex` (Table 1).
* `stage1_script_table.py` generates `stage1_by_script.tex` (Tables 5--7).
* `stage1_dictionary_table.py` generates `stage1_by_dictionary.tex` (Tables 8--12).
* `stage1_ocr_hint_table.py` generates `stage1_ocr_hint_per_dictionary.tex` and `stage1_ocr_hint_summary.tex` (Tables 13--14). This is the fixed Gemini 3.1 Pro + alphabet ablation; only the OCR-hint flag changes.
* `stage2_summary_table.py` generates `stage2_summary.tex` (Table 2).
* `stage2_dictionary_table.py` generates `stage2_by_dictionary.tex` (Tables 15--16).
* `stage2_gold_cheatsheet_table.py` generates `stage2_gold_cheatsheet.tex` (Table 17).
* `stage2_e2e_summary_table.py` generates `stage2_e2e_summary.tex` (Table 18).
* `stage2_no_typography_table.py` generates `stage2_no_typography.tex` (Table 19).
* `inter_annotator_agreement.py` estimates Stage 2 inter-annotator agreement.
* `dictionary_language_script_words.py` generates `dictionary_language_script_words.tsv` in `tables/results`; that file was used to create the manually corrected `dictionary_language_script_words_manual.tsv`.
