"""Resolve prompt ids for benchmark vs inference modes."""

from __future__ import annotations

from mudidi.config.run_config import PromptMode
from mudidi.llm.prompt_store import get_prompt_store

_MODE_SUFFIXED = frozenset(
    {
        "stage_1_system",
        "stage_2_pass_2_system",
        "stage_2_pass_2_user",
    }
)


def prompt_id_for_mode(base_id: str, mode: PromptMode) -> str:
    """
    Return the prompt id for ``mode``, falling back to ``base_id``.

    Prompts that differ by mode use the ``_{mode}`` suffix in PROMPT.json.
    Shared prompts (alphabet, OCR hint, Stage 2 Pass 1, etc.) keep unsuffixed ids.
    """
    if base_id not in _MODE_SUFFIXED:
        return base_id
    store = get_prompt_store()
    suffixed = f"{base_id}_{mode}"
    ids = set(store.prompt_ids())
    if suffixed in ids:
        return suffixed
    if base_id in ids:
        return base_id
    raise KeyError(
        f"No prompt {suffixed!r} or {base_id!r} in {store.path}. "
        f"Available: {', '.join(sorted(ids))}"
    )
