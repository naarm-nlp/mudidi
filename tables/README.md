
# Reproducing tables in the paper

`scripts` folder contains scripts to reproduce tables reported in the paper, and
`tables` folder contains the resulting latex tables.

* `dictionary_info_table.R` generates `dictionary_info.tex` which is Table 3 in the paper.
* `dictionary_stats_table.R` generates `dictionary_stats.tex` which is Table 4 in the paper.
* `stage1_summary_table.R` generates `stage1_summary.tex` which is Table 1 in the paper.
* `stage1_script_table.R` generates `stage1_by_script.tex` which is Tables 5-7 in the paper.
* `stage1_dictionary_table.R` generates `stage1_by_dictionary.tex` which is Table 8-12 in the paper.
* `stage1_ocr_hint_table.R` generates `stage1_ocr_hint_per_dictionary.tex` and `stage1_ocr_hint_summary.tex` which are Tables 13-14 in the paper.
* `stage2_summary_table.R` generates `stage2_summary.tex` which is Table 2 in the paper.
* `stage2_dictionary_table.R` generates `stage2_by_dictionary.tex` which is Table 15-16 in the paper.
* `stage2_gold_cheatsheet_table.R` generates `stage2_gold_cheatsheet.tex` which is Table 17 in the paper.
* `stage2_e2e_summary_table.R` generates `stage2_e2e_summary.tex` which is Table 18 in the paper.
* `stage2_no_typography_table.R` generates `stage2_no_typography.tex` which is Table 19 in the paper.
* `inter_annotator_agreement.py` is used to estimate the inter-annotator agreement between two annotators for Stage 2. 
* `dictionary_language_script_words.py` generates `dictionary_language_script_words.tsv` in the `results` folder, which was then used to create our manual fix for the script tags as stated in `dictionary_language_script_words_manual.tsv`.
