"""Tests for the optional user-authored dictionary profile."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mudidi.schemas.dictionary_profile import DictionaryProfile, ProfileLanguage


def test_dictionary_profile_formats_stage_prompts_from_five_answers() -> None:
    profile = DictionaryProfile(
        headword=ProfileLanguage(language="Chukchi", script="Cyrillic"),
        targets=[ProfileLanguage(language="Russian", script="Cyrillic")],
        page_layout="inline_entries",
        information_types=["translation", "part_of_speech", "example"],
    )

    stage1 = profile.stage1_context_hint()
    pass1 = profile.pass1_config_hint()

    assert "Chukchi" in stage1
    assert "Cyrillic" in stage1
    assert "inline_entries" in stage1
    assert "Russian" in pass1
    assert "part_of_speech" in pass1


def test_dictionary_profile_requires_at_least_one_target_and_information_type() -> None:
    with pytest.raises(ValidationError):
        DictionaryProfile(
            headword=ProfileLanguage(language="Na", script="Latin"),
            targets=[],
            page_layout="aligned_language_columns",
            information_types=[],
        )
