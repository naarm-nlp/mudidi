"""Tests for parse-rules sample page resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mudidi.utils.parse_rules_pages import (
    format_sample_pages_block,
    normalize_parse_rules_page_stems,
    select_parse_rules_sample_images,
)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (None, []),
        ("1", ["page_1"]),
        ("1-4", ["page_1", "page_2", "page_3", "page_4"]),
        ("1,3,5", ["page_1", "page_3", "page_5"]),
        (["1", "10"], ["page_1", "page_10"]),
        (["1-2,5", "7"], ["page_1", "page_2", "page_5", "page_7"]),
        ("page_1", ["page_1"]),
        (["page_1", "page_2"], ["page_1", "page_2"]),
        (["page_1,page_2", "page_3"], ["page_1", "page_2", "page_3"]),
        (["50,200"], ["page_50", "page_200"]),
    ],
)
def test_normalize_parse_rules_page_stems(
    values: str | list[str] | None,
    expected: list[str],
) -> None:
    assert normalize_parse_rules_page_stems(values) == expected


def test_normalize_parse_rules_page_stems_invalid_spec() -> None:
    with pytest.raises(ValueError, match="Unrecognised page"):
        normalize_parse_rules_page_stems(["not-a-page"])


def test_select_parse_rules_sample_images_defaults_to_all(tmp_path: Path) -> None:
    images = [tmp_path / "page_10.png", tmp_path / "page_2.png"]
    for path in images:
        path.write_bytes(b"x")
    selected = select_parse_rules_sample_images(images, [])
    assert selected == images


def test_select_parse_rules_sample_images_missing_stem(tmp_path: Path) -> None:
    image = tmp_path / "page_1.png"
    image.write_bytes(b"x")
    with pytest.raises(ValueError, match=r"not found.*['99']"):
        select_parse_rules_sample_images([image], ["page_99"])


def test_format_sample_pages_block() -> None:
    block = format_sample_pages_block(
        [
            ("page_1", "alpha line"),
            ("page_50", "beta line"),
        ]
    )
    assert '<sample_transcription page="page_1">' in block
    assert "alpha line" in block
    assert '<sample_transcription page="page_50">' in block
