#!/usr/bin/env Rscript
#
# Build the Stage 1 OCR-hint ablation LaTeX table, comparing each dictionary's best model/alphabet combo with vs. without an OCR-system hint in the prompt.
#
# Usage:
#   Rscript evaluations/scripts/stage1_ocr_hint_table.R
#
# Output:
#   evaluations/tables/stage1_ocr_hint_per_dictionary.tex

suppressWarnings(suppressMessages({
  script_path <- commandArgs(trailingOnly = FALSE)
  script_arg <- sub("^--file=", "", script_path[grep("^--file=", script_path)])
  repo_root <- if (length(script_arg)) normalizePath(file.path(dirname(script_arg), "..", "..")) else getwd()
}))

hint_csv <- file.path(repo_root, "evaluations", "stage1_flat_ocrhint_best_eval", "stage1_flat_eval_summary.csv")
baseline_csv <- file.path(repo_root, "evaluations", "stage1_flat_per_lang_script_eval", "stage1_flat_eval_summary.csv")
output_dir <- file.path(repo_root, "tables", "tables")
output_path <- file.path(output_dir, "stage1_ocr_hint_per_dictionary.tex")

if (!file.exists(hint_csv)) {
  stop(sprintf(
    "OCR-hint summary CSV not found: %s\nGenerate it first with:\n  uv run mudidi benchmark evaluate stage1 --stage1-output-subdir stage-1-ocr --all-experiments --dataset-dir dataset/mudidi/dictionaries --pred-root outputs/benchmark/stage-1 -o evaluations/stage1_flat_ocrhint_best_eval",
    hint_csv
  ))
}
if (!file.exists(baseline_csv)) {
  stop(sprintf("Baseline (no-hint) summary CSV not found: %s", baseline_csv))
}

hint_results <- read.csv(hint_csv, stringsAsFactors = FALSE)
baseline_results <- read.csv(baseline_csv, stringsAsFactors = FALSE)

metric_cols <- c("TextEdit", "GCER", "WER", "typography_f1", "ReadOrderEdit")
higher_is_better <- c(TextEdit = FALSE, GCER = FALSE, WER = FALSE,
                       typography_f1 = TRUE, ReadOrderEdit = FALSE)

# --- model key (from experiment name prefix) -> short display name used in this table ---
model_display <- c(
  claudeopus47 = "Claude-Opus",
  gemini31pro = "Gemini-Pro",
  gemini3flash = "Gemini-Flash",
  gpt55 = "GPT-5.5"
)

model_key_from_experiment <- function(experiment) {
  # e.g. "claudeopus47_flat_alpha_ocrhint" -> "claudeopus47"
  sub("_flat_.*$", "", experiment)
}

hint_results$model_key <- model_key_from_experiment(hint_results$experiment)
unknown_models <- setdiff(hint_results$model_key, names(model_display))
if (length(unknown_models)) {
  stop(sprintf("Unrecognized model key(s) in OCR-hint experiment names: %s",
               paste(unknown_models, collapse = ", ")))
}

# alphabet is read from run_config.json by the evaluator (authoritative) --
# don't infer it from the "_alpha"/"_noalpha" substring in the experiment name.
hint_results$alphabet_flag <- tolower(trimws(hint_results$alphabet)) == "true"
hint_results$baseline_experiment <- sprintf(
  "%s_flat_%s", hint_results$model_key,
  ifelse(hint_results$alphabet_flag, "alpha", "noalpha")
)

# --- join hint rows to their matching no-hint baseline row (same dictionary + baseline experiment) ---
baseline_keyed <- baseline_results[, c("experiment", "language", metric_cols)]
names(baseline_keyed)[names(baseline_keyed) %in% metric_cols] <- paste0("base_", metric_cols)

merged <- merge(
  hint_results,
  baseline_keyed,
  by.x = c("baseline_experiment", "language"),
  by.y = c("experiment", "language"),
  all.x = TRUE
)
missing_baseline <- merged$language[is.na(merged$base_TextEdit)]
if (length(missing_baseline)) {
  stop(sprintf(
    "No matching no-hint baseline found for: %s",
    paste(sprintf("%s (wanted %s)", missing_baseline,
                   merged$baseline_experiment[is.na(merged$base_TextEdit)]),
          collapse = "; ")
  ))
}
merged <- merged[order(merged$language), ]

