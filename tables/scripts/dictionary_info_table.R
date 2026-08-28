#!/usr/bin/env Rscript
#
# Emit the hand-authored dictionary/language/script metadata LaTeX table, with the Script column computed from the manually curated language-script TSV.
#
# Usage:
#   Rscript evaluations/scripts/dictionary_info_table.R
#
# Output:
#   evaluations/tables/dictionary_info.tex

suppressWarnings(suppressMessages({
  # Non-UTF-8-locale Rscript sessions can mis-handle string literals that mix
  # raw UTF-8 bytes (e.g. Kannada/Cyrillic/Devanagari sample characters) with
  # \uXXXX escapes, silently corrupting them into mojibake on write.
  for (loc in c("en_US.UTF-8", "C.UTF-8")) {
    if (suppressWarnings(Sys.setlocale("LC_CTYPE", loc)) != "") break
  }
  script_path <- commandArgs(trailingOnly = FALSE)
  script_arg <- sub("^--file=", "", script_path[grep("^--file=", script_path)])
  repo_root <- if (length(script_arg)) normalizePath(file.path(dirname(script_arg), "..", "..")) else getwd()
}))

output_dir <- file.path(repo_root, "tables", "tables")
output_path <- file.path(output_dir, "dictionary_info.tex")

manual_tsv <- file.path(repo_root, "tables", "temporary", "dictionary_language_script_words_manual.tsv")
if (!file.exists(manual_tsv)) {
  stop(sprintf("Manually-curated language-script TSV not found: %s", manual_tsv))
}
manual <- read.delim(manual_tsv, stringsAsFactors = FALSE, colClasses = "character")
manual$words <- as.integer(manual$words)
manual <- manual[manual$decision != "exclude", ]

# Maps each row's display "source" label to the vernacular-language name used
# in the manual TSV's "language" column -- these mostly agree, but a few
# display labels are parenthesized alternate names or differ in spelling
# (e.g. "Bengali" here vs. "Bengalese" in the TSV).
source_language_lookup <- c(
  "Assyrian" = "Assyrian",
  "Bengali" = "Bengalese",
  "Canala (Xârâcùù)" = "Canala",
  "Chepang" = "Chepang",
  "Chukchi" = "Chukchi",
  "Circassian (Adyghe)" = "Circassian",
  "Efik" = "Efik",
  "Evenki" = "Evenki",
  "Georgian" = "Georgian",
  "Gojri" = "Gojri",
  "Greek" = "Greek",
  "Gujarati" = "Gujarati",
  "Iñupiatun Eskimo" = "Iñupiatun Eskimo",
  "Japanese" = "Japanese",
  "Kashmiri" = "Kashmiri",
  "Khmer (Cambodian)" = "Khmer",
  "Malay" = "Malay",
  "Na (Mosuo)" = "Na",
  "Nahuatl" = "Nahuatl",
  "Punjabi" = "Punjabi",
  "Reel" = "Reel",
  "Ritharngu" = "Ritharngu",
  "Sanskrit" = "Sanskrit",
  "Shilluk" = "Shilluk",
  "Syriac" = "Syriac",
  "Telugu" = "Telugu",
  "Thai" = "Thai",
  "Tiri (Grand Couli)" = "Tiri",
  "Vernacular Syriac" = "Vernacular Syriac",
  "Yiddish" = "Yiddish"
)

scripts_for_source <- function(source_label) {
  language_name <- source_language_lookup[[source_label]]
  if (is.null(language_name)) {
    stop(sprintf("No manual-TSV language mapping for source label: %s", source_label))
  }
  subset_rows <- manual[manual$language == language_name, ]
  if (!nrow(subset_rows)) {
    stop(sprintf("No non-excluded manual-TSV rows found for language: %s", language_name))
  }
  subset_rows <- subset_rows[order(-subset_rows$words), ]
  paste(subset_rows$script, collapse = ", ")
}

