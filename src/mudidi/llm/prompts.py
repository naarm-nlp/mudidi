"""
Stage 1 prompt builders.

Templates live under ``mudidi/assets/prompts``. Complete messages are rendered from
their template files so their optional sections remain visible beside their text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mudidi.schemas.dictionary_profile import DictionaryProfile

from mudidi.config.run_config import PromptMode
from mudidi.llm.prompt_mode import prompt_id_for_mode
from mudidi.llm.prompt_store import get_prompt_store
from mudidi.utils.page_context import PageContext


def page_boundary_rules_prompt() -> str:
    """Return the Stage 2 ``page_boundary_rules`` prompt template."""
    return get_prompt_store().get("page_boundary_rules")


def stage_1_system_prompt(
    mode: PromptMode = "benchmark",
    *,
    typography: bool = False,
) -> str:
    """Stage 1 column-mode system prompt."""
    return get_prompt_store().format(
        "stage_1_column_system",
        typography=mode == "benchmark" or typography,
    )


def stage_1_flat_system_prompt(
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
    *,
    typography: bool = False,
) -> str:
    """Stage 1 flat-mode system prompt."""
    del page_context
    prompt_id = prompt_id_for_mode("stage_1_system", mode)
    return get_prompt_store().format(
        prompt_id,
        typography=mode == "benchmark" or typography,
    )


def stage_1_user(
    alphabet_text: str = "",
    ocr_hint: str = "",
    guides: str = "",
    dictionary_profile: "DictionaryProfile | None" = None,
) -> str:
    """
    Build the user-turn prompt for Stage 1 transcription.

    Args:
        alphabet_text: The alphabet/legend for the script (text form).
        ocr_hint: Optional existing OCR output as a character-shape reference.
        guides: Optional user-defined guidelines appended verbatim at the end.
    """
    return get_prompt_store().format(
        "stage_1_user",
        alphabet_text=alphabet_text,
        ocr_hint=ocr_hint,
        dictionary_profile_context=(
            dictionary_profile.stage1_context_hint() if dictionary_profile is not None else ""
        ),
        guides=guides,
    )
