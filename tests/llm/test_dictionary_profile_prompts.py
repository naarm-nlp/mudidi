"""Prompt wiring tests for optional DictionaryProfile guidance."""

from mudidi.llm.prompts import stage_1_user
from mudidi.schemas.dictionary_profile import DictionaryProfile, ProfileLanguage


def test_stage1_user_includes_optional_profile_as_non_authoritative_context() -> None:
    profile = DictionaryProfile(
        headword=ProfileLanguage(language="Evenki", script="Cyrillic"),
        targets=[ProfileLanguage(language="Russian", script="Cyrillic")],
        page_layout="independent_entry_columns",
        information_types=["translation", "part_of_speech"],
    )

    prompt = stage_1_user(mode="inference", dictionary_profile=profile)

    assert "<dictionary_profile>" in prompt
    assert "Evenki" in prompt
    assert "This profile is context only." in prompt


def test_stage1_user_renders_partial_profile_context() -> None:
    profile = DictionaryProfile(
        headword=ProfileLanguage(language="Chukchi", script="Cyrillic"),
    )

    prompt = stage_1_user(mode="inference", dictionary_profile=profile)

    assert "Headwords: Chukchi (Cyrillic)" in prompt
    assert "Translations/glosses/definitions:" not in prompt
    assert "Page arrangement:" not in prompt
