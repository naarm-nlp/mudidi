#!/usr/bin/env python3
"""Estimate Stage 2 MDF inter-annotator agreement between two human annotators.

Two annotators (default suffixes ``_EV`` and ``_TK``) independently produced
MDF gold labels for the same Stage 2 pages. This script pairs up their field
lines by content using the same fuzzy record/line alignment machinery the
Stage 2 evaluator uses to score model predictions against gold
(``mudidi.evaluation.stage2.mdf_align``), then compares the ``\\marker`` each
annotator assigned to each paired line and prints the pooled Cohen's kappa.

Writes no files -- just prints the aggregated agreement score.

Usage:
  uv run python tables/temporary/stage2_inter_annotator_agreement.py
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from sklearn.metrics import cohen_kappa_score

from mudidi.evaluation.stage2.mdf_align import align_lines, align_records
from mudidi.evaluation.stage2.mdf_parser import parse_mdf

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_DIR = REPO / "dataset" / "mudidi" / "dictionaries"
DEFAULT_RECORD_THRESHOLD = 0.6
DEFAULT_LINE_THRESHOLD = 0.7
DEFAULT_SUFFIX_A = "_EV"
DEFAULT_SUFFIX_B = "_TK"


@dataclass(frozen=True)
class AnnotationPageTask:
    """One dictionary page with both annotators' MDF files."""

    dictionary: str
    page_id: str
    a_path: Path
    b_path: Path


def discover_annotation_pairs(
    dataset_dir: Path,
    suffix_a: str,
    suffix_b: str,
) -> List[AnnotationPageTask]:
    """Find pages under dataset_dir that have both annotators' MDF files."""
    tasks: List[AnnotationPageTask] = []
    for dict_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        stage2_dir = dict_dir / "Stage 2 MDF file"
        if not stage2_dir.is_dir():
            continue
        for page_dir in sorted(p for p in stage2_dir.iterdir() if p.is_dir()):
            a_path = next(page_dir.glob(f"*{suffix_a}.txt"), None)
            b_path = next(page_dir.glob(f"*{suffix_b}.txt"), None)
            if a_path and b_path:
                tasks.append(
                    AnnotationPageTask(
                        dictionary=dict_dir.name,
                        page_id=page_dir.name,
                        a_path=a_path,
                        b_path=b_path,
                    )
                )
    return tasks


def marker_pairs_for_task(
    task: AnnotationPageTask,
    *,
    record_threshold: float,
    line_threshold: float,
) -> List[Tuple[str, str]]:
    """Align one page's two annotations and return the paired (a_marker, b_marker) list."""
    a_records = parse_mdf(task.a_path.read_text(encoding="utf-8"))
    b_records = parse_mdf(task.b_path.read_text(encoding="utf-8"))

    record_alignment = align_records(a_records, b_records, threshold=record_threshold)

    marker_pairs: List[Tuple[str, str]] = []
    for match in record_alignment.matched:
        a_rec = a_records[match.gold_index]
        b_rec = b_records[match.pred_index]
        line_alignment = align_lines(a_rec.lines, b_rec.lines, threshold=line_threshold)
        for line_match in line_alignment.matched:
            a_line = a_rec.lines[line_match.gold_index]
            b_line = b_rec.lines[line_match.pred_index]
            marker_pairs.append((a_line.marker, b_line.marker))

    return marker_pairs


def _kappa(marker_pairs: List[Tuple[str, str]]) -> float | None:
    if not marker_pairs:
        return None
    a_labels = [pair[0] for pair in marker_pairs]
    b_labels = [pair[1] for pair in marker_pairs]
    if len(set(a_labels) | set(b_labels)) < 2:
        # cohen_kappa_score is undefined (0/0) when every rated item shares
        # one label; agreement is then trivially total.
        return 1.0
    return cohen_kappa_score(a_labels, b_labels)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate Stage 2 MDF inter-annotator agreement (Cohen's kappa).",
    )
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--suffix-a", default=DEFAULT_SUFFIX_A, help="First annotator's filename suffix")
    parser.add_argument("--suffix-b", default=DEFAULT_SUFFIX_B, help="Second annotator's filename suffix")
    parser.add_argument("--record-threshold", type=float, default=DEFAULT_RECORD_THRESHOLD)
    parser.add_argument("--line-threshold", type=float, default=DEFAULT_LINE_THRESHOLD)
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset_dir)
    label_a = args.suffix_a.lstrip("_")
    label_b = args.suffix_b.lstrip("_")

    if not dataset_dir.is_dir():
        logger.error("Dataset directory not found: %s", dataset_dir)
        return 1

    tasks = discover_annotation_pairs(dataset_dir, args.suffix_a, args.suffix_b)
    if not tasks:
        logger.error(
            "No pages with both %s and %s annotation files found under %s",
            args.suffix_a,
            args.suffix_b,
            dataset_dir,
        )
        return 1

    logger.info("Found %d annotated page(s) across %d dictionaries", len(tasks), len({t.dictionary for t in tasks}))

    all_marker_pairs: List[Tuple[str, str]] = []
    for task in tasks:
        all_marker_pairs.extend(
            marker_pairs_for_task(
                task,
                record_threshold=args.record_threshold,
                line_threshold=args.line_threshold,
            )
        )

    n_paired = len(all_marker_pairs)
    n_agree = sum(1 for a, b in all_marker_pairs if a == b)
    kappa = _kappa(all_marker_pairs)
    raw_agreement = n_agree / n_paired if n_paired else None

    print(f"\nPaired lines: {n_paired}")
    print(f"Pooled raw agreement ({label_a} vs {label_b}): {raw_agreement:.6f}" if raw_agreement is not None else "Pooled raw agreement: n/a")
    print(f"Pooled Cohen's kappa ({label_a} vs {label_b}): {kappa:.6f}" if kappa is not None else "Pooled Cohen's kappa: n/a")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
