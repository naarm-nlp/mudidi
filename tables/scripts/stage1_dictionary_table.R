#!/usr/bin/env Rscript
#
# Build the per-dictionary Stage 1 evaluation LaTeX table (Edit/GCER/WER/Markup F1/ReadOrderEdit for each of the 30 dictionaries individually) for each model x alphabet-hint variant.
#
# Usage:
#   Rscript evaluations/scripts/stage1_dictionary_table.R
#
# Output:
#   evaluations/tables/stage1_by_dictionary.tex

suppressWarnings(suppressMessages({
  script_path <- commandArgs(trailingOnly = FALSE)
  script_arg <- sub("^--file=", "", script_path[grep("^--file=", script_path)])
  repo_root <- if (length(script_arg)) normalizePath(file.path(dirname(script_arg), "..", "..")) else getwd()
}))

summary_csv <- file.path(repo_root, "evaluations", "stage1_flat_per_lang_script_eval", "stage1_flat_eval_summary.csv")
output_dir <- file.path(repo_root, "tables", "tables")
output_path <- file.path(output_dir, "stage1_by_dictionary.tex")

if (!file.exists(summary_csv)) {
  stop(sprintf("Stage 1 summary CSV not found: %s", summary_csv))
}

results <- read.csv(summary_csv, stringsAsFactors = FALSE)

metric_cols <- c("TextEdit", "GCER", "WER", "typography_f1", "ReadOrderEdit")
missing_cols <- setdiff(c("experiment", "language", metric_cols), names(results))
if (length(missing_cols)) {
  stop(sprintf("Missing expected column(s) in summary CSV: %s", paste(missing_cols, collapse = ", ")))
}
higher_is_better <- c(TextEdit = FALSE, GCER = FALSE, WER = FALSE,
                       typography_f1 = TRUE, ReadOrderEdit = FALSE)

# --- Map experiment name -> (display model, alphabet flag, section, row order) ---
# Same mapping as stage1_summary_table.R / stage1_script_table.R.
row_spec <- data.frame(
  experiment = c(
    "GLM-OCR-flat_noalpha", "GLM-OCR-flat_alpha",
    "Mathpix-OCR", "MinerU2.5-Pro", "PaddleOCR-VL-1.5",
    "qwen3vl235_flat_noalpha", "qwen3vl235_flat_alpha",
    "claudeopus47_flat_noalpha", "claudeopus47_flat_alpha",
    "gpt55_flat_noalpha", "gpt55_flat_alpha",
    "gemini3flash_flat_noalpha", "gemini3flash_flat_alpha",
    "gemini31pro_flat_noalpha", "gemini31pro_flat_alpha"
  ),
  model = c(
    "GLM-OCR", "GLM-OCR",
    "Mathpix", "MinerU2.5-Pro", "PaddleOCR-VL-1.5",
    "Qwen3-VL-235B", "Qwen3-VL-235B",
    "Claude Opus 4.7", "Claude Opus 4.7",
    "GPT-5.5", "GPT-5.5",
    "Gemini 3 Flash", "Gemini 3 Flash",
    "Gemini 3.1 Pro", "Gemini 3.1 Pro"
  ),
  alphabet = c(
    FALSE, TRUE,
    FALSE, FALSE, FALSE,
    FALSE, TRUE,
    FALSE, TRUE,
    FALSE, TRUE,
    FALSE, TRUE,
    FALSE, TRUE
  ),
  section = c(
    "OCR systems", "OCR systems",
    "OCR systems", "OCR systems", "OCR systems",
    "Vision Language Models", "Vision Language Models",
    "General-purpose LLMs", "General-purpose LLMs",
    "General-purpose LLMs", "General-purpose LLMs",
    "General-purpose LLMs", "General-purpose LLMs",
    "General-purpose LLMs", "General-purpose LLMs"
  ),
  row_order = seq_len(15),
  stringsAsFactors = FALSE
)

unmapped_experiments <- setdiff(unique(results$experiment), row_spec$experiment)
if (length(unmapped_experiments)) {
  warning(sprintf(
    "Experiment(s) present in summary CSV but not in the row map (skipped): %s",
    paste(unmapped_experiments, collapse = ", ")
  ))
}

fmt <- function(x) sprintf("%.2f", x)

# Display-only correction: the dataset folder / CSVs spell this dictionary
# with an underscore ("Kurdish_Turkish"); it should render with a hyphen.
# Data lookups still key on the original underscore spelling.
display_dictionary_name <- function(x) sub("Kurdish_Turkish", "Kurdish-Turkish", x, fixed = TRUE)

build_dictionary_block <- function(dict_name) {
  # Select only the metric columns from `results` -- it has its own
  # "alphabet" column (string "true"/"false") that would otherwise collide
  # with row_spec$alphabet (logical) during merge and get silently renamed
  # to alphabet.x/alphabet.y, leaving the checkmark column blank.
  subset_rows <- results[results$language == dict_name, c("experiment", metric_cols)]
  block <- merge(row_spec, subset_rows, by = "experiment", all.x = TRUE)
  block <- block[order(block$row_order), ]
  block
}

