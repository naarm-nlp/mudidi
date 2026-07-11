"""
Pass 2: direct page transcription → Toolbox MDF text using a field profile.

Used by ``TwoStageLLMExtraction`` for Stage 2 MDF output.
"""

from __future__ import annotations

import logging
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mudidi.config.prompt_cache import MediaReferenceMode, PromptCacheMode
from mudidi.config.run_config import PromptMode
from mudidi.llm import client as llm
from mudidi.llm.prompt_mode import prompt_id_for_mode
from mudidi.llm.prompt_store import get_prompt_store
from mudidi.schemas.field_map import FieldMapPrompt
from mudidi.utils.image import (
    file_content_part,
    image_data_url,
    mime_type_for_path,
    model_supports_pdf_input,
)
from mudidi.utils.mdf_export import normalize_mdf_text
from mudidi.llm.prompts import page_boundary_rules_prompt
from mudidi.utils.page_context import (
    PageContext,
    format_current_page_block,
    format_neighbor_text_block,
    format_page_image_order_note,
)
from mudidi.utils.pdf_render import needs_pdf_rasterization

logger = logging.getLogger(__name__)
_TRANSCRIPTION_SPLIT_MARKER = "__MUDIDI_TRANSCRIPTION_PLACEHOLDER__"
_TRANSCRIPTION_OPEN_TAG = "<transcription>\n"


@dataclass(frozen=True)
class DirectMdfPrompt:
    """Built Pass 2 messages plus the static text used for cache-keying."""

    messages: list[dict]
    static_text: str


def direct_mdf_system_prompt(
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
) -> str:
    """Pass 2 direct MDF system prompt."""
    store = get_prompt_store()
    prompt_id = prompt_id_for_mode("stage_2_direct_mdf_system", mode)
    if mode == "inference":
        return store.format(
            prompt_id,
            page_boundary_rules=page_boundary_rules_prompt(),
        )
    return store.get(prompt_id)


def strip_markdown_fences(text: str) -> str:
    """Remove optional markdown code fences from model output."""
    text = text.strip()
    fence = re.search(r"```(?:mdf|text)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _toolbox_content_parts(
    toolbox_pdf: Path,
    *,
    model: str,
    media_reference: MediaReferenceMode = "auto",
) -> tuple[str, list[dict]]:
    """Build prompt section and optional vision parts for the Toolbox MDF manual."""
    store = get_prompt_store()
    if media_reference != "inline" and model_supports_pdf_input(model):
        section = store.get("stage_2_toolbox_pdf_section")
        return section, [
            file_content_part(
                str(toolbox_pdf),
                mime_type="application/pdf",
                media_reference=media_reference,
            )
        ]

    if needs_pdf_rasterization(model):
        logger.info(
            "Toolbox PDF %s attached as text reference for model %s",
            toolbox_pdf.name,
            model,
        )
        section = store.format(
            "stage_2_toolbox_text_section",
            mdf_marker_reference=store.get("mdf_marker_reference"),
        )
        return section, []

    section = store.get("stage_2_toolbox_pdf_section")
    return section, [
        file_content_part(
            str(toolbox_pdf),
            mime_type="application/pdf",
            media_reference=media_reference,
        )
    ]


def _neighbor_format_kwargs(
    mode: PromptMode,
    page_context: PageContext | None,
) -> dict[str, str]:
    if mode != "inference" or page_context is None:
        return {
            "page_boundary_rules": "",
            "current_page_context": "",
            "previous_page_context": "",
            "next_page_context": "",
            "page_image_order": "",
        }
    return {
        "page_boundary_rules": page_boundary_rules_prompt(),
        "current_page_context": format_current_page_block(page_context),
        "previous_page_context": format_neighbor_text_block(
            page_context.previous, label="previous_page"
        ),
        "next_page_context": format_neighbor_text_block(
            page_context.next, label="next_page"
        ),
        "page_image_order": format_page_image_order_note(page_context),
    }


def _render_direct_mdf_user_parts(
    *,
    transcription: str,
    field_map: FieldMapPrompt,
    toolbox_section: str,
    guides: str,
    mode: PromptMode,
    page_context: PageContext | None,
) -> tuple[str, str]:
    """Render the existing Pass 2 user template, split at transcription."""
    guides_block = f"\n\nUSER DEFINED GUIDELINES\n{guides}" if guides.strip() else ""
    user_prompt_id = prompt_id_for_mode("stage_2_direct_mdf_user", mode)
    neighbor_kwargs = _neighbor_format_kwargs(mode, page_context)
    if mode == "inference":
        # Boundary rules are already in the inference system prompt. Keeping
        # this user-turn placeholder empty avoids re-sending constant text in
        # the dynamic per-page message.
        neighbor_kwargs = {**neighbor_kwargs, "page_boundary_rules": ""}
    rendered = get_prompt_store().format(
        user_prompt_id,
        transcription=_TRANSCRIPTION_SPLIT_MARKER,
        field_block=field_map.format_prompt_block(),
        guides_block=guides_block,
        toolbox_section=toolbox_section,
        **neighbor_kwargs,
    )
    before, marker, after = rendered.partition(_TRANSCRIPTION_SPLIT_MARKER)
    if not marker:
        raise ValueError(
            f"Prompt {user_prompt_id!r} must include the {{transcription}} placeholder."
        )
    if not before.endswith(_TRANSCRIPTION_OPEN_TAG):
        raise ValueError(
            f"Prompt {user_prompt_id!r} must end the static prefix with "
            f"{_TRANSCRIPTION_OPEN_TAG!r} before {{transcription}}."
        )
    static_text = before[: -len(_TRANSCRIPTION_OPEN_TAG)].rstrip()
    dynamic_text = (
        _TRANSCRIPTION_OPEN_TAG
        + transcription.strip()
        + after
    ).strip()
    return static_text, dynamic_text


