#!/usr/bin/env python3
"""Write one row per (dictionary, language, script) combination with its gold word count.

Source data: evaluations/stage1_flat_per_lang_script_eval/
  stage1_flat_eval_per_language_script_summary.csv
(one row per experiment x dictionary x language_script tag; "language" in that
CSV is actually the dictionary name, and "language_script" is "<Language>-<Script>",
e.g. "Assyrian-Latin"). Word counts are gold-side and identical across
experiments, so only one experiment's rows are read.

Unlike stage1_script_table.R / stage1_langscript_table.R, this keeps
dictionary and language-script as separate columns rather than aggregating
across dictionaries -- one row per exact (dictionary, language, script)
triple, e.g. "Assyrian-English | English | Latin" and
"Bengalese-English | English | Latin" stay distinct.

Usage:
  uv run python tables/scripts/dictionary_language_script_words.py
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_CSV = (
    REPO / "evaluations" / "stage1_flat_per_lang_script_eval"
    / "stage1_flat_eval_per_language_script_summary.csv"
)
OUTPUT_PATH = REPO / "tables" / "results" / "dictionary_language_script_words.tsv"
REFERENCE_EXPERIMENT = "gemini31pro_flat_alpha"


def main() -> int:
    if not SOURCE_CSV.is_file():
        print(f"Per-language-script summary CSV not found: {SOURCE_CSV}")
        return 1

    with SOURCE_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    rows = [r for r in rows if r["experiment"] == REFERENCE_EXPERIMENT]
    if not rows:
        print(f"No rows found for reference experiment {REFERENCE_EXPERIMENT!r}")
        return 1

    words_by_triple: dict[tuple[str, str, str], int] = {}
    for row in rows:
        dictionary = row["language"]
        language, script = row["language_script"].split("-", 1)
        words_by_triple[(dictionary, language, script)] = int(row["gold_word_count"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["dictionary", "language", "script", "words"])
        for (dictionary, language, script), words in sorted(words_by_triple.items()):
            writer.writerow([dictionary, language, script, words])

    print(f"Wrote {OUTPUT_PATH} ({len(words_by_triple)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
