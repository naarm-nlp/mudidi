"""
Pass 1: discover which MDF markers this dictionary uses.

Output is a compact marker list plus structure rules. Cached per Stage 2
experiment as ``outputs/stage-2/<experiment>/mdf_parsing_guide.json``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mudidi.config.prompt_cache import MediaReferenceMode
from mudidi.llm.client import complete_with_usage
from mudidi.llm.prompt_store import get_prompt_store
from mudidi.paths import MDF_PARSING_GUIDE_FILENAME
from mudidi.schemas.dictionary_languages import DictionaryLanguagesConfig
from mudidi.schemas.dictionary_profile import DictionaryProfile
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet
from mudidi.utils.image import file_content_part, image_data_url, mime_type_for_path
from mudidi.utils.parse_rules_pages import format_sample_pages_block
from mudidi.instructions import PreparedInstructionContext

logger = logging.getLogger(__name__)


def find_parse_rules_path(directory: Path) -> Path:
    """Return the canonical MDF parsing-guide path."""

    return directory / MDF_PARSING_GUIDE_FILENAME


def pass_1_system_prompt() -> str:
    """Pass 1 field-discovery system prompt."""
    store = get_prompt_store()
    return store.format(
        "stage_2_pass_1_system",
        mdf_marker_reference=store.get("mdf_marker_reference"),
    )


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in field discovery response.")
    return json.loads(text[start : end + 1])


def _config_hint(
    languages_config: Optional[DictionaryLanguagesConfig],
    dictionary_profile: Optional[DictionaryProfile] = None,
) -> str:
    if dictionary_profile is not None:
        return dictionary_profile.pass1_config_hint()
    if languages_config is None:
        return ""
    return languages_config.pass1_config_hint()


def _page_content_part(
    path: Path,
    *,
    media_reference: MediaReferenceMode = "auto",
) -> dict:
    """Build a valid multimodal content part for an image or PDF page."""
    mime_type = mime_type_for_path(str(path))
    if mime_type == "application/pdf":
        return file_content_part(
            str(path),
            mime_type=mime_type,
            media_reference=media_reference,
        )
    return {
        "type": "image_url",
        "image_url": {"url": image_data_url(str(path), mime_type)},
    }


def _instruction_prompt_values(
    instruction_context: PreparedInstructionContext | None,
    guides: str,
) -> tuple[str, str]:
    if instruction_context is None:
        return guides, ""
    return instruction_context.text, instruction_context.metadata.original_filename or ""




def discover_field_cheatsheet(
    *,
    transcription: str,
    sample_image: Path,
    intro_images: List[Path],
    model: str,
    reasoning_effort: str = "high",
    temperature: float = 0.1,
    languages_config: Optional[DictionaryLanguagesConfig] = None,
    dictionary_profile: Optional[DictionaryProfile] = None,
    media_reference: MediaReferenceMode = "auto",
    guides: str = "",
    instruction_context: PreparedInstructionContext | None = None,
) -> Tuple[DictionaryMarkerCheatsheet, Dict[str, Any]]:
    """Pass 1: discover markers + rules for this dictionary."""
    guide_text, guide_source = _instruction_prompt_values(instruction_context, guides)
    user_text = get_prompt_store().format(
        "stage_2_pass_1_user_single",
        transcription=transcription.strip(),
        config_hint=_config_hint(languages_config, dictionary_profile),
        guides=guide_text,
        guides_source=guide_source,
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    if instruction_context is not None:
        content.extend(
            instruction_context.content_parts(model, stage_label="Stage 2 Pass 1")
        )
    for intro_img in intro_images:
        content.append(_page_content_part(intro_img, media_reference=media_reference))
    content.append(_page_content_part(sample_image, media_reference=media_reference))
    messages = [
        {"role": "system", "content": pass_1_system_prompt()},
        {"role": "user", "content": content},
    ]
    logger.info("Pass 1 field discovery: model=%s sample=%s", model, sample_image.name)
    raw, usage = complete_with_usage(
        model=model,
        messages=messages,
        temperature=temperature,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )
    data = _extract_json_object(raw)
    sheet = DictionaryMarkerCheatsheet.model_validate(data)
    return sheet, usage

def discover_field_cheatsheet_multi(
    *,
    samples: Sequence[tuple[str, str, Path]],
    intro_images: List[Path],
    model: str,
    reasoning_effort: str = "high",
    temperature: float = 0.1,
    languages_config: Optional[DictionaryLanguagesConfig] = None,
    dictionary_profile: Optional[DictionaryProfile] = None,
    media_reference: MediaReferenceMode = "auto",
    guides: str = "",
    instruction_context: PreparedInstructionContext | None = None,
) -> Tuple[DictionaryMarkerCheatsheet, Dict[str, Any]]:
    """Pass 1: discover markers + rules from several sample pages in one call."""
    if len(samples) < 2:
        raise ValueError("discover_field_cheatsheet_multi requires at least two samples.")

    guide_text, guide_source = _instruction_prompt_values(instruction_context, guides)
    sample_pages_block = format_sample_pages_block(
        [(stem, transcription) for stem, transcription, _ in samples]
    )
    user_text = get_prompt_store().format(
        "stage_2_pass_1_user_multi",
        config_hint=_config_hint(languages_config, dictionary_profile),
        sample_pages_block=sample_pages_block,
        guides=guide_text,
        guides_source=guide_source,
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    if instruction_context is not None:
        content.extend(
            instruction_context.content_parts(model, stage_label="Stage 2 Pass 1")
        )
    for intro_img in intro_images:
        content.append(_page_content_part(intro_img, media_reference=media_reference))
    for stem, _transcription, sample_image in samples:
        content.append(_page_content_part(sample_image, media_reference=media_reference))
    messages = [
        {"role": "system", "content": pass_1_system_prompt()},
        {"role": "user", "content": content},
    ]
    sample_names = ", ".join(stem for stem, _, _ in samples)
    logger.info(
        "Pass 1 multi-sample field discovery: model=%s samples=[%s]",
        model,
        sample_names,
    )
    raw, usage = complete_with_usage(
        model=model,
        messages=messages,
        temperature=temperature,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )
    data = _extract_json_object(raw)
    sheet = DictionaryMarkerCheatsheet.model_validate(data)
    return sheet, usage


def _parse_rules_cache_metadata_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".meta.json")


def _empty_instruction_manifest() -> dict[str, object]:
    return {
        "source_path": None,
        "kind": "none",
        "original_filename": None,
        "byte_count": 0,
        "sha256": None,
        "pdf_page_count": None,
        "selected_pages": [],
        "selected_path": None,
        "selected_sha256": None,
    }


def _parse_rules_cache_identity(
    instruction_context: PreparedInstructionContext | None,
    instruction_scope: str,
) -> dict[str, object]:
    return {
        "scope": instruction_scope,
        "instruction": (
            instruction_context.manifest_entry(scope=instruction_scope)
            if instruction_context is not None
            else _empty_instruction_manifest()
        ),
    }


def _write_parse_rules_cache_metadata(
    cache_path: Path,
    *,
    instruction_context: PreparedInstructionContext | None,
    instruction_scope: str,
) -> None:
    metadata_path = _parse_rules_cache_metadata_path(cache_path)
    metadata_path.write_text(
        json.dumps(
            _parse_rules_cache_identity(instruction_context, instruction_scope),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _ensure_parse_rules_cache_compatible(
    cache_path: Path,
    *,
    instruction_context: PreparedInstructionContext | None,
    instruction_scope: str,
    force_refresh: bool,
) -> None:
    metadata_path = _parse_rules_cache_metadata_path(cache_path)
    if force_refresh or not cache_path.is_file():
        return
    expected = _parse_rules_cache_identity(instruction_context, instruction_scope)
    if not metadata_path.is_file():
        if expected["instruction"]["kind"] == "none" and instruction_scope == "both":
            return
        raise ValueError(
            f"Instruction metadata for cached parse rules is unavailable at {cache_path}; "
            "pass --overwrite before reuse."
        )
    try:
        actual = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cached parse-rule metadata is unreadable at {metadata_path}; "
            "pass --overwrite before reuse."
        ) from exc
    if actual != expected:
        raise ValueError(
            "Instruction attachment metadata changed for cached parse rules; "
            "pass --overwrite before reuse."
        )


def load_parse_rules_file(path: Path) -> DictionaryMarkerCheatsheet:
    """Load user-supplied parse rules and validate schema."""
    if not path.is_file():
        raise FileNotFoundError(f"Parse rules file not found: {path}")
    logger.info("Loading parse rules file: %s", path)
    return DictionaryMarkerCheatsheet.model_validate_json(path.read_text(encoding="utf-8"))


def gold_parse_rules_path(entry_dir: Path) -> Path:
    """Resolve human-authored parse rules under ``outputs/stage-2-gold/``."""
    gold_dir = entry_dir / "outputs" / "stage-2-gold"
    return find_parse_rules_path(gold_dir)


def load_gold_parse_rules(entry_dir: Path) -> DictionaryMarkerCheatsheet:
    """Load gold Pass-1 parse rules for a dictionary entry."""
    path = gold_parse_rules_path(entry_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"Gold parse rules not found under {entry_dir / 'outputs' / 'stage-2-gold'} "
            f"(expected {MDF_PARSING_GUIDE_FILENAME})"
        )
    logger.info("Loading gold parse rules: %s", path)
    return DictionaryMarkerCheatsheet.model_validate_json(path.read_text(encoding="utf-8"))


def load_or_discover_parse_rules(
    cache_path: Path,
    *,
    force_refresh: bool = False,
    parse_rules_file: Path | None = None,
    multi_samples: Sequence[tuple[str, str, Path]] | None = None,
    instruction_context: PreparedInstructionContext | None = None,
    instruction_scope: str = "both",
    **discover_kwargs,
) -> Tuple[DictionaryMarkerCheatsheet, Optional[Dict[str, Any]]]:
    """Load cached parse rules, a user file, or run Pass 1 discovery."""
    read_path = find_parse_rules_path(cache_path.parent)
    _ensure_parse_rules_cache_compatible(
        read_path,
        instruction_context=instruction_context,
        instruction_scope=instruction_scope,
        force_refresh=force_refresh,
    )
    if read_path.is_file() and not force_refresh and parse_rules_file is None:
        logger.info("Loading cached parse rules: %s", read_path)
        return (
            DictionaryMarkerCheatsheet.model_validate_json(
                read_path.read_text(encoding="utf-8")
            ),
            None,
        )

    usage: Optional[Dict[str, Any]] = None
    if parse_rules_file is not None:
        sheet = load_parse_rules_file(parse_rules_file)
    elif multi_samples is not None and len(multi_samples) > 1:
        sheet, usage = discover_field_cheatsheet_multi(
            samples=multi_samples,
            intro_images=discover_kwargs.get("intro_images", []),
            model=discover_kwargs["model"],
            reasoning_effort=discover_kwargs.get("reasoning_effort", "high"),
            temperature=discover_kwargs.get("temperature", 0.1),
            languages_config=discover_kwargs.get("languages_config"),
            dictionary_profile=discover_kwargs.get("dictionary_profile"),
            media_reference=discover_kwargs.get("media_reference", "auto"),
            guides=discover_kwargs.get("guides", ""),
            instruction_context=instruction_context,
        )
    else:
        sheet, usage = discover_field_cheatsheet(
            instruction_context=instruction_context,
            **discover_kwargs,
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(sheet.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_parse_rules_cache_metadata(
        cache_path,
        instruction_context=instruction_context,
        instruction_scope=instruction_scope,
    )
    logger.info("Saved parse rules → %s", cache_path)
    return sheet, usage
