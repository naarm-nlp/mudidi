#!/usr/bin/env python3
"""Test Stage 1 agentic recovery on pages with severe v1 hallucinations.

Treats v1 TSV rows per page as the initial (bad) Stage 1 flat transcript,
runs the bounded verifier-rewriter loop against the dictionary page image,
and compares headword coverage vs v2 gold rows for the same page.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.schemas.ocr_result import OCRPageResult

# Pages with severe v1 hallucinations / page bleed from prior analysis.
DEFAULT_BAD_PAGES = (136, 237, 262, 348, 355, 409, 428)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_V1 = REPO / "tmp" / "carolinian_combined_v1.tsv"
DEFAULT_V2 = REPO / "tmp" / "carolinian_combined_v2.tsv"
DEFAULT_PDF = REPO / "inputs" / "Carolinian-English-Dictionary.pdf"
DEFAULT_OUT = REPO / "tmp" / "agentic_hallucination_test"


def norm_hw(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "").strip()).lower()
    return re.sub(r"[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D\-—–]", "-", text)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def rows_for_page(rows: list[dict[str, str]], page_num: int) -> list[dict[str, str]]:
    tag = f"page_{page_num}"
    return [row for row in rows if (row.get("Source_Page") or "").strip() == tag]


def mdf_rows_to_flat(rows: list[dict[str, str]]) -> str:
    """Serialize MDF rows into dictionary-page-like flat lines."""
    lines: list[str] = []
    for row in rows:
        headword = (row.get("Headword") or "").strip()
        gloss = (row.get("Gloss") or "").strip()
        pos = (row.get("POS") or "").strip()
        entry_type = (row.get("Entry_Type") or "").strip().lower()
        if not headword and not gloss:
            continue
        if entry_type == "subentry":
            parent = (row.get("Parent_Lexeme") or "").strip()
            prefix = f"—{headword}" if headword else ""
            if parent and not prefix:
                prefix = f"—{parent}"
            chunk = " ".join(part for part in (prefix, pos + "." if pos else "", gloss) if part)
            lines.append(chunk.strip())
            continue
        chunk_parts = [headword]
        if pos:
            chunk_parts.append(f"{pos}.")
        if gloss:
            chunk_parts.append(gloss)
        lines.append(" ".join(chunk_parts))
    return "\n".join(lines)


def headwords_from_flat(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^—?([^\s]+)", stripped)
        if match:
            result.add(norm_hw(match.group(1)))
    return result


def headwords_from_rows(rows: list[dict[str, str]]) -> set[str]:
    result: set[str] = set()
    for row in rows:
        hw = norm_hw(row.get("Headword", ""))
        if hw:
            result.add(hw)
    return result


def headwords_in_text(text: str, candidates: set[str]) -> set[str]:
    lowered = text.lower()
    found: set[str] = set()
    for hw in candidates:
        if not hw:
            continue
        pattern = rf"\b{re.escape(hw)}\b"
        if re.search(pattern, lowered):
            found.add(hw)
    return found


def pdf_page_text(pdf_path: Path, page_num: int) -> str:
    doc = pymupdf.open(str(pdf_path))
    try:
        return doc.load_page(page_num - 1).get_text()
    finally:
        doc.close()


def render_pdf_page(pdf_path: Path, page_num: int, out_path: Path, dpi: int = 200) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        if page_num < 1 or page_num > doc.page_count:
            raise ValueError(f"Page {page_num} out of range (1-{doc.page_count})")
        pix = doc.load_page(page_num - 1).get_pixmap(dpi=dpi)
        pix.save(str(out_path))
    finally:
        doc.close()
    return out_path


@dataclass(frozen=True)
class PageMetrics:
    page_num: int
    v1_headwords: int
    v2_headwords: int
    bad_v1_only: int
    bad_recall_before: float
    bad_recall_after: float
    gold_recall_before: float
    gold_recall_after: float
    gold_flat_similarity_before: float
    gold_flat_similarity_after: float
    stop_reason: str
    rewrite_count: int
    agentic_cost_usd: float | None


def evaluate_page(
    *,
    page_num: int,
    bad_flat: str,
    fixed_flat: str,
    v1_rows: list[dict[str, str]],
    v2_rows: list[dict[str, str]],
    gold_flat: str,
    agentic_usage: dict,
) -> PageMetrics:
    v1_hw = headwords_from_rows(v1_rows)
    v2_hw = headwords_from_rows(v2_rows)
    gold_hw = v2_hw if v2_hw else headwords_from_flat(gold_flat)
    v1_only = v1_hw - gold_hw

    def recall(found_set: set[str], target: set[str]) -> float:
        if not target:
            return 1.0
        return len(found_set & target) / len(target)

    before_v1_only = headwords_in_text(bad_flat, v1_only)
    after_v1_only = headwords_in_text(fixed_flat, v1_only)
    before_gold = headwords_in_text(bad_flat, gold_hw)
    after_gold = headwords_in_text(fixed_flat, gold_hw)

    return PageMetrics(
        page_num=page_num,
        v1_headwords=len(v1_hw),
        v2_headwords=len(gold_hw),
        bad_v1_only=len(v1_only),
        bad_recall_before=recall(before_v1_only, v1_only),
        bad_recall_after=recall(after_v1_only, v1_only),
        gold_recall_before=recall(before_gold, gold_hw),
        gold_recall_after=recall(after_gold, gold_hw),
        gold_flat_similarity_before=SequenceMatcher(None, bad_flat, gold_flat).ratio(),
        gold_flat_similarity_after=SequenceMatcher(None, fixed_flat, gold_flat).ratio(),
        stop_reason=str(agentic_usage.get("stop_reason", "")),
        rewrite_count=int(agentic_usage.get("rewrite_count", 0)),
        agentic_cost_usd=agentic_usage.get("total_cost_usd"),
    )


def run_page(
    *,
    strategy: TwoStageLLMExtraction,
    pdf_path: Path,
    out_dir: Path,
    page_num: int,
    bad_flat: str,
) -> tuple[str, dict]:
    image_path = render_pdf_page(
        pdf_path,
        page_num,
        out_dir / f"page_{page_num}" / f"page_{page_num}.png",
    )
    ocr_result = OCRPageResult(
        source_image=str(image_path),
        backend="synthetic_v1_hallucination",
        raw_text="",
    )
    artifact_dir = out_dir / f"page_{page_num}" / "agentic" / "stage1"
    fixed_flat, usage = strategy._run_stage1_agentic_loop(
        initial_output=bad_flat,
        image_path=str(image_path),
        ocr_result=ocr_result,
        artifact_dir=artifact_dir,
        output_suffix=".txt",
        page_context=None,
    )
    page_dir = out_dir / f"page_{page_num}"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "initial_bad_stage1.txt").write_text(bad_flat, encoding="utf-8")
    (page_dir / "agentic_fixed_stage1.txt").write_text(fixed_flat, encoding="utf-8")
    return fixed_flat, usage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-tsv", type=Path, default=DEFAULT_V1)
    parser.add_argument("--v2-tsv", type=Path, default=DEFAULT_V2)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pages", type=int, nargs="+", default=list(DEFAULT_BAD_PAGES))
    parser.add_argument("--model", default="gemini/gemini-3.5-flash")
    parser.add_argument("--agentic-max-iterations", type=int, default=3)
    parser.add_argument(
        "--agentic-reasoning",
        choices=["none", "low", "medium", "high"],
        default="low",
        help="Shared fallback reasoning effort for agentic calls.",
    )
    parser.add_argument(
        "--agentic-evaluator-reasoning",
        choices=["none", "low", "medium", "high"],
        default="high",
        help="Reasoning effort for verifier/evaluator calls.",
    )
    parser.add_argument(
        "--agentic-rewriter-reasoning",
        choices=["none", "low", "medium", "high"],
        default="low",
        help="Reasoning effort for correction/rewrite calls.",
    )
    parser.add_argument(
        "--agentic-max-patches-per-attempt",
        type=int,
        default=16,
        help="Max verifier-provided exact patches per correction round.",
    )
    parser.add_argument(
        "--catastrophic-recovery",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable full-page re-transcription on catastrophic Stage 1 failures.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    v1_rows = load_rows(args.v1_tsv)
    v2_rows = load_rows(args.v2_tsv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    strategy = TwoStageLLMExtraction(
        transcribe_model=args.model,
        stage1_mode="flat",
        stage1_agentic=True,
        agentic_max_iterations=args.agentic_max_iterations,
        agentic_reasoning_effort=args.agentic_reasoning,
        agentic_evaluator_reasoning_effort=args.agentic_evaluator_reasoning,
        agentic_rewriter_reasoning_effort=args.agentic_rewriter_reasoning,
        agentic_max_patches_per_attempt=args.agentic_max_patches_per_attempt,
        agentic_catastrophic_recovery=args.catastrophic_recovery,
    )

    all_metrics: list[PageMetrics] = []
    for page_num in args.pages:
        v1_page = rows_for_page(v1_rows, page_num)
        v2_page = rows_for_page(v2_rows, page_num)
        gold_flat = mdf_rows_to_flat(v2_page) if v2_page else pdf_page_text(args.pdf, page_num)
        bad_flat = mdf_rows_to_flat(v1_page)
        if not bad_flat.strip():
            print(f"page_{page_num}: skip (empty v1 flat)")
            continue
        print(f"\n{'=' * 60}\npage_{page_num}: bad flat {len(bad_flat)} chars, v1={len(v1_page)} rows, v2={len(v2_page)} rows")
        if args.dry_run:
            print(bad_flat[:400])
            continue

        fixed_flat, usage = run_page(
            strategy=strategy,
            pdf_path=args.pdf,
            out_dir=args.output_dir,
            page_num=page_num,
            bad_flat=bad_flat,
        )
        metrics = evaluate_page(
            page_num=page_num,
            bad_flat=bad_flat,
            fixed_flat=fixed_flat,
            v1_rows=v1_page,
            v2_rows=v2_page,
            gold_flat=gold_flat,
            agentic_usage=usage,
        )
        all_metrics.append(metrics)
        print(
            f"  stop={metrics.stop_reason} rewrites={metrics.rewrite_count} "
            f"gold_recall {metrics.gold_recall_before:.2f}->{metrics.gold_recall_after:.2f} "
            f"bad_recall {metrics.bad_recall_before:.2f}->{metrics.bad_recall_after:.2f} "
            f"flat_sim {metrics.gold_flat_similarity_before:.2f}->{metrics.gold_flat_similarity_after:.2f}"
        )

    summary = {
        "model": args.model,
        "agentic_max_iterations": args.agentic_max_iterations,
        "agentic_reasoning": args.agentic_reasoning,
        "agentic_evaluator_reasoning": args.agentic_evaluator_reasoning,
        "agentic_rewriter_reasoning": args.agentic_rewriter_reasoning,
        "agentic_max_patches_per_attempt": args.agentic_max_patches_per_attempt,
        "catastrophic_recovery": args.catastrophic_recovery,
        "pages": [m.__dict__ for m in all_metrics],
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if all_metrics:
        avg_gold_before = sum(m.gold_recall_before for m in all_metrics) / len(all_metrics)
        avg_gold_after = sum(m.gold_recall_after for m in all_metrics) / len(all_metrics)
        avg_bad_before = sum(m.bad_recall_before for m in all_metrics) / len(all_metrics)
        avg_bad_after = sum(m.bad_recall_after for m in all_metrics) / len(all_metrics)
        print(f"\nSummary ({len(all_metrics)} pages)")
        print(f"  gold headword recall: {avg_gold_before:.3f} -> {avg_gold_after:.3f}")
        print(f"  spurious v1 headword recall: {avg_bad_before:.3f} -> {avg_bad_after:.3f}")
        print(f"  wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
