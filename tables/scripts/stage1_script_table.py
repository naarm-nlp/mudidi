#!/usr/bin/env python3
"""Generate the Stage 1 evaluation table aggregated by writing script."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from table_utils import TableDataError, lines_to_text, parse_float, parse_paths, read_delimited, write_text_atomic

METRICS = ("TextEdit", "GCER", "WER")
ROW_SPEC = (
    ("GLM-OCR-flat_noalpha", "GLM-OCR", False, "OCR systems"),
    ("GLM-OCR-flat_alpha", "GLM-OCR", True, "OCR systems"),
    ("Mathpix-OCR", "Mathpix", False, "OCR systems"),
    ("MinerU2.5-Pro", "MinerU2.5-Pro", False, "OCR systems"),
    ("PaddleOCR-VL-1.5", "PaddleOCR-VL-1.5", False, "OCR systems"),
    ("qwen3vl235_flat_noalpha", "Qwen3-VL-235B", False, "Vision Language Models"),
    ("qwen3vl235_flat_alpha", "Qwen3-VL-235B", True, "Vision Language Models"),
    ("claudeopus47_flat_noalpha", "Claude Opus 4.7", False, "General-purpose LLMs"),
    ("claudeopus47_flat_alpha", "Claude Opus 4.7", True, "General-purpose LLMs"),
    ("gpt55_flat_noalpha", "GPT-5.5", False, "General-purpose LLMs"),
    ("gpt55_flat_alpha", "GPT-5.5", True, "General-purpose LLMs"),
    ("gemini3flash_flat_noalpha", "Gemini 3 Flash", False, "General-purpose LLMs"),
    ("gemini3flash_flat_alpha", "Gemini 3 Flash", True, "General-purpose LLMs"),
    ("gemini31pro_flat_noalpha", "Gemini 3.1 Pro", False, "General-purpose LLMs"),
    ("gemini31pro_flat_alpha", "Gemini 3.1 Pro", True, "General-purpose LLMs"),
)


def _prepare_rows(repo_root: Path) -> tuple[list[dict[str, object]], Path]:
    evaluation_root = repo_root / "evaluations" / "stage1_flat_per_lang_script_eval"
    source = evaluation_root / "stage1_flat_eval_per_language_script_summary.csv"
    manual_source = repo_root / "tables" / "results" / "dictionary_language_script_words_manual.tsv"
    required = (
        "experiment",
        "language",
        "language_script",
        "gold_word_count",
        "gold_grapheme_count",
        *METRICS,
    )
    raw_rows = read_delimited(source, required_columns=required)
    manual_rows = read_delimited(
        manual_source,
        delimiter="\t",
        required_columns=("dictionary", "language", "script", "words", "decision"),
    )
    manual: dict[tuple[str, str, int], tuple[str, str]] = {}
    for row_number, row in enumerate(manual_rows, start=2):
        try:
            words = int(row["words"])
        except ValueError as error:
            raise TableDataError(f"{manual_source}: row {row_number}: words is not an integer") from error
        key = (row["dictionary"], row["language"], words)
        if key in manual:
            if manual[key][1] == row["decision"] == "exclude":
                continue
            raise TableDataError(f"{manual_source}: ambiguous dictionary/language/words key {key}")
        manual[key] = (row["script"], row["decision"])

    expected_experiments = {spec[0] for spec in ROW_SPEC}
    experiments = {row["experiment"] for row in raw_rows}
    if experiments != expected_experiments:
        raise TableDataError(
            f"{source}: experiment set mismatch; missing={sorted(expected_experiments - experiments)}, "
            f"extra={sorted(experiments - expected_experiments)}"
        )

    prepared: list[dict[str, object]] = []
    unmatched: list[tuple[str, str, int]] = []
    for row_number, row in enumerate(raw_rows, start=2):
        extracted_language, separator, fallback_script = row["language_script"].partition("-")
        if not separator:
            raise TableDataError(f"{source}: row {row_number}: invalid language_script {row['language_script']!r}")
        try:
            word_count = int(row["gold_word_count"])
            grapheme_count = int(row["gold_grapheme_count"])
        except ValueError as error:
            raise TableDataError(f"{source}: row {row_number}: support counts must be integers") from error
        key = (row["language"], extracted_language, word_count)
        if key in manual:
            script, decision = manual[key]
        else:
            script, decision = fallback_script, ""
            unmatched.append(key)
        if decision == "exclude":
            continue
        prepared.append(
            {
                "experiment": row["experiment"],
                "dictionary": row["language"],
                "language_script": row["language_script"],
                "script": script,
                "word_count": word_count,
                "grapheme_count": grapheme_count,
                **{
                    metric: parse_float(row[metric], source=source, field=metric, row_number=row_number)
                    for metric in METRICS
                },
            }
        )
    if unmatched:
        raise TableDataError(f"{source}: {len(set(unmatched))} rows have no manual script mapping")
    return prepared, source


def _render_block(script: str, rows: list[dict[str, object]], source: Path) -> list[str]:
    by_experiment: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_experiment.setdefault(str(row["experiment"]), []).append(row)
    expected_experiments = {spec[0] for spec in ROW_SPEC}
    if set(by_experiment) != expected_experiments:
        raise TableDataError(f"{source}: incomplete experiment set for script {script}")
    aggregates = {
        experiment: {
            metric: fmean(float(row[metric]) for row in experiment_rows)
            for metric in METRICS
        }
        for experiment, experiment_rows in by_experiment.items()
    }
    best = {metric: min(values[metric] for values in aggregates.values()) for metric in METRICS}
    support_rows = {
        (
            str(row["dictionary"]),
            str(row["language_script"]),
            int(row["word_count"]),
            int(row["grapheme_count"]),
        )
        for row in rows
    }
    dictionary_count = len({row[0] for row in support_rows})
    word_count = sum(row[2] for row in support_rows)
    grapheme_count = sum(row[3] for row in support_rows)
    lines = [
        rf"\multicolumn{{5}}{{>{{\columncolor{{green!12}}}}l}}{{\textbf{{{script}}} ({dictionary_count} dictionaries, {word_count:,} words, {grapheme_count:,} graphemes)}}"
    ]
    current_section: str | None = None
    for experiment, model, alphabet, section in ROW_SPEC:
        if section != current_section:
            lines.append(rf"\multicolumn{{5}}{{>{{\columncolor{{gray!12}}}}l}}{{\emph{{{section}}}}}")
            current_section = section
        cells: list[str] = []
        for metric in METRICS:
            value = aggregates[experiment][metric]
            rendered = f"{value:.2f}"
            cells.append(rf"\textbf{{{rendered}}}" if value == best[metric] else rendered)
        mark = r"\cmark" if alphabet else ""
        lines.append(f"{model} & {mark} & {' & '.join(cells)}")
    return lines


def render_table(repo_root: Path) -> str:
    """Render the Stage 1 script-level table."""
    rows, source = _prepare_rows(repo_root)
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["script"]), []).append(row)
    scripts = sorted(grouped)
    blocks = {script: _render_block(script, grouped[script], source) for script in scripts}
    lines = [
        "% Auto-generated by tables/scripts/stage1_script_table.py -- do not edit by hand.",
        r"\begin{table*}[!hp]",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2.2pt}",
        r"\renewcommand{\arraystretch}{1.08}",
        r"\begin{adjustbox}{max width=\textwidth}",
        r"\begin{tabular}{lc@{\quad}rrr@{\qquad}lc@{\quad}rrr}",
        r"\toprule",
        r"\textbf{Model} & \textbf{Alph.} & \textbf{Edit} & \textbf{GCER} & \textbf{WER} & \textbf{Model} & \textbf{Alph.} & \textbf{Edit} & \textbf{GCER} & \textbf{WER} \\",
        r"\midrule",
        "",
    ]
    pairs = [scripts[index : index + 2] for index in range(0, len(scripts), 2)]
    for pair_index, pair in enumerate(pairs):
        left = blocks[pair[0]]
        right = blocks[pair[1]] if len(pair) == 2 else [r"\multicolumn{5}{l}{}"] * len(left)
        if len(left) != len(right):
            raise TableDataError(f"paired script blocks differ in length: {pair}")
        lines.extend(f"{left_line} & {right_line} \\\\" for left_line, right_line in zip(left, right, strict=True))
        if pair_index != len(pairs) - 1:
            lines.extend((r"\addlinespace[0.35em]", r"\midrule", r"\addlinespace[0.35em]", ""))
    lines.extend(
        (
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{adjustbox}",
            r"\caption{Stage 1 script-specific evaluation results, aggregated by writing script (e.g. \textit{Latin} pools every language whose dictionary column is transliterated into Latin script). Edit/GCER/WER only: Markup F1 and read order are page-level structural metrics and do not decompose to a single script. Each shaded script header reports the number of dictionaries, gold word count, and gold grapheme count backing that script's average; many scripts are backed by a single dictionary and should be read accordingly. \textit{Best scores per script are bolded (lower is better for all three metrics)}.}",
            r"\label{tab:stage1-by-script}",
            r"\end{table*}",
        )
    )
    return lines_to_text(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Generate ``stage1_by_script.tex``."""
    repo_root, output_dir = parse_paths(__file__, __doc__ or "", argv)
    output_path = output_dir / "stage1_by_script.tex"
    table = render_table(repo_root)
    write_text_atomic(output_path, table)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