all_dictionaries <- sort(unique(results$language))
missing_dict_rows <- sapply(all_dictionaries, function(d) {
  any(is.na(build_dictionary_block(d)$TextEdit))
})
if (any(missing_dict_rows)) {
  stop(sprintf(
    "Row map references experiment(s) missing for dictionary/dictionaries: %s",
    paste(names(missing_dict_rows)[missing_dict_rows], collapse = ", ")
  ))
}

# Best-score bolding is scoped per dictionary block, decided on full-precision values.
mark_best <- function(block) {
  best_mask <- lapply(metric_cols, function(col) {
    raw <- block[[col]]
    if (all(is.na(raw))) return(rep(FALSE, length(raw)))
    best <- if (higher_is_better[[col]]) max(raw, na.rm = TRUE) else min(raw, na.rm = TRUE)
    !is.na(raw) & raw == best
  })
  names(best_mask) <- metric_cols
  best_mask
}

cell <- function(block, best_mask, col, i) {
  raw <- block[[col]][i]
  if (is.na(raw)) return("--")
  value <- fmt(raw)
  if (isTRUE(best_mask[[col]][i])) sprintf("\\textbf{%s}", value) else value
}

row_block_lines <- function(dict_name) {
  block <- build_dictionary_block(dict_name)
  best_mask <- mark_best(block)
  lines <- character(0)
  lines <- c(lines, sprintf("\\multicolumn{7}{>{\\columncolor{green!12}}l}{\\textbf{%s}}", display_dictionary_name(dict_name)))
  sections_in_order <- unique(block$section)
  for (sec in sections_in_order) {
    lines <- c(lines, sprintf("\\multicolumn{7}{>{\\columncolor{gray!12}}l}{\\emph{%s}}", sec))
    sec_rows <- which(block$section == sec)
    for (i in sec_rows) {
      checkmark <- if (isTRUE(block$alphabet[i])) "\\cmark" else ""
      lines <- c(lines, sprintf(
        "%s & %s & %s & %s & %s & %s & %s",
        block$model[i], checkmark,
        cell(block, best_mask, "TextEdit", i),
        cell(block, best_mask, "GCER", i),
        cell(block, best_mask, "WER", i),
        cell(block, best_mask, "typography_f1", i),
        cell(block, best_mask, "ReadOrderEdit", i)
      ))
    }
  }
  lines
}

# --- Pair dictionaries two-per-row (alphabetical) ---
n_dicts <- length(all_dictionaries)
pairs <- split(all_dictionaries, ceiling(seq_len(n_dicts) / 2))

lines <- character(0)
lines <- c(lines,
  "% Auto-generated by evaluations/scripts/stage1_dictionary_table.R -- do not edit by hand.",
  "\\begin{table*}[!hp]",
  "\\centering",
  "\\scriptsize",
  "\\setlength{\\tabcolsep}{2.2pt}",
  "\\renewcommand{\\arraystretch}{1.08}",
  "\\begin{adjustbox}{max width=\\textwidth}",
  "\\begin{tabular}{lcrrrrr@{\\qquad}lcrrrrr}",
  "\\toprule",
  paste(
    "\\textbf{Model} & \\textbf{Alph.} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} & \\textbf{Mrk. F1} & \\textbf{RO} &",
    "\\textbf{Model} & \\textbf{Alph.} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} & \\textbf{Mrk. F1} & \\textbf{RO} \\\\"
  ),
  "\\midrule",
  ""
)

for (idx in seq_along(pairs)) {
  pair <- pairs[[idx]]
  left_lines <- row_block_lines(pair[1])
  right_lines <- if (length(pair) == 2) row_block_lines(pair[2]) else NULL

  if (is.null(right_lines)) {
    # Odd dictionary out: pad the right side with empty cells matching left length.
    right_lines <- rep("\\multicolumn{7}{l}{}", length(left_lines))
  }

  stopifnot(length(left_lines) == length(right_lines))
  for (i in seq_along(left_lines)) {
    lines <- c(lines, paste0(left_lines[i], " & ", right_lines[i], " \\\\"))
  }

  if (idx != length(pairs)) {
    lines <- c(lines, "\\addlinespace[0.35em]", "\\midrule", "\\addlinespace[0.35em]", "")
  }
}

lines <- c(lines,
  "\\bottomrule",
  "\\end{tabular}",
  "\\end{adjustbox}",
  "\\caption{Stage 1 dictionary-specific evaluation results grouped by dictionary.}",
  "\\end{table*}"
)

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(lines, output_path)

cat(sprintf("Wrote %s (%d dictionaries, %d rows total)\n", output_path, n_dicts, n_dicts))