fmt <- function(x) sprintf("%.3f", x)

# Display-only correction: the dataset folder / CSVs spell this dictionary
# with an underscore ("Kurdish_Turkish"); it should render with a hyphen.
# Data lookups still key on the original underscore spelling.
display_dictionary_name <- function(x) sub("Kurdish_Turkish", "Kurdish-Turkish", x, fixed = TRUE)

# Bold whichever side of the pair is better, decided on full-precision values;
# exact ties (as seen in practice, e.g. WER 0.027 vs 0.027) are left unbolded.
pair_cells <- function(base_val, hint_val, higher_better) {
  if (base_val == hint_val) {
    return(c(fmt(base_val), fmt(hint_val)))
  }
  base_wins <- if (higher_better) base_val > hint_val else base_val < hint_val
  c(
    if (base_wins) sprintf("\\textbf{%s}", fmt(base_val)) else fmt(base_val),
    if (!base_wins) sprintf("\\textbf{%s}", fmt(hint_val)) else fmt(hint_val)
  )
}

row_line <- function(i) {
  r <- merged[i, ]
  checkmark <- if (isTRUE(r$alphabet_flag)) "\\cmark" else ""
  cells <- unlist(lapply(metric_cols, function(col) {
    pair_cells(r[[paste0("base_", col)]], r[[col]], higher_is_better[[col]])
  }))
  sprintf(
    "%s & %s & %s & %s \\\\",
    display_dictionary_name(r$language), model_display[[r$model_key]], checkmark,
    paste(cells, collapse = " & ")
  )
}

data_lines <- vapply(seq_len(nrow(merged)), row_line, character(1))

# --- Mean row: unweighted mean across all 30 dictionaries; bold whichever
# side (no-hint vs. hint) wins per metric, same pair_cells logic as the
# per-dictionary rows above.
mean_cells <- unlist(lapply(metric_cols, function(col) {
  pair_cells(
    mean(merged[[paste0("base_", col)]]),
    mean(merged[[col]]),
    higher_is_better[[col]]
  )
}))
mean_line <- sprintf("\\textit{Mean} &  &  & %s \\\\", paste(mean_cells, collapse = " & "))

lines <- c(
  "% Auto-generated by evaluations/scripts/stage1_ocr_hint_table.R -- do not edit by hand.",
  "\\section{Stage-1 OCR Assisted Prompting Results}",
  "\\label{sec:app-ocr-asst}",
  "\\begin{table*}[!h]",
  "\\centering",
  "\\scriptsize",
  "\\setlength{\\tabcolsep}{3pt}",
  "\\renewcommand{\\arraystretch}{1.08}",
  "\\begin{adjustbox}{max width=\\textwidth}",
  "\\begin{tabular}{llc cc cc cc cc cc}",
  "\\toprule",
  " & & & \\multicolumn{2}{c}{\\textbf{Edit}} & \\multicolumn{2}{c}{\\textbf{GCER}} & \\multicolumn{2}{c}{\\textbf{WER}} & \\multicolumn{2}{c}{\\textbf{Mrk. F1}} & \\multicolumn{2}{c}{\\textbf{Order}} \\\\",
  "\\cmidrule(lr){4-5}\\cmidrule(lr){6-7}\\cmidrule(lr){8-9}\\cmidrule(lr){10-11}\\cmidrule(lr){12-13}",
  "\\textbf{Dictionary} & \\textbf{Model} & \\textbf{A} & -- & +hint & -- & +hint & -- & +hint & -- & +hint & -- & +hint \\\\",
  "\\midrule",
  data_lines,
  "\\midrule",
  mean_line,
  "\\bottomrule",
  "\\end{tabular}",
  "\\end{adjustbox}",
  "\\caption{Per-dictionary breakdown of the Stage 1 OCR-hint ablation summarised in Table~\\ref{tab:stage1-aggregate-ocr-hint}. Each metric is shown as a paired (without hint, with hint) value. \\textit{Best score per pair is bolded; lower is better except for Markup F1.}}",
  "\\label{tab:stage1-ocr-hint-per-language}",
  "\\end{table*}"
)

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(lines, output_path)

