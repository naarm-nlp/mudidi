"""
Stage 1 prompt builders.

Templates live in ``assets/PROMPT.json``; this module assembles dynamic user turns.
"""

from __future__ import annotations

from mudidi.config.run_config import PromptMode
from mudidi.llm.prompt_mode import prompt_id_for_mode
from mudidi.llm.prompt_store import get_prompt_store
from mudidi.utils.page_context import PageContext


def page_boundary_rules_prompt() -> str:
    """Page-boundary instructions from ``page_boundary_rules`` in PROMPT.json (Stage 2 only)."""
    return get_prompt_store().get("page_boundary_rules")


def stage_1_system_prompt(
    mode: PromptMode = "benchmark",
    *,
    typography: bool = False,
) -> str:
    """Stage 1 column-mode system prompt."""
    del mode
    store = get_prompt_store()
    prompt = store.get("stage_1_column_system")
    if typography:
        prompt = "\n\n".join([prompt, store.get("stage_1_typography_instruction")])
    return prompt


def stage_1_flat_system_prompt(
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
    *,
    typography: bool = False,
) -> str:
    """Stage 1 flat-mode system prompt."""
    del page_context
    store = get_prompt_store()
    prompt_id = prompt_id_for_mode("stage_1_system", mode)
    prompt = store.get(prompt_id)
    if typography:
        prompt = "\n\n".join([prompt, store.get("stage_1_typography_instruction")])
    return prompt


def stage_1_user(
    alphabet_text: str = "",
    ocr_hint: str = "",
    guides: str = "",
) -> str:
    """
    Build the user-turn prompt for Stage 1 transcription.

    Args:
        alphabet_text: The alphabet/legend for the script (text form).
        ocr_hint: Optional existing OCR output as a character-shape reference.
        guides: Optional user-defined guidelines appended verbatim at the end.
    """
    store = get_prompt_store()
    parts: list[str] = []
    if alphabet_text:
        parts.append(store.format("stage_1_user_alphabet", alphabet_text=alphabet_text))
    if ocr_hint:
        parts.append(store.format("stage_1_user_ocr_reference", ocr_hint=ocr_hint))
    parts.append(store.get("stage_1_user_closing"))
    if guides:
        parts.append(f"USER DEFINED GUIDELINES\n{guides}")
    return "\n\n".join(parts)


