"""Neighbor-page context for inference-mode extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mudidi.utils.stage2_page_selection import sort_snippet_pages

logger = logging.getLogger(__name__)

# Characters kept from each neighbor's Stage 1 transcript: the end of the
# previous page, so lines at the top of the current page that continue an
# earlier entry get the right MDF markers, and the start of the next page, so
# the lines at the bottom of the current page do too.
NEIGHBOR_EXCERPT_CHARS = 500


@dataclass(frozen=True)
class NeighborPage:
    """One adjacent dictionary page used as cross-page entry context."""

    stem: str
    image_path: Path
    transcript: str = ""


@dataclass(frozen=True)
class PageContext:
    """Previous- and next-page context for the current snippet."""

    previous: Optional[NeighborPage]
    current_stem: str
    next: Optional[NeighborPage] = None

    @property
    def has_previous(self) -> bool:
        return self.previous is not None

    @property
    def has_next(self) -> bool:
        return self.next is not None


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
    Build previous- and next-page context for ``pages[index]``.

    Args:
        pages: Snippet page paths in the caller's processing order.
        index: Index of the current page in ``pages`` (same list passed to the loop).
        transcript_loader: Optional callback ``stem -> transcript text``. The
            next page's transcript exists only once Stage 1 has finished for it,
            which the Stage 2 phase guarantees; otherwise it loads as empty.
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
        next_path = ordered[pos + 1]
        next_page = NeighborPage(
            stem=next_path.stem,
            image_path=next_path,
            transcript=loader(next_path.stem),
        )

    return PageContext(previous=previous, current_stem=stem, next=next_page)


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
        f"\"{page_context.current_stem}\" is this page's file name in the run. It is "
        f"not the page number printed on the page, which may differ; the "
        f"transcription above and the page image are this page.\n"
        f"Emit MDF for every line printed on this page, and only those lines.\n"
        f"If the top of the page continues a field or entry begun on the previous "
        f"page, emit that text first as the page boundary rules describe, without a "
        f"\\lx line and without repeating or inventing the parent headword.\n"
        f"If the last entry runs past the bottom of the page, stop where the page "
        f"stops; use <next_page> only to choose the right markers for those last "
        f"lines.\n"
        f"IMPORTANT: Only emit content that is visibly present in the current page's "
        f"transcription. Do NOT copy text from <previous_page> or <next_page> (the one "
        f"exception is finishing a word hyphenated across the page break, as the page "
        f"boundary rules describe), and do NOT infer, recall, or complete senses or "
        f"sub-fields from prior knowledge or earlier entries seen in this "
        f"conversation.\n"
        f"</current_page>"
    )


def format_page_image_order_note(page_context: PageContext) -> str:
    """Explain which page the single page image in the user message shows."""
    note = (
        f"The page image in this message is the CURRENT page "
        f"(file {page_context.current_stem}) — emit MDF for this page."
    )
    neighbors = [
        label
        for label, present in (
            ("previous", page_context.has_previous),
            ("next", page_context.has_next),
        )
        if present
    ]
    if len(neighbors) == 1:
        note += (
            f" The {neighbors[0]} page is provided as a transcript excerpt only, "
            "not as an image."
        )
    elif neighbors:
        note += (
            " The previous and next pages are provided as transcript excerpts "
            "only, not as images."
        )
    return note


def previous_page_excerpt(
    transcript: str,
    *,
    limit: int = NEIGHBOR_EXCERPT_CHARS,
) -> tuple[str, bool]:
    """Keep whole lines from the end of ``transcript`` up to ``limit`` characters.

    Returns the excerpt and whether any lines were left out. At least one line
    is always kept, even when it is longer than ``limit``.
    """
    lines = transcript.strip().splitlines()
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if kept and used + cost > limit:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return "\n".join(kept), len(kept) < len(lines)


def format_previous_page_block(page: Optional[NeighborPage]) -> str:
    """Format the end of the previous page as a text block for prompt injection."""
    label = "previous_page"
    if page is None:
        return f"<{label}>\n(none)\n</{label}>"
    excerpt, truncated = previous_page_excerpt(page.transcript)
    scope = (
        "This is only the END of the previous page: its last lines, ending "
        "at the bottom of that page. Earlier lines are omitted."
        if truncated
        else "This is the whole previous page; its last lines sit directly "
        "above the top of the CURRENT page."
    )
    usage = (
        "Use it only to work out which entry, sense and field the lines at the "
        "top of the CURRENT page continue, so they get the right MDF markers. "
        "It is context only — do not copy any of this text into the output."
    )
    omitted = f"[... earlier lines of {page.stem} omitted ...]\n"
    transcript_body = f"{omitted}{excerpt}" if truncated else excerpt
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


def next_page_excerpt(
    transcript: str,
    *,
    limit: int = NEIGHBOR_EXCERPT_CHARS,
) -> tuple[str, bool]:
    """Keep whole lines from the start of ``transcript`` up to ``limit`` characters.

    Returns the excerpt and whether any lines were left out. At least one line
    is always kept, even when it is longer than ``limit``.
    """
    lines = transcript.strip().splitlines()
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if kept and used + cost > limit:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept), len(kept) < len(lines)


def format_next_page_block(page: Optional[NeighborPage]) -> str:
    """Format the start of the next page as a text block for prompt injection."""
    label = "next_page"
    if page is None:
        return f"<{label}>\n(none)\n</{label}>"
    excerpt, truncated = next_page_excerpt(page.transcript)
    scope = (
        "This is only the START of the next page: its first lines, beginning "
        "at the top of that page. Later lines are omitted."
        if truncated
        else "This is the whole next page; its first lines sit directly below "
        "the bottom of the CURRENT page."
    )
    usage = (
        "Use it only to work out what the lines at the bottom of the CURRENT page "
        "are (which field each belongs to, and whether a field or word continues "
        "past the page break), so they get the right MDF markers. It is context "
        "only — do not copy its text into the output, except to finish a word "
        "hyphenated across the page break."
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