rows <- list(
  list(source = "Assyrian", target = "English", j20 = "0", egids = "10", family = "Afro-Asiatic", area = "Eurasia", characters = "{\\cuneiformfont \\char\"12362\\char\"12154\\char\"12072}", citation = "Williams \\& Northgate, 1868"),
  list(source = "Bengali", target = "English", j20 = "3", egids = "1", family = "Indo-European", area = "Eurasia", characters = "{\\bengalifont ত্ম ত জ্ঞ}", citation = "Mendies, 1828"),
  list(source = "Canala (Xârâcùù)", target = "English", j20 = "0", egids = "6a", family = "Austronesian", area = "Papunesia", characters = "{\\ipafont mʷ ã ɨ}", citation = "Grace, 1975"),
  list(source = "Chepang", target = "English", j20 = "0", egids = "6b", family = "Sino-Tibetan", area = "Eurasia", characters = "{\\ipafont ŋ a ʔ}", citation = "Caughley, 2000"),
  list(source = "Chukchi", target = "Russian", j20 = "0", egids = "6b", family = "Chukotko-Kamchatkan", area = "Eurasia", characters = "{\\cyrillicfont қ ӈ ԓ}", citation = "Inenlikei, 1982"),
  list(source = "Circassian (Adyghe)", target = "English, Turkish", j20 = "1", egids = "5", family = "Northwest Caucasian", area = "Eurasia", characters = "{\\arabicfont ش و نِه}", citation = "Loewe, 1854"),
  list(source = "Efik", target = "English", j20 = "0", egids = "3", family = "Atlantic-Congo", area = "Africa", characters = "{\\ipafont ö ñ ë}", citation = "Hugh, 1886"),
  list(source = "Evenki", target = "Russian", j20 = "0", egids = "6b", family = "Tungusic", area = "Eurasia", characters = "{\\cyrillicfont ӯ э̄ н}", citation = "Vasilevish, 1958"),
  list(source = "Georgian", target = "Russian", j20 = "3", egids = "1", family = "Kartvelian", area = "Eurasia", characters = "{\\geofont ე ს თ}", citation = "Kankava, 2001 (3rd ed)"),
  list(source = "Gojri", target = "English, Hindi", j20 = "0", egids = "5", family = "Indo-European", area = "Eurasia", characters = "{\\devanagarifont सा र णू}", citation = "Anjum \\& Sadiq, 2021"),
  list(source = "Greek", target = "English", j20 = "3", egids = "1", family = "Indo-European", area = "Eurasia", characters = "{\\greekfont ξ λ ψ}", citation = "Kyriakidēs, 1892"),
  list(source = "Gujarati", target = "English", j20 = "1", egids = "2", family = "Indo-European", area = "Eurasia", characters = "{\\gujaratifont ગ બ ત્તી}", citation = "Edalji, 1863"),
  list(source = "Iñupiatun Eskimo", target = "English", j20 = "1", egids = "8a", family = "Eskimo-Aleut", area = "North America", characters = "u t m", citation = "Seiler, 2012"),
  list(source = "Japanese", target = "English", j20 = "5", egids = "1", family = "Japonic", area = "Eurasia", characters = "{\\japanesefont フ ナ ド}", citation = "Hepburn, 1886"),
  list(source = "Kashmiri", target = "English", j20 = "1", egids = "4", family = "Indo-European", area = "Eurasia", characters = "{\\arabicfont کہِ لٹہِ کٹَ}", citation = "Chaltra, 1922"),
  list(source = "Khmer (Cambodian)", target = "English", j20 = "1", egids = "1", family = "Austroasiatic", area = "Eurasia", characters = "{\\khmerfont ផ្ អើ ល}", citation = "ICC, 2012"),
  list(source = "Malay", target = "English", j20 = "3", egids = "3", family = "Austronesian", area = "Eurasia", characters = "{\\arabicfont يا غ قر}", citation = "Howison, 1801"),
  list(source = "Na (Mosuo)", target = "English, Chinese, French", j20 = "0", egids = "6b", family = "Sino-Tibetan", area = "Eurasia", characters = "{\\ipafont ˩ ɕ ˧}", citation = "Michaud \\& Galliot, 2018"),
  list(source = "Nahuatl", target = "French", j20 = "1", egids = "6a/b", family = "Uto-Aztecan", area = "North America", characters = "{\\ipafont Ç O T}", citation = "Siméon, 1885"),
  list(source = "Punjabi", target = "English", j20 = "2", egids = "2", family = "Indo-European", area = "Eurasia", characters = "{\\gurmukhifont ਕੁ ਚਾ ਰੀ}", citation = "Janvier, 1854"),
  list(source = "Reel", target = "English", j20 = "0", egids = "6a", family = "Nilotic", area = "Africa", characters = "{\\ipafont ɛ̈ ŋ ä}", citation = "Cien et al., 2015"),
  list(source = "Ritharngu", target = "English", j20 = "0", egids = "8b", family = "Pama-Nyungan", area = "Australia", characters = "{\\ipafont ṛ č ḍ}", citation = "Heath, 1980"),
  list(source = "Sanskrit", target = "English", j20 = "2", egids = "9", family = "Indo-European", area = "Eurasia", characters = "{\\devanagarifont क झ त}", citation = "Yates, 1846"),
  list(source = "Shilluk", target = "English", j20 = "0", egids = "5", family = "Nilotic", area = "Africa", characters = "{\\ipafont ä r ø}", citation = "Ayoker \\& Kur, 2016"),
  list(source = "Syriac", target = "English", j20 = "0", egids = "9", family = "Afro-Asiatic", area = "Eurasia", characters = "{\\syriacfont ܡ ܪܵ ܐ}", citation = "Yohannan, 1900"),
  list(source = "Telugu", target = "English", j20 = "1", egids = "2", family = "Dravidian", area = "Eurasia", characters = "{\\telugufont అ వ ష్టం}", citation = "Sankaranarayana, 1900"),
  list(source = "Thai", target = "Russian", j20 = "3", egids = "1", family = "Tai-Kadai", area = "Eurasia", characters = "{\\thaifont วั ฒ นะ}", citation = "Morev, 1964"),
  list(source = "Tiri (Grand Couli)", target = "English", j20 = "0", egids = "7", family = "Austronesian", area = "Papunesia", characters = "{\\ipafont ɔ̃ bʷ ŋ}", citation = "Grace, 1976"),
  list(source = "Vernacular Syriac", target = "Kurdish, Turkish, English", j20 = "0", egids = "6b", family = "Afro-Asiatic", area = "Eurasia", characters = "{\\syriacfont ܬ ܫܸ ܡܲ}", citation = "Maclean, 1901"),
  list(source = "Yiddish", target = "English", j20 = "1", egids = "9", family = "Indo-European", area = "Eurasia", characters = "{\\hebrewfont ע ן פ}", citation = "Harkavy, 1901")
)

