"""Tests for neighbor page context formatting."""

from __future__ import annotations

from pathlib import Path

from mudidi.utils.page_context import (
    format_current_page_block,
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
