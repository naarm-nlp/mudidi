"""Neighbor page context for inference-mode extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mudidi.utils.stage2_page_selection import sort_snippet_pages

logger = logging.getLogger(__name__)

# Characters of each neighbor transcript kept for cross-page entry context:
# the end of the previous page and the start of the next page.
NEIGHBOR_EXCERPT_CHARS = 1500


@dataclass(frozen=True)
class NeighborPage:
    """One adjacent dictionary page used as cross-page entry context."""

    stem: str
    image_path: Path
    transcript: str = ""


@dataclass(frozen=True)
class PageContext:
    """Previous and next page context for the current snippet."""

    previous: Optional[NeighborPage]
    next: Optional[NeighborPage]
    current_stem: str

    @property
    def has_neighbors(self) -> bool:
        return self.previous is not None or self.next is not None


TranscriptLoader = Callable[[str], str]


def _empty_loader(_stem: str) -> str:
    return ""


def build_page_context(
    pages: list[Path],
    index: int,
    *,
    transcript_loader: TranscriptLoader | None = None,
) -> PageContext:
    """
    Build neighbor context for ``pages[index]``.

    Args:
        pages: Snippet page paths in the caller's processing order.
        index: Index of the current page in ``pages`` (same list passed to the loop).
        transcript_loader: Optional callback ``stem -> transcript text`` for
            pages already processed in the current run.
    """
    loader = transcript_loader or _empty_loader
    ordered = sort_snippet_pages(pages)
    current = pages[index]
    try:
        pos = next(i for i, p in enumerate(ordered) if p == current)
    except StopIteration as exc:
        raise ValueError(
            f"Page {current} at index {index} not found in sorted snippet list."
        ) from exc
    stem = current.stem

    previous: Optional[NeighborPage] = None
    next_page: Optional[NeighborPage] = None

    if pos > 0:
        prev_path = ordered[pos - 1]
        previous = NeighborPage(
            stem=prev_path.stem,
            image_path=prev_path,
            transcript=loader(prev_path.stem),
        )
    if pos + 1 < len(ordered):
        nxt_path = ordered[pos + 1]
        next_page = NeighborPage(
            stem=nxt_path.stem,
            image_path=nxt_path,
            transcript=loader(nxt_path.stem),
        )

    return PageContext(previous=previous, next=next_page, current_stem=stem)


def format_current_page_block(page_context: PageContext, *, ocr: bool = False) -> str:
    """Identify the page being processed (matches the page image sent)."""
    if ocr:
        return (
            f"<current_page>\n"
            f"page: {page_context.current_stem}\n"
            f"Transcribe every line visible on this page image (header, body, footer). "
            f"Do not skip lines.\n"
            f"</current_page>"
        )
    return (
        f"<current_page>\n"
        f"page: {page_context.current_stem}\n"
        f"Emit MDF for:\n"
        f"  1. All entries whose main headword (\\lx) starts on this page.\n"
        f"  2. Subentries (\\se) whose subentry heading appears on this page, even if the "
        f"parent \\lx started on a previous page.\n"
        f"Include all sub-fields (\\se, \\va, senses, examples) for those entries even when "
        f"they print on the next page — copy characters from <next_page> transcript.\n"
        f"If a \\se heading starts on this page but its parent \\lx started on a previous page, "
        f"emit the \\se block without inventing or repeating the parent \\lx.\n"
        f"Do not re-emit the parent \\lx record or any \\se subentries already captured on a previous page.\n"
        f"IMPORTANT: Only emit content that is visibly present in the page image or transcripts "
        f"provided. Do NOT infer, recall, or complete senses or sub-fields from prior knowledge "
        f"or earlier entries seen in this conversation.\n"
        f"</current_page>"
    )


def format_page_image_order_note(page_context: PageContext) -> str:
    """Explain which page the single page image in the user message shows."""
    note = (
        f"The page image in this message is the CURRENT page "
        f"({page_context.current_stem}) — emit MDF for this page."
    )
    if page_context.has_neighbors:
        note += " Neighbor pages are provided as transcripts only, not as images."
    return note


def neighbor_transcript_excerpt(
    transcript: str,
    *,
    from_end: bool,
    limit: int = NEIGHBOR_EXCERPT_CHARS,
) -> tuple[str, bool]:
    """Keep whole lines from one end of ``transcript`` up to ``limit`` characters.

    Returns the excerpt and whether any lines were left out. At least one line
    is always kept, even when it is longer than ``limit``.
    """
    lines = transcript.strip().splitlines()
    if from_end:
        lines.reverse()
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if kept and used + cost > limit:
            break
        kept.append(line)
        used += cost
    truncated = len(kept) < len(lines)
    if from_end:
        kept.reverse()
    return "\n".join(kept), truncated


def format_neighbor_text_block(
    page: Optional[NeighborPage],
    *,
    label: str,
) -> str:
    """Format a neighbor page as a text block for prompt injection.

    ``label`` ``previous_page`` yields the end of that page; any other label
    yields the start of the page.
    """
    if page is None:
        return f"<{label}>\n(none)\n</{label}>"
    is_previous = label == "previous_page"
    excerpt, truncated = neighbor_transcript_excerpt(
        page.transcript, from_end=is_previous
    )
    if is_previous:
        scope = (
            "This is only the END of the previous page: its last lines, ending "
            "at the bottom of that page. Earlier lines are omitted."
            if truncated
            else "This is the whole previous page; its last lines sit directly "
            "above the top of the CURRENT page."
        )
        usage = (
            "Use it to detect lines at the top of the CURRENT page that continue "
            "an entry whose \\lx started on the previous page — exclude those "
            "from the current output."
        )
        omitted = f"[... earlier lines of {page.stem} omitted ...]\n"
        transcript_body = f"{omitted}{excerpt}" if truncated else excerpt
    else:
        scope = (
            "This is only the START of the next page: its first lines, beginning "
            "at the top of that page. Later lines are omitted."
            if truncated
            else "This is the whole next page; its first lines follow directly "
            "after the bottom of the CURRENT page."
        )
        usage = (
            "Use it to complete sub-fields for entries owned by the CURRENT page "
            "when they overflow onto the next page. Do not emit entries whose "
            "\\lx starts on the next page."
        )
        omitted = f"\n[... later lines of {page.stem} omitted ...]"
        transcript_body = f"{excerpt}{omitted}" if truncated else excerpt
    transcript_section = (
        f"\n<transcript>\n{transcript_body}\n</transcript>" if excerpt else ""
    )
    return (
        f"<{label}>\n"
        f"page: {page.stem}\n"
        f"Cross-page entry context. {scope} {usage}"
        f"{transcript_section}\n"
        f"</{label}>"
    )
