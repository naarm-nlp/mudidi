#!/usr/bin/env Rscript
#
# Build the Stage 2 gold-parse-rules upper-bound LaTeX table, comparing MDF Fields F1 with vs. without the human gold cheat sheet.
#
# Usage:
#   Rscript evaluations/scripts/stage2_gold_cheatsheet_table.R
#
# Output:
#   evaluations/tables/stage2_gold_cheatsheet.tex

suppressWarnings(suppressMessages({
  script_path <- commandArgs(trailingOnly = FALSE)
  script_arg <- sub("^--file=", "", script_path[grep("^--file=", script_path)])
  repo_root <- if (length(script_arg)) normalizePath(file.path(dirname(script_arg), "..", "..")) else getwd()
}))

summary_csv <- file.path(repo_root, "evaluations", "stage2_mdf_lang_script_eval_stage1-gold", "stage2_mdf_eval_summary.csv")
output_dir <- file.path(repo_root, "tables", "tables")
output_path <- file.path(output_dir, "stage2_gold_cheatsheet.tex")

if (!file.exists(summary_csv)) {
  stop(sprintf("Stage 2 summary CSV not found: %s", summary_csv))
}

results <- read.csv(summary_csv, stringsAsFactors = FALSE)

goldcheat_rows <- results[results$language != "__aggregate__" & grepl("_goldcheat$", results$experiment), ]
if (nrow(goldcheat_rows) == 0) {
  stop("No '_goldcheat' experiments found in the summary CSV.")
}

model_display <- c(
  claudeopus47 = "Claude Opus 4.7",
  gemini31pro = "Gemini 3.1 Pro",
  gpt55 = "GPT-5.5",
  qwen3vl235 = "Qwen3-VL-235B"
)
# Short display names for the dictionaries that (so far) have a goldcheat run.
dictionary_display <- c(
  "Evenki-Russian" = "Evenki",
  "Kashmiri-English" = "Kashmiri",
  "Na-English-Chinese-French" = "Na (Mosuo)",
  "Tiri-English" = "Tiri",
  "Nahuatl-French" = "Nahuatl",
  "Iñupiatun Eskimo-English" = "Iñupiatun"
)
# Fixed row order matching the reference table; any dictionary not listed
# here (e.g. a newly added goldcheat run) is appended alphabetically after.
preferred_order <- c("Evenki-Russian", "Kashmiri-English", "Na-English-Chinese-French",
                      "Tiri-English", "Nahuatl-French")

goldcheat_rows$baseline_experiment <- sub("_goldcheat$", "", goldcheat_rows$experiment)
goldcheat_rows$model_key <- sub("_high_mdf_.*$", "", goldcheat_rows$baseline_experiment)
remainder <- sub("^.*_high_mdf_", "", goldcheat_rows$baseline_experiment)
goldcheat_rows$intro_flag <- startsWith(remainder, "intro_")
goldcheat_rows$toolbox_flag <- !endsWith(remainder, "_notoolbox")

unknown_models <- setdiff(goldcheat_rows$model_key, names(model_display))
if (length(unknown_models)) {
  stop(sprintf("Unrecognized model key(s) in goldcheat experiment names: %s",
               paste(unknown_models, collapse = ", ")))
}
unknown_dicts <- setdiff(goldcheat_rows$language, names(dictionary_display))
if (length(unknown_dicts)) {
  stop(sprintf(
    "No short display name mapped for dictionary/dictionaries: %s -- add to dictionary_display in this script.",
    paste(unknown_dicts, collapse = ", ")
  ))
}

baseline_keyed <- results[, c("experiment", "language", "MDF_Fields_F1")]
names(baseline_keyed)[names(baseline_keyed) == "MDF_Fields_F1"] <- "inf_f1"

merged <- merge(
  goldcheat_rows,
  baseline_keyed,
  by.x = c("baseline_experiment", "language"),
  by.y = c("experiment", "language"),
  all.x = TRUE
)
names(merged)[names(merged) == "MDF_Fields_F1"] <- "gold_f1"

missing_baseline <- merged$language[is.na(merged$inf_f1)]
if (length(missing_baseline)) {
  stop(sprintf("No matching non-goldcheat baseline found for: %s", paste(missing_baseline, collapse = ", ")))
}

# Exclude dictionaries whose baseline already rounds to a perfect 1.00 F1.
merged <- merged[round(merged$inf_f1, 2) < 1.00, ]
if (nrow(merged) == 0) {
  stop("All goldcheat dictionaries already have a perfect (rounded) baseline F1 -- nothing to show.")
}

merged$order_key <- match(merged$language, preferred_order)
merged$order_key[is.na(merged$order_key)] <- length(preferred_order) + rank(merged$language[is.na(merged$order_key)])
merged <- merged[order(merged$order_key), ]

fmt <- function(x) sprintf("%.2f", x)

# Bold whichever side of the pair is better (higher F1); exact ties left unbolded.
pair_cells <- function(inf_val, gold_val) {
  if (inf_val == gold_val) {
    return(c(fmt(inf_val), fmt(gold_val)))
  }
  inf_wins <- inf_val > gold_val
  c(
    if (inf_wins) sprintf("\\textbf{%s}", fmt(inf_val)) else fmt(inf_val),
    if (!inf_wins) sprintf("\\textbf{%s}", fmt(gold_val)) else fmt(gold_val)
  )
}

row_line <- function(i) {
  r <- merged[i, ]
  intro_mark <- if (isTRUE(r$intro_flag)) "\\cmark" else ""
  toolbox_mark <- if (isTRUE(r$toolbox_flag)) "\\cmark" else ""
  cells <- pair_cells(r$inf_f1, r$gold_f1)
  sprintf(
    "%s & %s & %s & %s & %s & %s \\\\",
    dictionary_display[[r$language]], model_display[[r$model_key]],
    intro_mark, toolbox_mark, cells[1], cells[2]
  )
}
data_lines <- vapply(seq_len(nrow(merged)), row_line, character(1))

macro_cells <- pair_cells(mean(merged$inf_f1), mean(merged$gold_f1))
macro_line <- sprintf("\\textbf{Macro avg.} &  &  &  & %s & %s \\\\", macro_cells[1], macro_cells[2])

lines <- c(
  "% Auto-generated by evaluations/scripts/stage2_gold_cheatsheet_table.R -- do not edit by hand.",
  "\\begin{table}[!h]",
  "\\centering",
  "\\small",
  "\\caption{Stage~2 gold parse-rules upper bound on dictionaries where the model does not generate a perfect MDF file. Each row uses the per-language best model and ablation setting from Table~\\ref{tab:stage2-mdf-aggregate}, replacing the inferred Pass~1 parse-rules with a human-validated gold parse-rules before Pass~2.}",
  "\\label{tab:stage2-gold-cheat-sheet}",
  "%\\begin{adjustbox}{width=\\columnwidth,center}",
  "\\setlength{\\tabcolsep}{4pt}",
  "\\begin{tabular}{l l cc rr}",
  "\\toprule",
  "\\textbf{Dictionary} & \\textbf{Model} & \\textbf{Intro} & \\textbf{MDF} & \\textbf{Inf. F1} & \\textbf{Gold F1} \\\\",
  "\\midrule",
  data_lines,
  "\\midrule",
  macro_line,
  "\\bottomrule",
  "\\end{tabular}",
  "%\\end{adjustbox}",
  "\\end{table}"
)

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(lines, output_path)

cat(sprintf("Wrote %s (%d dictionaries)\n", output_path, nrow(merged)))