rows <- lapply(rows, function(r) {
  r$script <- scripts_for_source(r$source)
  r
})

# Column order: Source, Target, J20, EGIDS, Language family, Area, Script, Characters, Citation.
row_line <- function(r) {
  line <- paste(
    r$source, r$target, r$j20, r$egids, r$family, r$area, r$script, r$characters, r$citation,
    sep = " & "
  )
  paste0(line, " \\\\")
}

data_lines <- vapply(rows, row_line, character(1))

lines <- c(
  "% Auto-generated by evaluations/scripts/dictionary_info_table.R -- do not edit by hand.",
  "% Hand-authored linguistic metadata; not derived from any data file in this repo.",
  "\\begin{table*}[!ht]",
  "\\centering",
  "\\small",
  "\\adjustbox{max width=\\textwidth}{%",
  "\\begin{tabular}{L{2.5cm} L{1.8cm} c c L{2.6cm} L{1.7cm} L{2.6cm} L{2.4cm} L{3cm}}",
  "\\toprule",
  "\\textbf{Source} & \\textbf{Target} & \\textbf{J20} & \\textbf{EGIDS} & \\textbf{Language family} & \\textbf{Area} & \\textbf{Script} & \\textbf{Characters} & \\textbf{Citation}\\\\",
  "\\midrule",
  data_lines,
  "\\bottomrule",
  "\\end{tabular}}",
  "\\caption{Languages and scripts included in the dataset and evaluation; listed alphabetically by source language. The J20 column follows the resource taxonomy of \\citet{joshi-etal-2020-state}, ranging from 0 for low-resource languages to 5 for high-resource languages. The EGIDS column follows the Expanded Graded Intergenerational Disruption Scale~\\cite{lewis2010assessing}, ranging from 0 for international languages to 10 for extinct languages.}",
  "\\label{tab:dictionaries-info}",
  "\\end{table*}"
)

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(lines, output_path, useBytes = TRUE)

cat(sprintf("Wrote %s (%d rows)\n", output_path, length(rows)))
