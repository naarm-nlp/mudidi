#!/usr/bin/env python3
"""Generate the Stage 2 human-validated gold parse-rules upper-bound table."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import re
import unicodedata
from statistics import fmean

from table_utils import (
    TableDataError,
    lines_to_text,
    parse_float,
    parse_paths,
    read_delimited,
    write_text_atomic,
)

MODEL_DISPLAY = {
    "claudeopus47": "Claude Opus 4.7",
    "gemini31pro": "Gemini 3.1 Pro",
    "gpt55": "GPT-5.5",
    "qwen3vl235": "Qwen3-VL-235B",
}
DICTIONARY_DISPLAY = {
    "Evenki-Russian": "Evenki",
    "Kashmiri-English": "Kashmiri",
    "Na-English-Chinese-French": "Na (Mosuo)",
    "Tiri-English": "Tiri",
    "Nahuatl-French": "Nahuatl",
    "Iñupiatun Eskimo-English": "Iñupiatun",
}
PREFERRED_ORDER = (
    "Evenki-Russian",
    "Kashmiri-English",
    "Na-English-Chinese-French",
    "Tiri-English",
    "Nahuatl-French",
)
EXPERIMENT_PATTERN = re.compile(
    r"^(?P<model>[^_]+)_high_mdf_(?P<intro>intro|nointro)_(?P<toolbox>toolbox|notoolbox)$"
)


def _pair_cells(inferred: float, gold: float) -> tuple[str, str]:
    inferred_text = f"{inferred:.2f}"
    gold_text = f"{gold:.2f}"
    if inferred == gold:
        return inferred_text, gold_text
    if inferred > gold:
        return rf"\textbf{{{inferred_text}}}", gold_text
    return inferred_text, rf"\textbf{{{gold_text}}}"


def render_table(repo_root: Path) -> str:
    """Render the Stage 2 gold parse-rules upper-bound table."""
    source = (
        repo_root
        / "evaluations"
        / "stage2_mdf_lang_script_eval"
        / "stage2_mdf_eval_summary.csv"
    )
    raw_rows = read_delimited(
        source,
        required_columns=("experiment", "language", "MDF_Fields_F1"),
    )
    baseline: dict[tuple[str, str], float] = {}
    for row_number, row in enumerate(raw_rows, start=2):
        key = (row["experiment"], unicodedata.normalize("NFC", row["language"]))
        if key in baseline:
            raise TableDataError(f"{source}: duplicate experiment/language key {key}")
        baseline[key] = parse_float(
            row["MDF_Fields_F1"],
            source=source,
            field="MDF_Fields_F1",
            row_number=row_number,
        )

    paired: list[dict[str, object]] = []
    for row_number, row in enumerate(raw_rows, start=2):
        if row["language"] == "__aggregate__" or not row["experiment"].endswith(
            "_goldcheat"
        ):
            continue
        baseline_experiment = row["experiment"].removesuffix("_goldcheat")
        match = EXPERIMENT_PATTERN.fullmatch(baseline_experiment)
        if match is None:
            raise TableDataError(
                f"{source}: row {row_number}: invalid goldcheat experiment"
            )
        model = match.group("model")
        if model not in MODEL_DISPLAY:
            raise TableDataError(f"{source}: row {row_number}: unknown model {model!r}")
        dictionary = unicodedata.normalize("NFC", row["language"])
        if dictionary not in DICTIONARY_DISPLAY:
            raise TableDataError(f"{source}: no short display name for {dictionary!r}")
        baseline_key = (baseline_experiment, dictionary)
        if baseline_key not in baseline:
            raise TableDataError(f"{source}: no baseline row for {baseline_key}")
        inferred_f1 = baseline[baseline_key]
        if round(inferred_f1, 2) >= 1.0:
            continue
        paired.append(
            {
                "dictionary": dictionary,
                "model": model,
                "intro": match.group("intro") == "intro",
                "toolbox": match.group("toolbox") == "toolbox",
                "inferred": inferred_f1,
                "gold": parse_float(
                    row["MDF_Fields_F1"],
                    source=source,
                    field="MDF_Fields_F1",
                    row_number=row_number,
                ),
            }
        )
    if not paired:
        raise TableDataError(f"{source}: no non-perfect goldcheat comparisons")
    preferred_index = {
        dictionary: index for index, dictionary in enumerate(PREFERRED_ORDER)
    }
    paired.sort(
        key=lambda row: (
            preferred_index.get(str(row["dictionary"]), len(PREFERRED_ORDER)),
            "" if str(row["dictionary"]) in preferred_index else str(row["dictionary"]),
        )
    )

    data_lines: list[str] = []
    for row in paired:
        inferred_cell, gold_cell = _pair_cells(
            float(row["inferred"]), float(row["gold"])
        )
        intro_mark = r"\cmark" if row["intro"] else ""
        toolbox_mark = r"\cmark" if row["toolbox"] else ""
        data_lines.append(
            f"{DICTIONARY_DISPLAY[str(row['dictionary'])]} & {MODEL_DISPLAY[str(row['model'])]} & "
            f"{intro_mark} & {toolbox_mark} & {inferred_cell} & {gold_cell} \\\\"
        )
    macro_cells = _pair_cells(
        fmean(float(row["inferred"]) for row in paired),
        fmean(float(row["gold"]) for row in paired),
    )
    lines = [
        "% Auto-generated by tables/scripts/stage2_gold_cheatsheet_table.py -- do not edit by hand.",
        r"\begin{table}[!h]",
        r"\centering",
        r"\small",
        r"\caption{Stage~2 gold parse-rules diagnostic for every dictionary with imperfect MDF Field F1 under its best per-dictionary configuration, except Efik. Each row replaces the inferred Pass~1 parse-rules with human-validated gold parse-rules before Pass~2 while retaining the same model and introduction/manual setting.}",
        r"\label{tab:stage2-gold-cheat-sheet}",
        r"%\begin{adjustbox}{width=\columnwidth,center}",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{l l cc rr}",
        r"\toprule",
        r"\textbf{Dictionary} & \textbf{Model} & \textbf{Intro} & \textbf{MDF} & \textbf{Inf. F1} & \textbf{Gold F1} \\",
        r"\midrule",
        *data_lines,
        r"\midrule",
        f"\\textbf{{Macro avg.}} &  &  &  & {macro_cells[0]} & {macro_cells[1]} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"%\end{adjustbox}",
        r"\end{table}",
    ]
    return lines_to_text(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Generate ``stage2_gold_cheatsheet.tex``."""
    repo_root, output_dir = parse_paths(__file__, __doc__ or "", argv)
    output_path = output_dir / "stage2_gold_cheatsheet.tex"
    write_text_atomic(output_path, render_table(repo_root))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
