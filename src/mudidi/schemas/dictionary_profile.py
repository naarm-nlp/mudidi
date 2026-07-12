"""Optional user-supplied context describing a dictionary's visible structure."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PageLayout = Literal[
    "inline_entries",
    "aligned_language_columns",
    "independent_entry_columns",
    "mixed_or_variable",
    "unknown",
]

InformationType = Literal[
    "translation",
    "definition",
    "gloss",
    "part_of_speech",
    "pronunciation",
    "example",
    "usage_note",
    "etymology",
    "cross_reference",
    "variant",
    "grammar",
    "other",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileLanguage(_StrictModel):
    """One language and the script used to print it in the dictionary."""

    language: str = Field(min_length=1)
    script: str = Field(min_length=1)

    @field_validator("language", "script")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DictionaryProfile(_StrictModel):
    """Answers to the five optional dictionary-context questions."""

    headword: ProfileLanguage
    targets: list[ProfileLanguage] = Field(min_length=1)
    page_layout: PageLayout
    information_types: list[InformationType] = Field(min_length=1)

    def stage1_context_hint(self) -> str:
        """Return non-authoritative transcription context for Stage 1."""

        targets = ", ".join(f"{item.language} ({item.script})" for item in self.targets)
        return (
            "<dictionary_profile>\n"
            "This profile is context only. Transcribe what is visible; do not invent, "
            "translate, or normalize text to match it.\n"
            f"Headwords: {self.headword.language} ({self.headword.script})\n"
            f"Translations/glosses/definitions: {targets}\n"
            f"Page arrangement: {self.page_layout}\n"
            f"Expected entry information: {', '.join(self.information_types)}\n"
            "</dictionary_profile>"
        )

    def pass1_config_hint(self) -> str:
        """Return compact structural context for parse-rule discovery."""

        targets = ", ".join(f"{item.language} ({item.script})" for item in self.targets)
        return (
            "Optional DictionaryProfile (use as context, verify against the page):\n"
            f"- headword language/script: {self.headword.language} / {self.headword.script}\n"
            f"- translation, gloss, or definition languages/scripts: {targets}\n"
            f"- page arrangement: {self.page_layout}\n"
            f"- entry information types: {', '.join(self.information_types)}"
        )