cat(sprintf("Wrote %s (%d dictionaries)\n", output_path, nrow(merged)))

# ============================================================
# Table 2: pooled OCR-hint ablation -- same "best config per dictionary"
# data as above, averaged over all 30 dictionaries into a single with/without
# comparison row, plus a delta row.
# ============================================================

pooled_output_path <- file.path(output_dir, "stage1_ocr_hint_summary.tex")
fmt2 <- function(x) sprintf("%.2f", x)

mean_no_hint <- vapply(metric_cols, function(col) mean(merged[[paste0("base_", col)]]), numeric(1))
mean_hint <- vapply(metric_cols, function(col) mean(merged[[col]]), numeric(1))

# Best-score bolding per metric, decided on full-precision means; exact ties left unbolded.
pooled_pair_cells <- function(no_hint_val, hint_val, higher_better) {
  if (no_hint_val == hint_val) {
    return(c(fmt2(no_hint_val), fmt2(hint_val)))
  }
  no_hint_wins <- if (higher_better) no_hint_val > hint_val else no_hint_val < hint_val
  c(
    if (no_hint_wins) sprintf("\\textbf{%s}", fmt2(no_hint_val)) else fmt2(no_hint_val),
    if (!no_hint_wins) sprintf("\\textbf{%s}", fmt2(hint_val)) else fmt2(hint_val)
  )
}

no_hint_row_cells <- character(0)
hint_row_cells <- character(0)
delta_cells <- character(0)
for (col in metric_cols) {
  pair <- pooled_pair_cells(mean_no_hint[[col]], mean_hint[[col]], higher_is_better[[col]])
  no_hint_row_cells <- c(no_hint_row_cells, pair[1])
  hint_row_cells <- c(hint_row_cells, pair[2])
  delta_cells <- c(delta_cells, sprintf("$%+.2f$", mean_hint[[col]] - mean_no_hint[[col]]))
}

pooled_lines <- c(
  "% Auto-generated by evaluations/scripts/stage1_ocr_hint_table.R -- do not edit by hand.",
  "% ============================================================",
  "% Table 2: Pooled OCR-hint ablation",
  "% For each of the 30 dictionaries, take the strongest LLM+alphabet",
  "% configuration identified in Table 1 and compare with vs. without",
  "% an auxiliary OCR transcript supplied at inference.",
  "% ============================================================",
  "\\begin{table}[!h]",
  "\\centering",
  "\\small",
  "\\setlength{\\tabcolsep}{3pt}",
  "%\\renewcommand{\\arraystretch}{1.1}",
  "\\caption{Stage 1 OCR-hint ablation study, averaged over 30 dictionaries. For each dictionary, we hold fixed the strongest LLM and alphabet configuration from Table~\\ref{tab:stage1-aggregate-alphabet} and compare transcription with vs.\\ without a preliminary OCR transcript supplied to the model. \\textit{Best score per metric is bolded.}}",
  "\\label{tab:stage1-aggregate-ocr-hint}",
  "%\\begin{adjustbox}{max width=\\columnwidth}",
  "\\begin{tabular}{lcrrrrr}",
  "\\toprule",
  "\\textbf{Configuration} & \\textbf{OCR hint} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} & \\textbf{Mrk. F1} & \\textbf{Order} \\\\",
  "\\midrule",
  sprintf("Best LLM + alphabet per language &  & %s \\\\", paste(no_hint_row_cells, collapse = " & ")),
  sprintf("Best LLM + alphabet per language & \\cmark & %s \\\\", paste(hint_row_cells, collapse = " & ")),
  "\\midrule",
  sprintf("$\\Delta$ (with hint $-$ without)  &  & %s \\\\", paste(delta_cells, collapse = " & ")),
  "\\bottomrule",
  "\\end{tabular}",
  "%\\end{adjustbox}",
  "\\end{table}"
)

writeLines(pooled_lines, pooled_output_path)

cat(sprintf("Wrote %s (pooled over %d dictionaries)\n", pooled_output_path, nrow(merged)))
