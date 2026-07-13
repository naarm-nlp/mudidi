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

from mudidi.llm.client import complete_with_usage
from mudidi.llm.prompt_store import get_prompt_store
from mudidi.paths import MDF_PARSING_GUIDE_FILENAME
from mudidi.schemas.dictionary_languages import DictionaryLanguagesConfig
from mudidi.schemas.dictionary_profile import DictionaryProfile
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet
from mudidi.utils.image import image_data_url, mime_type_for_path
from mudidi.utils.parse_rules_pages import format_sample_pages_block

logger = logging.getLogger(__name__)


def find_parse_rules_path(directory: Path) -> Path:
    """Return the canonical MDF parsing-guide path."""

    return directory / MDF_PARSING_GUIDE_FILENAME


def pass_1_system_prompt() -> str:
    """Pass 1 field-discovery system prompt."""
    store = get_prompt_store()
    return store.format(
        "stage_2_pass_1",
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
) -> Tuple[DictionaryMarkerCheatsheet, Dict[str, Any]]:
    """Pass 1: discover markers + rules for this dictionary."""
    user_text = get_prompt_store().format(
        "stage_2_pass_2",
        transcription=transcription.strip(),
        config_hint=_config_hint(languages_config, dictionary_profile),
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    for intro_img in intro_images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data_url(str(intro_img), mime_type_for_path(str(intro_img)))
                },
            }
        )
    content.append(
        {
            "type": "image_url",
            "image_url": {
                "url": image_data_url(str(sample_image), mime_type_for_path(str(sample_image)))
            },
        }
    )
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
) -> Tuple[DictionaryMarkerCheatsheet, Dict[str, Any]]:
    """Pass 1: discover markers + rules from several sample pages in one call."""
    if len(samples) < 2:
        raise ValueError("discover_field_cheatsheet_multi requires at least two samples.")

    sample_pages_block = format_sample_pages_block(
        [(stem, transcription) for stem, transcription, _ in samples]
    )
    user_text = get_prompt_store().format(
        "stage_2_pass_2_multi",
        config_hint=_config_hint(languages_config, dictionary_profile),
        sample_pages_block=sample_pages_block,
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    for intro_img in intro_images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data_url(str(intro_img), mime_type_for_path(str(intro_img)))
                },
            }
        )
    for stem, _transcription, sample_image in samples:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data_url(
                        str(sample_image),
                        mime_type_for_path(str(sample_image)),
                    ),
                },
            }
        )
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


def gold_cheatsheet_path(entry_dir: Path) -> Path:
    """Deprecated alias for :func:`gold_parse_rules_path`."""
    return gold_parse_rules_path(entry_dir)


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


def load_gold_cheatsheet(entry_dir: Path) -> DictionaryMarkerCheatsheet:
    """Deprecated alias for :func:`load_gold_parse_rules`."""
    return load_gold_parse_rules(entry_dir)


def load_or_discover_parse_rules(
    cache_path: Path,
    *,
    force_refresh: bool = False,
    parse_rules_file: Path | None = None,
    multi_samples: Sequence[tuple[str, str, Path]] | None = None,
    **discover_kwargs,
) -> Tuple[DictionaryMarkerCheatsheet, Optional[Dict[str, Any]]]:
    """Load cached parse rules, a user file, or run Pass 1 discovery."""
    read_path = find_parse_rules_path(cache_path.parent)
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
        )
    else:
        sheet, usage = discover_field_cheatsheet(**discover_kwargs)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(sheet.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved parse rules → %s", cache_path)
    return sheet, usage


def load_or_discover_cheatsheet(
    cache_path: Path,
    *,
    force_refresh: bool = False,
    **discover_kwargs,
) -> DictionaryMarkerCheatsheet:
    """Deprecated alias for :func:`load_or_discover_parse_rules`."""
    sheet, _ = load_or_discover_parse_rules(
        cache_path, force_refresh=force_refresh, **discover_kwargs
    )
    return sheet