def _mark_static_cache_boundary(
    content: list[dict],
    prompt_cache: PromptCacheMode,
) -> list[dict]:
    """Mark the end of the static prefix as a litellm cache boundary."""
    if prompt_cache == "off" or not content:
        return [*content]
    return [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]


def _stage2_prompt_cache_key(
    *,
    model: str,
    static_text: str,
    toolbox_pdf: Optional[Path],
    prompt_cache_key: Optional[str],
) -> str:
    """Build a stable cache key for providers that accept routing hints."""
    digest_input = "\n".join(
        [
            model,
            static_text,
            str(toolbox_pdf) if toolbox_pdf else "",
            str(toolbox_pdf.stat().st_mtime_ns) if toolbox_pdf and toolbox_pdf.exists() else "",
        ]
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    prefix = prompt_cache_key or "mudidi-stage2-pass2"
    return f"{prefix}-{digest}"


def _build_direct_mdf_prompt(
    *,
    transcription: str,
    image_path: str,
    field_map: FieldMapPrompt,
    model: str,
    guides: str = "",
    toolbox_pdf: Optional[Path] = None,
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
    prompt_cache: PromptCacheMode = "auto",
    media_reference: MediaReferenceMode = "auto",
) -> DirectMdfPrompt:
    """Build LLM messages and cache-key source text for Pass 2 extraction."""
    toolbox_section = ""
    toolbox_parts: list[dict] = []
    if toolbox_pdf and toolbox_pdf.is_file():
        toolbox_section, toolbox_parts = _toolbox_content_parts(
            toolbox_pdf,
            model=model,
            media_reference=media_reference,
        )
    static_text, dynamic_text = _render_direct_mdf_user_parts(
        transcription=transcription,
        field_map=field_map,
        toolbox_section=toolbox_section,
        guides=guides,
        mode=mode,
        page_context=page_context,
    )
    mime = mime_type_for_path(image_path)
    dynamic_content: list[dict] = [{"type": "text", "text": dynamic_text}]
    if mode == "inference" and page_context is not None:
        for neighbor in (page_context.previous, page_context.next):
            if neighbor is None:
                continue
            n_mime = mime_type_for_path(str(neighbor.image_path))
            dynamic_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url(str(neighbor.image_path), n_mime),
                    },
                }
            )
    dynamic_content.append(
        {
            "type": "image_url",
            "image_url": {"url": image_data_url(image_path, mime)},
        }
    )
    system_message = {
        "role": "system",
        "content": direct_mdf_system_prompt(mode=mode, page_context=page_context),
    }
    if prompt_cache == "off":
        merged_text = "\n".join(part for part in (static_text, dynamic_text) if part)
        user_content = [
            {"type": "text", "text": merged_text},
            *toolbox_parts,
            *dynamic_content[1:],
        ]
        messages = [system_message, {"role": "user", "content": user_content}]
    else:
        static_content = _mark_static_cache_boundary(
            [{"type": "text", "text": static_text}, *toolbox_parts],
            prompt_cache,
        )
        messages = [
            system_message,
            {"role": "user", "content": static_content},
            {"role": "user", "content": dynamic_content},
        ]
    return DirectMdfPrompt(messages=messages, static_text=static_text)


def build_direct_mdf_messages(
    *,
    transcription: str,
    image_path: str,
    field_map: FieldMapPrompt,
    model: str,
    guides: str = "",
    toolbox_pdf: Optional[Path] = None,
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
    prompt_cache: PromptCacheMode = "auto",
    media_reference: MediaReferenceMode = "auto",
) -> list[dict]:
    """Build LLM messages for Pass 2 direct MDF extraction."""
    return _build_direct_mdf_prompt(
        transcription=transcription,
        image_path=image_path,
        field_map=field_map,
        model=model,
        guides=guides,
        toolbox_pdf=toolbox_pdf,
        mode=mode,
        page_context=page_context,
        prompt_cache=prompt_cache,
        media_reference=media_reference,
    ).messages


def extract_direct_mdf(
    *,
    transcription: str,
    image_path: str,
    field_map: FieldMapPrompt,
    model: str,
    reasoning_effort: str,
    temperature: float = 0.1,
    guides: str = "",
    toolbox_pdf: Optional[Path] = None,
    mode: PromptMode = "benchmark",
    page_context: PageContext | None = None,
    prompt_cache: PromptCacheMode = "auto",
    media_reference: MediaReferenceMode = "auto",
    prompt_cache_key: Optional[str] = None,
) -> tuple[str, str, dict, list]:
    """
    Run Pass 2 direct MDF extraction.

    Returns:
        (mdf_text, raw_response, usage_dict, sanitized_messages)
    """
    prompt = _build_direct_mdf_prompt(
        transcription=transcription,
        image_path=image_path,
        field_map=field_map,
        model=model,
        guides=guides,
        toolbox_pdf=toolbox_pdf,
        mode=mode,
        page_context=page_context,
        prompt_cache=prompt_cache,
        media_reference=media_reference,
    )
    messages = prompt.messages
    effective_cache_key = None
    if prompt_cache != "off" and llm.supports_prompt_cache_key(model):
        effective_cache_key = _stage2_prompt_cache_key(
            model=model,
            static_text=prompt.static_text,
            toolbox_pdf=toolbox_pdf,
            prompt_cache_key=prompt_cache_key,
        )
    raw, usage = llm.complete_with_usage(
        model=model,
        messages=messages,
        temperature=temperature,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
        prompt_cache_key=effective_cache_key,
    )
    mdf_text = normalize_mdf_text(strip_markdown_fences(raw))
    return mdf_text, raw, usage, messages
