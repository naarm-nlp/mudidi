#!/usr/bin/env Rscript
#
# Build the Stage 1 script-specific evaluation LaTeX table (Edit/GCER/WER aggregated per writing script), with script labels and noise-row exclusion from the manual language-script TSV.
#
# Usage:
#   Rscript evaluations/scripts/stage1_script_table.R
#
# Output:
#   evaluations/tables/stage1_by_script.tex

suppressWarnings(suppressMessages({
  script_path <- commandArgs(trailingOnly = FALSE)
  script_arg <- sub("^--file=", "", script_path[grep("^--file=", script_path)])
  repo_root <- if (length(script_arg)) normalizePath(file.path(dirname(script_arg), "..", "..")) else getwd()
}))

lang_script_csv <- file.path(
  repo_root, "evaluations", "stage1_flat_per_lang_script_eval",
  "stage1_flat_eval_per_language_script_summary.csv"
)
manual_tsv <- file.path(
  repo_root, "tables", "temporary", "dictionary_language_script_words_manual.tsv"
)
output_dir <- file.path(repo_root, "tables", "tables")
output_path <- file.path(output_dir, "stage1_by_script.tex")

if (!file.exists(lang_script_csv)) {
  stop(sprintf("Per-language-script summary CSV not found: %s", lang_script_csv))
}
if (!file.exists(manual_tsv)) {
  stop(sprintf("Manually-curated language-script TSV not found: %s", manual_tsv))
}

results <- read.csv(lang_script_csv, stringsAsFactors = FALSE)

metric_cols <- c("TextEdit", "GCER", "WER")
missing_cols <- setdiff(c("experiment", "language", "language_script", "gold_word_count", metric_cols), names(results))
if (length(missing_cols)) {
  stop(sprintf("Missing expected column(s) in per-language-script CSV: %s", paste(missing_cols, collapse = ", ")))
}

manual <- read.delim(manual_tsv, stringsAsFactors = FALSE, colClasses = "character")
missing_manual_cols <- setdiff(c("dictionary", "language", "script", "words", "decision"), names(manual))
if (length(missing_manual_cols)) {
  stop(sprintf("Missing expected column(s) in manual language-script TSV: %s", paste(missing_manual_cols, collapse = ", ")))
}
manual$words <- as.integer(manual$words)

# "language_script" is "<Language>-<Script>" (e.g. "Assyrian-Latin",
# "English-Latin"); language names never contain a hyphen, so splitting on
# the first hyphen isolates the language. Join key to the manual TSV is
# (dictionary, language, gold word count) -- see header comment.
results$extracted_language <- sub("-.*$", "", results$language_script)

join_key <- function(dictionary, language, words) paste(dictionary, language, words, sep = "")
manual_key <- join_key(manual$dictionary, manual$language, manual$words)
if (any(duplicated(manual_key))) {
  warning("Manual language-script TSV has duplicate (dictionary, language, words) keys; matches may be ambiguous.")
}
manual_lookup <- setNames(manual$script, manual_key)
manual_decision_lookup <- setNames(manual$decision, manual_key)

results_key <- join_key(results$language, results$extracted_language, results$gold_word_count)
unmatched <- unique(results_key[!(results_key %in% names(manual_lookup))])
if (length(unmatched)) {
  warning(sprintf(
    "%d (dictionary, language, words) triple(s) from the per-language-script CSV have no match in the manual TSV (kept, un-relabeled, not excluded): %s",
    length(unmatched), paste(unmatched, collapse = "; ")
  ))
}

results$script <- ifelse(
  results_key %in% names(manual_lookup),
  manual_lookup[results_key],
  sub("^[^-]+-", "", results$language_script)
)
results$decision <- ifelse(
  results_key %in% names(manual_decision_lookup),
  manual_decision_lookup[results_key],
  ""
)

n_before <- nrow(results)
results <- results[results$decision != "exclude", ]
cat(sprintf("Dropped %d row(s) flagged 'exclude' in the manual TSV (%d remain).\n", n_before - nrow(results), nrow(results)))

# --- Map experiment name -> (display model, alphabet flag, section, row order) ---
# Same mapping as stage1_summary_table.R.
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
    "Experiment(s) present in per-language-script CSV but not in the row map (skipped): %s",
    paste(unmapped_experiments, collapse = ", ")
  ))
}

fmt <- function(x) sprintf("%.2f", x)

# --- Aggregate: macro-average per (script, experiment) across every
# (dictionary, language_script) row that falls under that script; each row
# is one equally-weighted observation, same "equal weight per dictionary"
# convention as the Table 1 summary. ---
build_script_block <- function(script_name) {
  subset_rows <- results[results$script == script_name, ]
  agg <- aggregate(
    subset_rows[, metric_cols],
    by = list(experiment = subset_rows$experiment),
    FUN = function(x) mean(as.numeric(x), na.rm = TRUE)
  )
  block <- merge(row_spec, agg, by = "experiment", all.x = TRUE)
  block <- block[order(block$row_order), ]
  block
}

