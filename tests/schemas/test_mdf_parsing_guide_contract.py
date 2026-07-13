"""Canonical MDF parsing-guide schema and dataset naming contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mudidi import paths
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet


def test_schema_has_no_dictionary_name_field() -> None:
    assert "dictionary_name" not in DictionaryMarkerCheatsheet.model_fields
    with pytest.raises(ValidationError, match="dictionary_name"):
        DictionaryMarkerCheatsheet.model_validate({"dictionary_name": "Obsolete"})


def test_canonical_output_filename_is_mdf_parsing_guide() -> None:
    assert paths.MDF_PARSING_GUIDE_FILENAME == "mdf_parsing_guide.json"


def test_dataset_gold_guides_use_canonical_filename_and_schema() -> None:
    dictionaries = Path(__file__).parents[2] / "dataset" / "MUDIDI" / "dictionaries"
    guides = sorted(
        dictionaries.glob("*/Stage 2 Gold Cheat Sheet/mdf_parsing_guide.json")
    )

    assert len(guides) == 10
    assert not list(dictionaries.glob("*/Stage 2 Gold Cheat Sheet/field_cheatsheet.json"))
    for guide in guides:
        payload = json.loads(guide.read_text(encoding="utf-8"))
        assert "dictionary_name" not in payload
        DictionaryMarkerCheatsheet.model_validate(payload)
