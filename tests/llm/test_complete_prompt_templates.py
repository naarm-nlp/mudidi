"""Contracts for complete, human-readable Stage 1 and Stage 2 prompt templates."""

from __future__ import annotations

import pytest

from mudidi.llm.prompt_store import configure_prompts, default_prompts_path, get_prompt_store
from mudidi.llm.prompts import stage_1_user


@pytest.fixture(autouse=True)
def _load_default_prompts() -> None:
    configure_prompts(default_prompts_path())


def test_stage1_user_template_renders_only_the_context_supplied() -> None:
    prompt = stage_1_user(mode="benchmark")

    assert prompt == (
        "Now transcribe every line of text from the dictionary page image exactly as "
        "it appears. Preserve all diacritics and special characters."
    )


def test_stage1_user_template_exposes_all_conditional_context() -> None:
    prompt = stage_1_user(
        mode="benchmark",
        alphabet_text="A B C",
        ocr_hint="OCR sample",
        guides="Retain abbreviations.",
    )

    assert "<alphabet>\nA B C\n</alphabet>" in prompt
    assert "<ocr_reference>\nOCR sample\n</ocr_reference>" in prompt
    assert "USER DEFINED GUIDELINES\nRetain abbreviations." in prompt


def test_stage1_benchmark_user_prompt_excludes_dictionary_profile_context() -> None:
    profile = {
        "headword": {"language": "Evenki", "script": "Cyrillic"},
        "targets": [{"language": "Russian", "script": "Cyrillic"}],
        "page_layout": "two columns",
        "information_types": ["translation"],
    }

    prompt = stage_1_user(mode="benchmark", dictionary_profile=profile)

    assert "<dictionary_profile>" not in prompt
    assert "Evenki" not in prompt


def test_pass2_user_template_declares_toolbox_reference_variants() -> None:
    prompt = get_prompt_store().get("stage_2_pass_2_user_benchmark")

    assert "toolbox_reference_mode == 'pdf'" in prompt
    assert "toolbox_reference_mode == 'text_fallback'" in prompt
    assert "mdf_marker_reference" in prompt
