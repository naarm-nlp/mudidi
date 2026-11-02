"""Tests for neighbor-page context formatting."""

from __future__ import annotations

from pathlib import Path

from mudidi.utils.page_context import (
    NeighborPage,
    PageContext,
    build_page_context,
    format_current_page_block,
    format_next_page_block,
    format_page_image_order_note,
    format_previous_page_block,
    next_page_excerpt,
    previous_page_excerpt,
)


def test_build_previous_page_for_sparse_page_list(tmp_path: Path) -> None:
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
    assert not ctx_53.has_previous

    ctx_54 = build_page_context(pages, 1, transcript_loader=lambda stem: f"text {stem}")
    assert ctx_54.previous is not None
    assert ctx_54.previous.stem == "page_53"
    assert ctx_54.previous.transcript == "text page_53"


def test_build_page_context_loads_both_neighbor_transcripts(tmp_path: Path) -> None:
    pages = [tmp_path / f"page_{n}.pdf" for n in (1, 2, 3)]
    loaded: list[str] = []

    def loader(stem: str) -> str:
        loaded.append(stem)
        return f"text {stem}"

    middle = build_page_context(pages, 1, transcript_loader=loader)
    last = build_page_context(pages, 2, transcript_loader=loader)

    assert loaded == ["page_1", "page_3", "page_2"]
    assert middle.next is not None and middle.next.transcript == "text page_3"
    assert last.next is None and not last.has_next


def test_current_page_block_is_page_local() -> None:
    ctx = PageContext(
        previous=NeighborPage("page_53", Path("page_53.pdf")),
        current_stem="page_54",
    )
    block = format_current_page_block(ctx)
    assert "page: page_54" in block
    assert "<current_page>" in block
    assert "every line printed on this page, and only those lines" in block
    assert "without a \\lx line" in block
    assert "use <next_page> only to choose the right markers" in block

    order = format_page_image_order_note(ctx)
    assert "CURRENT page (file page_54)" in order
    assert "not the page number printed on the page" in block
    assert "transcript excerpt only" in order
    assert "page_53" not in order


def test_image_order_note_names_only_the_neighbors_that_exist() -> None:
    first = format_page_image_order_note(PageContext(previous=None, current_stem="page_1"))
    both = format_page_image_order_note(
        PageContext(
            previous=NeighborPage("page_1", Path("page_1.pdf")),
            current_stem="page_2",
            next=NeighborPage("page_3", Path("page_3.pdf")),
        )
    )

    assert "previous" not in first and "next" not in first
    assert "previous and next pages are provided as transcript excerpts" in both


def test_previous_page_excerpt_keeps_whole_lines_from_the_end() -> None:
    transcript = "\n".join(f"line {i:02d}" for i in range(10))

    tail, truncated = previous_page_excerpt(transcript, limit=24)

    assert tail == "line 07\nline 08\nline 09"
    assert truncated


def test_previous_page_excerpt_keeps_short_pages_and_one_overlong_line() -> None:
    assert previous_page_excerpt("a\nb") == ("a\nb", False)
    assert previous_page_excerpt("x" * 50, limit=10) == ("x" * 50, False)


def test_previous_page_block_holds_the_end_of_the_page_as_context_only() -> None:
    transcript = "\n".join(f"entry {i:03d} " + "x" * 40 for i in range(100))
    block = format_previous_page_block(
        NeighborPage("page_53", Path("page_53.pdf"), transcript)
    )

    assert block.startswith("<previous_page>")
    assert "only the END of the previous page" in block
    assert "entry 099" in block and "entry 000" not in block
    assert "[... earlier lines of page_53 omitted ...]" in block
    assert "do not copy any of this text into the output" in block


def test_previous_page_block_reports_a_whole_short_page() -> None:
    block = format_previous_page_block(
        NeighborPage("page_53", Path("page_53.pdf"), "ala n. fish")
    )

    assert "whole previous page" in block
    assert "omitted" not in block
    assert "ala n. fish" in block


def test_previous_page_block_without_a_previous_page() -> None:
    assert format_previous_page_block(None) == "<previous_page>\n(none)\n</previous_page>"


def test_next_page_block_holds_the_start_of_the_page_as_context_only() -> None:
    transcript = "\n".join(f"entry {i:03d} " + "x" * 40 for i in range(100))
    block = format_next_page_block(
        NeighborPage("page_55", Path("page_55.pdf"), transcript)
    )

    assert block.startswith("<next_page>")
    assert "only the START of the next page" in block
    assert "entry 000" in block and "entry 099" not in block
    assert block.index("entry 000") < block.index(
        "[... later lines of page_55 omitted ...]"
    )
    assert "except to finish a word hyphenated across the page break" in block


def test_next_page_excerpt_keeps_whole_lines_from_the_start() -> None:
    transcript = "\n".join(f"line {i:02d}" for i in range(10))

    assert next_page_excerpt(transcript, limit=24) == ("line 00\nline 01\nline 02", True)
    assert next_page_excerpt("x" * 50, limit=10) == ("x" * 50, False)
    assert format_next_page_block(None) == "<next_page>\n(none)\n</next_page>"
