#!/usr/bin/env python3
"""Generate fixed Gemini 3.1 Pro + alphabet OCR-hint ablation tables."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from table_utils import (
    TableDataError,
    index_unique,
    lines_to_text,
    parse_bool,
    parse_float,
    parse_paths,
    read_delimited,
    write_text_atomic,
)

BASELINE_EXPERIMENT = "gemini31pro_flat_alpha"
HINT_EXPERIMENT = "gemini31pro_flat_alpha_ocr"
METRICS = ("TextEdit", "GCER", "WER", "typography_f1", "ReadOrderEdit")
HIGHER_IS_BETTER = {metric: metric == "typography_f1" for metric in METRICS}
REQUIRED_COLUMNS = ("experiment", "language", "alphabet", "ocr-hint", *METRICS)


def _load_experiment(path: Path, experiment: str, *, expect_hint: bool) -> dict[str, dict[str, object]]:
    raw_rows = read_delimited(path, required_columns=REQUIRED_COLUMNS)
    selected = [row for row in raw_rows if row["experiment"] == experiment]
    unexpected = sorted({row["experiment"] for row in raw_rows if row["experiment"] != experiment})
    if unexpected:
        raise TableDataError(f"{path}: unexpected experiment(s): {', '.join(unexpected)}")
    keyed = index_unique(selected, ("language",), source=path)
    if len(keyed) != 30:
        raise TableDataError(f"{path}: {experiment} has {len(keyed)} dictionaries, expected 30")

    parsed: dict[str, dict[str, object]] = {}
    for row_number, row in enumerate(selected, start=2):
        alphabet = parse_bool(row["alphabet"], source=path, field="alphabet", row_number=row_number)
        hint = parse_bool(row["ocr-hint"], source=path, field="ocr-hint", row_number=row_number)
        if not alphabet:
            raise TableDataError(f"{path}: row {row_number}: fixed protocol requires alphabet=true")
        if hint != expect_hint:
            raise TableDataError(
                f"{path}: row {row_number}: expected ocr-hint={str(expect_hint).lower()}"
            )
        parsed[row["language"]] = {
            metric: parse_float(row[metric], source=path, field=metric, row_number=row_number)
            for metric in METRICS
        }
    return parsed


def _paired_rows(repo_root: Path) -> list[tuple[str, dict[str, object], dict[str, object]]]:
    evaluation_root = repo_root / "evaluations" / "stage1_flat_per_lang_script_eval"
    baseline_source = evaluation_root / "stage1_flat_eval_summary.csv"
    hint_source = evaluation_root / "stage1_flat_eval_ocr_hint_summary.csv"

    all_baseline_rows = read_delimited(baseline_source, required_columns=REQUIRED_COLUMNS)
    baseline_only = [row for row in all_baseline_rows if row["experiment"] == BASELINE_EXPERIMENT]
    if len(baseline_only) != 30:
        raise TableDataError(
            f"{baseline_source}: {BASELINE_EXPERIMENT} has {len(baseline_only)} dictionaries, expected 30"
        )
    # Parse the selected baseline rows directly; the canonical summary intentionally
    # contains other Stage 1 experiments.
    index_unique(baseline_only, ("language",), source=baseline_source)
    baseline: dict[str, dict[str, object]] = {}
    for row_number, row in enumerate(baseline_only, start=2):
        alphabet = parse_bool(row["alphabet"], source=baseline_source, field="alphabet", row_number=row_number)
        hint = parse_bool(row["ocr-hint"], source=baseline_source, field="ocr-hint", row_number=row_number)
        if not alphabet or hint:
            raise TableDataError(
                f"{baseline_source}: {row['language']}: fixed baseline requires alphabet=true and ocr-hint=false"
            )
        baseline[row["language"]] = {
            metric: parse_float(
                row[metric], source=baseline_source, field=metric, row_number=row_number
            )
            for metric in METRICS
        }

    hints = _load_experiment(hint_source, HINT_EXPERIMENT, expect_hint=True)
    if set(baseline) != set(hints):
        raise TableDataError(
            "OCR-hint dictionary set differs from the fixed baseline; "
            f"missing={sorted(set(baseline) - set(hints))}, "
            f"extra={sorted(set(hints) - set(baseline))}"
        )
    return [(language, baseline[language], hints[language]) for language in sorted(baseline)]


def _pair_cells(baseline: float, hint: float, *, higher_is_better: bool, digits: int) -> tuple[str, str]:
    baseline_text = f"{baseline:.{digits}f}"
    hint_text = f"{hint:.{digits}f}"
    if baseline == hint:
        return baseline_text, hint_text
    baseline_wins = baseline > hint if higher_is_better else baseline < hint
    return (
        rf"\textbf{{{baseline_text}}}" if baseline_wins else baseline_text,
        rf"\textbf{{{hint_text}}}" if not baseline_wins else hint_text,
    )


def render_tables(repo_root: Path) -> tuple[str, str]:
    """Render the per-dictionary and aggregate fixed-protocol tables."""
    rows = _paired_rows(repo_root)
    data_lines: list[str] = []
    for language, baseline, hint in rows:
        cells: list[str] = []
        for metric in METRICS:
            cells.extend(
                _pair_cells(
                    float(baseline[metric]),
                    float(hint[metric]),
                    higher_is_better=HIGHER_IS_BETTER[metric],
                    digits=3,
                )
            )
        display_language = language.replace("Kurdish_Turkish", "Kurdish-Turkish")
        data_lines.append(
            f"{display_language} & Gemini-Pro & \\cmark & {' & '.join(cells)} \\\\"
        )

    means = {
        "baseline": {metric: fmean(float(baseline[metric]) for _, baseline, _ in rows) for metric in METRICS},
        "hint": {metric: fmean(float(hint[metric]) for _, _, hint in rows) for metric in METRICS},
    }
    mean_cells: list[str] = []
    baseline_cells: list[str] = []
    hint_cells: list[str] = []
    delta_cells: list[str] = []
    for metric in METRICS:
        baseline_value = means["baseline"][metric]
        hint_value = means["hint"][metric]
        pair = _pair_cells(
            baseline_value,
            hint_value,
            higher_is_better=HIGHER_IS_BETTER[metric],
            digits=3,
        )
        mean_cells.extend(pair)
        baseline_cells.append(pair[0])
        hint_cells.append(pair[1])
        delta_cells.append(f"${hint_value - baseline_value:+.3f}$")

    per_dictionary_lines = [
        "% Auto-generated by tables/scripts/stage1_ocr_hint_table.py -- do not edit by hand.",
        r"\section{Stage-1 OCR Assisted Prompting Results}",
        r"\label{sec:app-ocr-asst}",
        r"\begin{table*}[!h]",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1.08}",
        r"\begin{adjustbox}{max width=\textwidth}",
        r"\begin{tabular}{llc cc cc cc cc cc}",
        r"\toprule",
        " & & & \\multicolumn{2}{c}{\\textbf{Edit}} & \\multicolumn{2}{c}{\\textbf{GCER}} & \\multicolumn{2}{c}{\\textbf{WER}} & \\multicolumn{2}{c}{\\textbf{Mrk. F1}} & \\multicolumn{2}{c}{\\textbf{Order}} \\\\",
        r"\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}\cmidrule(lr){10-11}\cmidrule(lr){12-13}",
        "\\textbf{Dictionary} & \\textbf{Model} & \\textbf{A} & -- & +hint & -- & +hint & -- & +hint & -- & +hint & -- & +hint \\\\",
        r"\midrule",
        *data_lines,
        r"\midrule",
        f"\\textit{{Mean}} &  &  & {' & '.join(mean_cells)} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{adjustbox}",
        r"\caption{Per-dictionary breakdown of the Stage 1 OCR-hint ablation summarised in Table~\ref{tab:stage1-aggregate-ocr-hint}. Gemini~3.1 Pro receives the source-language alphabet in both conditions; each metric is shown as a paired (without hint, with hint) value. \textit{Best score per pair is bolded; lower is better except for Markup F1.}}",
        r"\label{tab:stage1-ocr-hint-per-language}",
        r"\end{table*}",
    ]
    summary_lines = [
        "% Auto-generated by tables/scripts/stage1_ocr_hint_table.py -- do not edit by hand.",
        "% Fixed Gemini 3.1 Pro + alphabet OCR-hint ablation, averaged over 30 dictionaries.",
        r"\begin{table}[!h]",
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{3pt}",
        r"\caption{Stage 1 OCR-hint ablation study, averaged over 30 dictionaries. We hold Gemini~3.1 Pro with alphabet input fixed and compare transcription with vs.\ without a preliminary OCR transcript supplied to the model. \textit{Best score per metric is bolded.}}",
        r"\label{tab:stage1-aggregate-ocr-hint}",
        r"\begin{tabular}{lcrrrrr}",
        r"\toprule",
        "\\textbf{Configuration} & \\textbf{OCR hint} & \\textbf{Edit} & \\textbf{GCER} & \\textbf{WER} & \\textbf{Mrk. F1} & \\textbf{Order} \\\\",
        r"\midrule",
        f"Gemini 3.1 Pro + alphabet &  & {' & '.join(baseline_cells)} \\\\",
        f"Gemini 3.1 Pro + alphabet & \\cmark & {' & '.join(hint_cells)} \\\\",
        r"\midrule",
        f"$\\Delta$ (with hint $-$ without) &  & {' & '.join(delta_cells)} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return lines_to_text(per_dictionary_lines), lines_to_text(summary_lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Generate both fixed-protocol OCR-hint tables."""
    repo_root, output_dir = parse_paths(__file__, __doc__ or "", argv)
    per_dictionary, summary = render_tables(repo_root)
    per_dictionary_path = output_dir / "stage1_ocr_hint_per_dictionary.tex"
    summary_path = output_dir / "stage1_ocr_hint_summary.tex"
    write_text_atomic(per_dictionary_path, per_dictionary)
    write_text_atomic(summary_path, summary)
    print(f"Wrote {per_dictionary_path} (30 dictionaries)")
    print(f"Wrote {summary_path} (fixed Gemini 3.1 Pro + alphabet)")


if __name__ == "__main__":
    main()