# --- Support stats: n dictionaries / gold word count / gold grapheme count
# backing each script. These are properties of the gold data, constant across
# experiments, so dedupe to one row per (language, language_script) before
# summing -- otherwise counts would be inflated 15x (once per experiment). ---
script_support <- function(script_name) {
  subset_rows <- results[results$script == script_name, ]
  dedup <- unique(subset_rows[, c("language", "language_script", "gold_word_count", "gold_grapheme_count")])
  list(
    n_dictionaries = length(unique(dedup$language)),
    gold_words = sum(dedup$gold_word_count),
    gold_graphemes = sum(dedup$gold_grapheme_count)
  )
}

all_scripts <- sort(unique(results$script))

# Best-score bolding is scoped per script block (lower is better for all
# three metrics here), decided on full-precision values.
mark_best <- function(block) {
  best_mask <- lapply(metric_cols, function(col) {
    raw <- block[[col]]
    if (all(is.na(raw))) return(rep(FALSE, length(raw)))
    best <- min(raw, na.rm = TRUE)
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

row_block_lines <- function(script_name) {
  block <- build_script_block(script_name)
  best_mask <- mark_best(block)
  support <- script_support(script_name)
  lines <- character(0)
  header_row <- sprintf(
    "\\multicolumn{5}{>{\\columncolor{green!12}}l}{\\textbf{%s} (%d dictionaries, %s words, %s graphemes)}",
    script_name, support$n_dictionaries,
    format(support$gold_words, big.mark = ","),
    format(support$gold_graphemes, big.mark = ",")
  )
  lines <- c(lines, header_row)
  sections_in_order <- unique(block$section)
  for (sec in sections_in_order) {
    lines <- c(lines, sprintf("\\multicolumn{5}{>{\\columncolor{gray!12}}l}{\\emph{%s}}", sec))
    sec_rows <- which(block$section == sec)
    for (i in sec_rows) {
      checkmark <- if (isTRUE(block$alphabet[i])) "\\cmark" else ""
      lines <- c(lines, sprintf(
        "%s & %s & %s & %s & %s",
        block$model[i], checkmark,
        cell(block, best_mask, "TextEdit", i),
        cell(block, best_mask, "GCER", i),
        cell(block, best_mask, "WER", i)
      ))
    }
  }
  lines
}

# --- Pair scripts two-per-row (alphabetical, matching the dictionary-table convention) ---
n_scripts <- length(all_scripts)
pairs <- split(all_scripts, ceiling(seq_len(n_scripts) / 2))

lines <- character(0)
lines <- c(lines,
  "% Auto-generated by evaluations/scripts/stage1_script_table.R -- do not edit by hand.",
  "\\begin{table*}[!hp]",
  "\\centering",
  "\\scriptsize",
  "\\setlength{\\tabcolsep}{2.2pt}",
  "\\renewcommand{\\arraystretch}{1.08}",
  "\\begin{adjustbox}{max width=\\textwidth}",
  "\\begin{tabular}{lc@{\\quad}rrr@{\\qquad}lc@{\\quad}rrr}",
  "\\toprule",
  paste(
    "\\textbf{Model} & \\textbf{Alph.} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} &",
    "\\textbf{Model} & \\textbf{Alph.} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} \\\\"
  ),
  "\\midrule",
  ""
)

for (idx in seq_along(pairs)) {
  pair <- pairs[[idx]]
  left_lines <- row_block_lines(pair[1])
  right_lines <- if (length(pair) == 2) row_block_lines(pair[2]) else NULL

  if (is.null(right_lines)) {
    # Odd script out: pad the right side with empty cells matching left length.
    right_lines <- rep("\\multicolumn{5}{l}{}", length(left_lines))
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
  "\\caption{Stage 1 script-specific evaluation results, aggregated by writing script (e.g. \\textit{Latin} pools every language whose dictionary column is transliterated into Latin script). Edit/GCER/WER only: Markup F1 and read order are page-level structural metrics and do not decompose to a single script. Each shaded script header reports the number of dictionaries, gold word count, and gold grapheme count backing that script's average; many scripts are backed by a single dictionary and should be read accordingly. \\textit{Best scores per script are bolded (lower is better for all three metrics)}.}",
  "\\label{tab:stage1-by-script}",
  "\\end{table*}"
)

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(lines, output_path)

cat(sprintf("Wrote %s (%d scripts, %d rows total)\n", output_path, n_scripts, length(all_scripts)))
