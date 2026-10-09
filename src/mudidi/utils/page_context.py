"""Neighbor page context for inference-mode extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mudidi.utils.stage2_page_selection import sort_snippet_pages

logger = logging.getLogger(__name__)


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


def format_neighbor_text_block(
    page: Optional[NeighborPage],
    *,
    label: str,
) -> str:
    """Format a neighbor page as a text block for prompt injection."""
    if page is None:
        return f"<{label}>\n(none)\n</{label}>"
    transcript = page.transcript.strip()
    transcript_section = (
        f"\n<transcript>\n{transcript}\n</transcript>" if transcript else ""
    )
    return (
        f"<{label}>\n"
        f"page: {page.stem}\n"
        f"Cross-page entry context. Use this transcript to (a) complete sub-fields "
        f"for entries owned by the CURRENT page when they overflow here, and "
        f"(b) detect lines at the top of the CURRENT page that belong to an entry "
        f"whose \\lx started on this neighbor — exclude those from the current output."
        f"{transcript_section}\n"
        f"</{label}>"
    )
