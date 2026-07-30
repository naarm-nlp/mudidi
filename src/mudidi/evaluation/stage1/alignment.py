"""
Stage 1 flat alignment: OmniDocBench quick_match (line-level) or page collapse.

Each flat line is one alignment unit. quick_match merges adjacent pred lines
when a gold line was split across multiple OCR lines (Adjacency Search Match).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from mudidi.evaluation.stage1.quick_match import quick_match_lines
from mudidi.evaluation.stage1.tag_parser import (
    normalize_line_text,
    strip_tags,
)

Row = Dict[str, str]


@dataclass(frozen=True)
class RowSpan:
    """One or more flat lines treated as one alignment unit."""

    span_id: str
    rows: List[Row]
    start_index: int
    end_index: int
    text: str
    tagged_text: str

    @property
    def row_count(self) -> int:
        return self.end_index - self.start_index + 1


@dataclass(frozen=True)
class AlignedSpanPair:
    """One matched or unmatched alignment unit."""

    pred: Optional[RowSpan]
    gold: Optional[RowSpan]
    similarity: float
    text_edit: float


@dataclass(frozen=True)
class AlignmentResult:
    """Alignment output shared by Stage 1 character and typography metrics."""

    pairs: List[AlignedSpanPair]
    pred_rows: List[Row]
    gold_rows: List[Row]

    @property
    def matched_count(self) -> int:
        return sum(1 for pair in self.pairs if pair.pred and pair.gold)

    @property
    def missing_count(self) -> int:
        return sum(1 for pair in self.pairs if pair.gold and not pair.pred)

    @property
    def extra_count(self) -> int:
        return sum(1 for pair in self.pairs if pair.pred and not pair.gold)


def clean_text(text: str) -> str:
    """Tag-strip and normalize text for semantic matching and text metrics."""
    return normalize_line_text(strip_tags(text))


def _span_text(rows: List[Row], *, tagged: bool) -> str:
    parts = [r.get("text", "") if tagged else clean_text(r.get("text", "")) for r in rows]
    joined = " ".join(p for p in parts if p)
    return normalize_line_text(joined) if not tagged else joined


def _rows_for_indices(rows: List[Row], indices: List[int]) -> List[Row]:
    return [rows[i] for i in indices]


def _make_span(
    rows: List[Row],
    indices: List[int],
    *,
    prefix: str,
    tagged: bool,
) -> RowSpan:
    selected = _rows_for_indices(rows, indices)
    start = indices[0]
    end = indices[-1]
    return RowSpan(
        span_id=f"{prefix}{start}_{end}",
        rows=selected,
        start_index=start,
        end_index=end,
        text=_span_text(selected, tagged=False),
        tagged_text=_span_text(selected, tagged=True),
    )


def collapse_rows_to_page(rows: List[Row]) -> List[Row]:
    """Collapse all rows into one synthetic page row (tagged text in ``text``)."""
    if not rows:
        return []
    tagged = _span_text(rows, tagged=True)
    return [{"column_id": "page", "line_number": "1", "text": tagged}]


def _quick_match_to_pairs(
    pred_rows: List[Row],
    gold_rows: List[Row],
    raw_matches: List[Dict[str, object]],
) -> List[AlignedSpanPair]:
    pairs: List[AlignedSpanPair] = []
    for idx, match in enumerate(raw_matches):
        gold_indices: List[int] = match["gold_indices"]  # type: ignore[assignment]
        pred_indices: List[int] = match["pred_indices"]  # type: ignore[assignment]
        text_edit = float(match["edit"])  # type: ignore[arg-type]

        gold_span = (
            _make_span(gold_rows, gold_indices, prefix=f"g{idx}_", tagged=True)
            if gold_indices
            else None
        )
        pred_span = (
            _make_span(pred_rows, pred_indices, prefix=f"p{idx}_", tagged=True)
            if pred_indices
            else None
        )
        pairs.append(
            AlignedSpanPair(
                pred=pred_span,
                gold=gold_span,
                similarity=max(0.0, 1.0 - text_edit),
                text_edit=text_edit,
            )
        )

    pairs.sort(
        key=lambda pair: (
            pair.pred.start_index if pair.pred else float("inf"),
            pair.gold.start_index if pair.gold else float("inf"),
        )
    )
    return pairs


def align_lines_quick_match(
    pred_rows: List[Row],
    gold_rows: List[Row],
) -> AlignmentResult:
    """Align flat lines with OmniDocBench quick_match (Adjacency Search Match)."""
    norm_gold = [clean_text(r.get("text", "")) for r in gold_rows]
    norm_pred = [clean_text(r.get("text", "")) for r in pred_rows]
    gold_tagged = [r.get("text", "") for r in gold_rows]
    pred_tagged = [r.get("text", "") for r in pred_rows]

    raw = quick_match_lines(norm_gold, norm_pred, gold_tagged, pred_tagged)
    pairs = _quick_match_to_pairs(pred_rows, gold_rows, raw)
    return AlignmentResult(pairs=pairs, pred_rows=pred_rows, gold_rows=gold_rows)


def align_page_collapsed(
    pred_rows: List[Row],
    gold_rows: List[Row],
) -> AlignmentResult:
    """Align pred/gold as single collapsed page spans (line split/merge invariant)."""
    return align_lines_quick_match(
        collapse_rows_to_page(pred_rows),
        collapse_rows_to_page(gold_rows),
    )
