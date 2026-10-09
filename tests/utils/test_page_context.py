"""Tests for neighbor page context formatting."""

from __future__ import annotations

from pathlib import Path

from mudidi.utils.page_context import (
    NeighborPage,
    format_current_page_block,
    format_neighbor_text_block,
    neighbor_transcript_excerpt,
    format_page_image_order_note,
    build_page_context,
)


def test_build_neighbors_for_sparse_page_list(tmp_path: Path) -> None:
    pages = [
        tmp_path / "page_53.pdf",
        tmp_path / "page_54.pdf",
        tmp_path / "page_77.pdf",
    ]
    for p in pages:
        p.touch()

    ctx_53 = build_page_context(pages, 0)
    assert ctx_53.current_stem == "page_53"
    assert ctx_53.previous is None
    assert ctx_53.next is not None
    assert ctx_53.next.stem == "page_54"

    ctx_54 = build_page_context(pages, 1)
    assert ctx_54.previous is not None
    assert ctx_54.previous.stem == "page_53"
    assert ctx_54.next is not None
    assert ctx_54.next.stem == "page_77"


def test_current_page_block_names_stem() -> None:
    from mudidi.utils.page_context import NeighborPage, PageContext

    ctx = PageContext(
        previous=NeighborPage("page_53", Path("page_53.pdf")),
        next=NeighborPage("page_55", Path("page_55.pdf")),
        current_stem="page_54",
    )
    block = format_current_page_block(ctx)
    assert "page: page_54" in block
    assert "\\lx" in block
    assert "<current_page>" in block

    order = format_page_image_order_note(ctx)
    assert "CURRENT page (page_54)" in order
    assert "transcripts only" in order
    assert "page_53" not in order
    assert "page_55" not in order


def test_neighbor_excerpt_keeps_whole_lines_from_the_requested_end() -> None:
    transcript = "\n".join(f"line {i:02d}" for i in range(10))

    tail, tail_truncated = neighbor_transcript_excerpt(
        transcript, from_end=True, limit=24
    )
    head, head_truncated = neighbor_transcript_excerpt(
        transcript, from_end=False, limit=24
    )

    assert tail == "line 07\nline 08\nline 09"
    assert head == "line 00\nline 01\nline 02"
    assert tail_truncated and head_truncated


def test_neighbor_excerpt_keeps_short_pages_and_one_overlong_line() -> None:
    assert neighbor_transcript_excerpt("a\nb", from_end=True) == ("a\nb", False)
    assert neighbor_transcript_excerpt("x" * 50, from_end=False, limit=10) == (
        "x" * 50,
        False,
    )


def test_neighbor_blocks_say_which_end_of_the_page_they_hold() -> None:
    transcript = "\n".join(f"entry {i:03d} " + "x" * 40 for i in range(100))
    previous = format_neighbor_text_block(
        NeighborPage("page_53", Path("page_53.pdf"), transcript),
        label="previous_page",
    )
    following = format_neighbor_text_block(
        NeighborPage("page_55", Path("page_55.pdf"), transcript),
        label="next_page",
    )

    assert "only the END of the previous page" in previous
    assert "entry 099" in previous and "entry 000" not in previous
    assert "[... earlier lines of page_53 omitted ...]" in previous
    assert "only the START of the next page" in following
    assert "entry 000" in following and "entry 099" not in following
    assert "[... later lines of page_55 omitted ...]" in following


def test_neighbor_block_reports_a_whole_short_page() -> None:
    block = format_neighbor_text_block(
        NeighborPage("page_53", Path("page_53.pdf"), "ala n. fish"),
        label="previous_page",
    )

    assert "whole previous page" in block
    assert "omitted" not in block
    assert "ala n. fish" in block
