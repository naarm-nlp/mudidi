"""Contracts for the direct-MDF extraction result."""

from mudidi.schemas.extraction_result import ExtractionResult


def test_extraction_result_contains_only_direct_mdf_metadata() -> None:
    result = ExtractionResult(
        page_number=7,
        source_file="page_7.png",
        mdf_text="\\lx example\n\\ge gloss\n",
    )

    assert result.model_dump() == {
        "page_number": 7,
        "source_file": "page_7.png",
        "mdf_text": "\\lx example\n\\ge gloss\n",
    }
