#!/usr/bin/env python3
"""Generate the Stage 1 language-script evaluation LaTeX tables."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from table_utils import (
    TableDataError,
    lines_to_text,
    parse_paths,
    read_delimited,
    write_text_atomic,
)

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
DEFAULT_SCRIPT_BY_LANGUAGE = {
    "Bengalese": "Bengali",
    "Canala": "Latin",
    "Chepang": "Latin",
    "Chukchi": "Cyrillic",
    "Efik": "Latin",
    "English": "Latin",
    "Evenki": "Cyrillic",
    "Greek": "Greek",
    "Gujarati": "Gujarati",
    "Na": "IPA",
    "Russian": "Cyrillic",
}
GROUP_PAGES = (
    (
        "Assyrian-Cuneiform",
        "Assyrian-Latin",
        "Bengalese-Bengali",
        "Canala-Latin",
        "Chepang-Latin",
        "Chinese-Han",
    ),
    (
        "Chukchi-Cyrillic",
        "Circassian-Arabic",
        "Circassian-Latin",
        "Efik-Latin",
        "English-Latin",
        "Evenki-Cyrillic",
    ),
    (
        "French-Latin",
        "Georgian-Georgian",
        "Gojri-Devanagari",
        "Gojri-Latin",
        "Greek-Greek",
        "Gujarati-Gujarati",
        "Hindi-Devanagari",
        "Iñupiatun Eskimo-Latin",
    ),
    (
        "Japanese-Kanji",
        "Japanese-Katakana",
        "Japanese-Latin",
        "Kashmiri-Arabic",
        "Kashmiri-Latin",
        "Khmer-Khmer",
        "Malay-Arabic",
        "Malay-Latin",
    ),
    (
        "Na-IPA",
        "Na-Latin",
        "Nahuatl-Latin",
        "Punjabi-Gurmukhi",
        "Punjabi-Latin",
        "Reel-Latin",
        "Ritharngu-Latin",
        "Russian-Cyrillic",
    ),
    (
        "Sanskrit-Devanagari",
        "Shilluk-Latin",
        "Syriac-Syriac",
        "Telugu-Telugu",
        "Thai-Cyrillic",
        "Thai-Thai",
        "Tiri-Latin",
        "Turkish-Arabic",
    ),
    (
        "Vernacular Syriac-Latin",
        "Vernacular Syriac-Syriac",
        "Yiddish-Hebrew",
    ),
)
GROUPS = tuple(group for page in GROUP_PAGES for group in page)


def _canonical_group(language_script: str, source: Path, row_number: int) -> str | None:
    if language_script == "Meta":
        return None
    if "-" in language_script:
        return language_script
    try:
        script = DEFAULT_SCRIPT_BY_LANGUAGE[language_script]
    except KeyError as exc:
        raise TableDataError(
            f"{source}: row {row_number}: no default script for {language_script!r}"
        ) from exc
    return f"{language_script}-{script}"


def _prepare_groups(repo_root: Path) -> tuple[dict[str, dict[str, object]], Path]:
    source = (
        repo_root
        / "evaluations"
        / "stage1_flat_per_lang_script_eval"
        / "stage1_flat_eval_per_language_script_detailed.csv"
    )
    required = (
        "experiment",
        "language",
        "language_script",
        "gold_word_count",
        "gold_grapheme_count",
        "total_graphemes_pred",
        "total_grapheme_edits",
        "total_word_edits",
    )
    raw_rows = read_delimited(source, required_columns=required)
    expected_experiments = {spec[0] for spec in ROW_SPEC}
    experiments = {row["experiment"] for row in raw_rows}
    if experiments != expected_experiments:
        raise TableDataError(
            f"{source}: experiment set mismatch; "
            f"missing={sorted(expected_experiments - experiments)}, "
            f"extra={sorted(experiments - expected_experiments)}"
        )

    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    expected_groups = set(GROUPS)
    for row_number, row in enumerate(raw_rows, start=2):
        group = _canonical_group(row["language_script"], source, row_number)
        if group not in expected_groups:
            continue
        try:
            parsed_counts = {
                field: int(row[field])
                for field in (
                    "gold_word_count",
                    "gold_grapheme_count",
                    "total_graphemes_pred",
                    "total_grapheme_edits",
                    "total_word_edits",
                )
            }
        except ValueError as exc:
            raise TableDataError(
                f"{source}: row {row_number}: metric counts must be integers"
            ) from exc
        grouped.setdefault((group, row["experiment"]), []).append(
            {
                "dictionary": row["language"],
                **parsed_counts,
            }
        )

    prepared: dict[str, dict[str, object]] = {}
    for group in GROUPS:
        experiment_rows = {
            experiment: grouped.get((group, experiment), [])
            for experiment in expected_experiments
        }
        missing = sorted(
            experiment for experiment, rows in experiment_rows.items() if not rows
        )
        if missing:
            raise TableDataError(f"{source}: {group} is missing experiments: {missing}")

        support_signatures = {
            experiment: (
                frozenset(str(row["dictionary"]) for row in rows),
                sum(int(row["gold_word_count"]) for row in rows),
                sum(int(row["gold_grapheme_count"]) for row in rows),
            )
            for experiment, rows in experiment_rows.items()
        }
        first_signature = next(iter(support_signatures.values()))
        if any(
            signature != first_signature for signature in support_signatures.values()
        ):
            raise TableDataError(
                f"{source}: {group} support differs across experiments"
            )

        metrics: dict[str, dict[str, float]] = {}
        for experiment, rows in experiment_rows.items():
            grapheme_edits = sum(int(row["total_grapheme_edits"]) for row in rows)
            graphemes_gold = sum(int(row["gold_grapheme_count"]) for row in rows)
            graphemes_pred = sum(int(row["total_graphemes_pred"]) for row in rows)
            word_edits = sum(int(row["total_word_edits"]) for row in rows)
            words_gold = sum(int(row["gold_word_count"]) for row in rows)
            if not graphemes_gold or not words_gold:
                raise TableDataError(f"{source}: {group} has zero gold support")
            metrics[experiment] = {
                "TextEdit": grapheme_edits / max(graphemes_gold, graphemes_pred, 1),
                "GCER": grapheme_edits / graphemes_gold,
                "WER": word_edits / words_gold,
            }

        prepared[group] = {
            "dictionary_count": len(first_signature[0]),
            "word_count": first_signature[1],
            "grapheme_count": first_signature[2],
            "metrics": metrics,
        }
    return prepared, source


def _render_block(group: str, prepared: dict[str, object]) -> list[str]:
    metrics = prepared["metrics"]
    if not isinstance(metrics, dict):
        raise TypeError("prepared metrics must be a dictionary")
    best = {
        metric: min(float(metrics[experiment][metric]) for experiment, *_ in ROW_SPEC)
        for metric in METRICS
    }
    lines = [
        rf"\multicolumn{{5}}{{>{{\columncolor{{green!12}}}}l}}{{\textbf{{{group}}} ({prepared['dictionary_count']} dictionaries, {int(prepared['word_count']):,} words, {int(prepared['grapheme_count']):,} graphemes)}}"
    ]
    current_section: str | None = None
    for experiment, model, alphabet, section in ROW_SPEC:
        if section != current_section:
            lines.append(
                rf"\multicolumn{{5}}{{>{{\columncolor{{gray!12}}}}l}}{{\emph{{{section}}}}}"
            )
            current_section = section
        cells: list[str] = []
        for metric in METRICS:
            value = float(metrics[experiment][metric])
            rendered = f"{value:.2f}"
            cells.append(
                rf"\textbf{{{rendered}}}" if value == best[metric] else rendered
            )
        mark = r"\cmark" if alphabet else ""
        lines.append(f"{model} & {mark} & {' & '.join(cells)}")
    return lines


def _render_page(
    page_number: int,
    groups: tuple[str, ...],
    blocks: dict[str, list[str]],
) -> list[str]:
    lines = [
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
    pairs = [groups[index : index + 2] for index in range(0, len(groups), 2)]
    for pair_index, pair in enumerate(pairs):
        left = blocks[pair[0]]
        right = (
            blocks[pair[1]] if len(pair) == 2 else [r"\multicolumn{5}{l}{}"] * len(left)
        )
        lines.extend(
            f"{left_line} & {right_line} \\\\"
            for left_line, right_line in zip(left, right, strict=True)
        )
        if pair_index != len(pairs) - 1:
            lines.extend(
                (r"\addlinespace[0.35em]", r"\midrule", r"\addlinespace[0.35em]", "")
            )
    continued = " (continued)" if page_number > 1 else ""
    lines.extend(
        (
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{adjustbox}",
            rf"\caption{{Stage 1 language-script-specific evaluation results{continued}. \textit{{Best scores are bolded (lower is better for all three metrics)}}.}}",
            rf"\label{{tab:stage1-by-langscript-{page_number}}}",
            r"\end{table*}",
        )
    )
    return lines


def render_table(repo_root: Path) -> str:
    """Render the seven Stage 1 language-script table pages."""
    prepared, _source = _prepare_groups(repo_root)
    blocks = {group: _render_block(group, prepared[group]) for group in GROUPS}
    lines = [
        "% Auto-generated by tables/scripts/stage1_script_table.py -- do not edit by hand."
    ]
    for page_number, groups in enumerate(GROUP_PAGES, start=1):
        if page_number > 1:
            lines.append("")
        lines.extend(_render_page(page_number, groups, blocks))
    return lines_to_text(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Generate ``stage1_by_script.tex``."""
    repo_root, output_dir = parse_paths(__file__, __doc__ or "", argv)
    output_path = output_dir / "stage1_by_script.tex"
    write_text_atomic(output_path, render_table(repo_root))
    print(f"Wrote {output_path} ({len(GROUPS)} language-script groups)")


if __name__ == "__main__":
    main()
