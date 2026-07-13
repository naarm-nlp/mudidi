"""Tests for the optional user-authored dictionary profile."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mudidi.schemas.dictionary_profile import DictionaryProfile, ProfileLanguage


def test_dictionary_profile_formats_stage_prompts_from_five_answers() -> None:
    profile = DictionaryProfile(
        headword=ProfileLanguage(language="Chukchi", script="Cyrillic"),
        targets=[ProfileLanguage(language="Russian", script="Cyrillic")],
        page_layout="There are two columns; each contains independent entries.",
        information_types=["translation", "part_of_speech", "other"],
        other_information_types="dialect labels, semantic domains",
    )

    stage1 = profile.stage1_context_hint()
    pass1 = profile.pass1_config_hint()

    assert "Chukchi" in stage1
    assert "Cyrillic" in stage1
    assert "two columns" in stage1
    assert "Russian" in pass1
    assert "part_of_speech" in pass1
    assert "dialect labels" in pass1


def test_dictionary_profile_requires_at_least_one_target_and_information_type() -> None:
    with pytest.raises(ValidationError):
        DictionaryProfile(
            headword=ProfileLanguage(language="Na", script="Latin"),
            targets=[],
            page_layout="aligned_language_columns",
            information_types=[],
        )


def test_other_information_types_require_other_selection_and_free_text() -> None:
    common = {
        "headword": ProfileLanguage(language="Na", script="Latin"),
        "targets": [ProfileLanguage(language="English", script="Latin")],
        "page_layout": "Entries run across two columns.",
    }

    with pytest.raises(ValidationError, match="other_information_types"):
        DictionaryProfile(
            **common,
            information_types=["translation", "other"],
        )

    with pytest.raises(ValidationError, match="other"):
        DictionaryProfile(
            **common,
            information_types=["translation"],
            other_information_types="dialect label",
        )
