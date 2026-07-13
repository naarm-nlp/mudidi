"""
Load ``dictionary_languages.yaml`` and build configs from folder names + metadata CSV.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from mudidi.schemas.dictionary_languages import (
    DictionaryLanguagesConfig,
    SourceLanguageConfig,
    TargetLanguageConfig,
)

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "dictionary_languages.yaml"

# Longest token first so ``Kurdish_Turkish`` matches before ``Turkish``.
_TARGET_SUFFIXES: Tuple[Tuple[str, str, str], ...] = (
    ("Kurdish_Turkish", "ku_tr", "Kurdish/Turkish"),
    ("English", "en", "English"),
    ("Chinese", "zh", "Chinese"),
    ("Turkish", "tr", "Turkish"),
    ("Russian", "ru", "Russian"),
    ("French", "fr", "French"),
    ("Hindi", "hi", "Hindi"),
)

# Folder-specific layout (not inferable from metadata alone).
_LAYOUT_OVERRIDES: Dict[str, str] = {
    "Circassian-English-Turkish": "column_trilingual",
}

# Folder name → metadata source_language when CSV name differs.
_SOURCE_ALIASES: Dict[str, str] = {
    "canala": "canala/xârâcùù",
    "inupiatun eskimo": "iñupiatun eskimo",
    "vernacular syriac": "vernacular syriac",
}


def _normalize_key(text: str) -> str:
    """Case- and accent-insensitive key for matching."""
    text = text.strip().lower()
    text = text.replace("ñ", "n").replace("í", "i").replace("ú", "u")
    text = text.replace("â", "a").replace("è", "e").replace("ô", "o")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def language_key(name: str) -> str:
    """Stable internal key derived from a language name (not stored in YAML)."""
    key = _normalize_key(name)
    if key in ("english",):
        return "en"
    if key in ("chinese",):
        return "zh"
    if key in ("turkish",):
        return "tr"
    if key in ("russian",):
        return "ru"
    if key in ("french",):
        return "fr"
    if key in ("hindi",):
        return "hi"
    if key in ("kurdishturkish",):
        return "ku_tr"
    return key[:24] or "unknown"


def _is_english_language(language: str) -> bool:
    """Whether ``language`` denotes English for MDF ``ge`` tier."""
    return _normalize_key(language) == "english" or language_key(language) == "en"


def _column_id_for_language(folder_name: str, language: str) -> Optional[str]:
    """Return column_id for a language name in column-trilingual folders."""
    raw = _COLUMN_TRILINGUAL_BY_LANGUAGE.get(folder_name, {})
    by_norm = {_normalize_key(lang): cid for lang, cid in raw.items()}
    return by_norm.get(_normalize_key(language))


# Folder-specific column layout (language names, not codes).
_COLUMN_TRILINGUAL_BY_LANGUAGE: Dict[str, Dict[str, str]] = {
    "Circassian-English-Turkish": {
        "English": "left",
        "Circassian": "center",
        "Turkish": "right",
    },
}


def parse_folder_name(folder_name: str) -> Tuple[str, List[Tuple[str, str, str]]]:
    """
    Parse ``Source-Target`` or ``Source-Target1-Target2`` folder names.

    Returns:
        (source_display_name, [(folder_token, code, metadata_target_name), ...])
    """
    remaining = folder_name.strip()
    targets: List[Tuple[str, str, str]] = []
    while remaining:
        matched = False
        for token, code, meta_name in _TARGET_SUFFIXES:
            suffix = "-" + token
            if remaining.endswith(suffix):
                targets.insert(0, (token, code, meta_name))
                remaining = remaining[: -len(suffix)]
                matched = True
                break
        if not matched:
            break
    source = remaining.strip("-")
    source_display = source.replace("-", " ")
    if not source_display:
        raise ValueError(f"Could not parse source language from folder: {folder_name!r}")
    if not targets:
        raise ValueError(f"Could not parse target language(s) from folder: {folder_name!r}")
    return source_display, targets


def _parse_csv_targets(cell: str) -> List[str]:
    if not cell or not str(cell).strip():
        return []
    return [p.strip() for p in re.split(r"[,/]", str(cell)) if p.strip()]


def load_metadata_csv(csv_path: Path) -> List[Dict[str, str]]:
    """Load dictionary_metadata.csv rows as dicts."""
    rows: List[Dict[str, str]] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def _row_archive_id(row: Optional[Dict[str, str]]) -> str:
    """First CSV column is the archive identifier (headerless in export)."""
    if not row:
        return ""
    return next(iter(row.values()), "") or ""


def match_metadata_row(
    source_display: str,
    target_meta_names: Sequence[str],
    rows: Sequence[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """Find the best metadata row for a sample folder."""
    src_key = _normalize_key(source_display)
    alias = _SOURCE_ALIASES.get(src_key)
    if alias:
        src_key = _normalize_key(alias)
    tgt_keys = {_normalize_key(t) for t in target_meta_names}

    best: Optional[Dict[str, str]] = None
    best_score = -1
    for row in rows:
        row_src = _normalize_key(row.get("source_language", ""))
        if row_src != src_key and src_key not in row_src and row_src not in src_key:
            continue
        row_tgts = {_normalize_key(t) for t in _parse_csv_targets(row.get("target_language", ""))}
        if tgt_keys != row_tgts:
            continue
        score = 2
        if score > best_score:
            best_score = score
            best = row
    return best


def infer_layout(folder_name: str, num_targets: int) -> str:
    if folder_name in _LAYOUT_OVERRIDES:
        return _LAYOUT_OVERRIDES[folder_name]
    if num_targets >= 2:
        return "inline_trilingual"
    return "inline_bilingual"


# SIL Toolbox gloss markers (Appendix A): ge=English, gn=national, gr=regional, gv=vernacular.
_MDF_GLOSS_NON_ENGLISH = ("gn", "gr", "gv")


def mdf_marker_for_target(code: str, *, non_english_index: int = 0) -> str:
    """
    Map a target language code to the appropriate MDF gloss marker.

    English always uses ``ge``. Each additional gloss language uses ``gn``,
    then ``gr``, then ``gv`` (national → regional → vernacular in SIL naming).

    Args:
        code: Short target language code (``en``, ``ru``, ``zh``, …).
        non_english_index: 0-based index among non-English targets in folder order.

    Returns:
        Two-letter MDF gloss marker.
    """
    if code == "en":
        return "ge"
    if non_english_index < len(_MDF_GLOSS_NON_ENGLISH):
        return _MDF_GLOSS_NON_ENGLISH[non_english_index]
    return f"g{non_english_index + 2}"


def build_config_from_folder(
    folder_name: str,
    metadata_rows: Optional[Sequence[Dict[str, str]]] = None,
    metadata_csv_path: Optional[Path] = None,
) -> DictionaryLanguagesConfig:
    """
    Build a language config from a sample folder name and optional metadata CSV.
    """
    if metadata_rows is None:
        if metadata_csv_path is None:
            metadata_rows = []
        else:
            metadata_rows = load_metadata_csv(metadata_csv_path)

    source_display, target_tokens = parse_folder_name(folder_name)
    target_meta_names = [meta for _, _, meta in target_tokens]
    meta_row = match_metadata_row(source_display, target_meta_names, metadata_rows)

    layout = infer_layout(folder_name, len(target_tokens))

    targets: List[TargetLanguageConfig] = []
    for _token, _code, meta_name in target_tokens:
        targets.append(
            TargetLanguageConfig(
                language=meta_name,
                column_id=_column_id_for_language(folder_name, meta_name),
            )
        )

    source_col = _column_id_for_language(folder_name, source_display)
    if layout == "column_trilingual" and not source_col:
        used = {t.column_id for t in targets if t.column_id}
        for cid in ("left", "center", "right"):
            if cid not in used:
                source_col = cid
                break

    if layout == "column_trilingual":
        for t in targets:
            if t.column_id is None:
                logger.warning(
                    "column_trilingual %s: no column_id for target %s",
                    folder_name,
                    t.language,
                )

    return DictionaryLanguagesConfig(
        layout=layout,
        source=SourceLanguageConfig(
            language=source_display,
            column_id=source_col,
        ),
        targets=targets,
        writing_system=(meta_row or {}).get("Writing System", ""),
        metadata_archive=_row_archive_id(meta_row),
    )


def markers_for_config(config: DictionaryLanguagesConfig) -> dict[str, str]:
    """
    Fallback MDF gloss markers for the legacy structured schema export path.

    The direct MDF two-pass pipeline assigns markers in ``mdf_parsing_guide.json``
    instead; this helper is not used there.
    """
    markers: dict[str, str] = {}
    non_english_index = 0
    for target in config.targets:
        key = language_key(target.language)
        if _is_english_language(target.language):
            markers[key] = "ge"
        else:
            markers[key] = mdf_marker_for_target(
                key, non_english_index=non_english_index
            )
            non_english_index += 1
    return markers


def config_to_yaml_dict(config: DictionaryLanguagesConfig) -> Dict[str, Any]:
    """Serialize for YAML output (languages and layout only — no MDF markers)."""

    def _src(s: SourceLanguageConfig) -> Dict[str, Any]:
        d: Dict[str, Any] = {"language": s.language}
        if s.column_id:
            d["column_id"] = s.column_id
        return d

    def _tgt(t: TargetLanguageConfig) -> Dict[str, Any]:
        d: Dict[str, Any] = {"language": t.language}
        if t.column_id:
            d["column_id"] = t.column_id
        return d

    out: Dict[str, Any] = {
        "layout": config.layout,
        "source": _src(config.source),
        "targets": [_tgt(t) for t in config.targets],
    }
    if config.layout_description:
        out["layout-description"] = config.layout_description
    if config.writing_system:
        out["writing_system"] = config.writing_system
    if config.metadata_archive:
        out["metadata_archive"] = config.metadata_archive
    return out


def write_dictionary_languages_yaml(
    entry_dir: Path,
    config: DictionaryLanguagesConfig,
) -> Path:
    """Write ``dictionary_languages.yaml`` under a sample entry directory."""
    path = entry_dir / CONFIG_FILENAME
    path.write_text(
        yaml.safe_dump(
            config_to_yaml_dict(config),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def load_dictionary_languages_file(path: Path) -> DictionaryLanguagesConfig:
    """Load ``dictionary_languages.yaml`` from an explicit file path."""
    if not path.is_file():
        raise FileNotFoundError(f"dictionary_languages file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return DictionaryLanguagesConfig.model_validate(data)


def load_pass1_dictionary_languages(
    *,
    dictionary_languages_path: Path | None,
    entry_dir: Path | None,
    metadata_csv_path: Path | None,
    benchmark: bool,
) -> DictionaryLanguagesConfig | None:
    """
    Resolve language config for Stage 2 Pass 1 discovery.

    Inference requires an explicit ``--dictionary-languages`` path. Benchmark mode
    falls back to ``{entry_dir}/dictionary_languages.yaml`` when the flag is omitted.

    Args:
        dictionary_languages_path: CLI ``--dictionary-languages`` value, if any.
        entry_dir: Sample or dictionary entry directory for benchmark auto-load.
        metadata_csv_path: Metadata CSV used when auto-generating benchmark YAML.
        benchmark: Whether benchmark / samples-dir mode is active.

    Returns:
        Loaded config, or ``None`` when Pass 1 should omit the language hint.
    """
    if dictionary_languages_path is not None:
        return load_dictionary_languages_file(dictionary_languages_path)

    if benchmark and entry_dir is not None:
        return load_dictionary_languages(
            entry_dir,
            metadata_csv_path=metadata_csv_path,
        )

    return None


def load_dictionary_languages(
    entry_dir: Path,
    *,
    metadata_csv_path: Optional[Path] = None,
    regenerate_if_missing: bool = True,
) -> DictionaryLanguagesConfig:
    """
    Load ``dictionary_languages.yaml`` from an entry folder.

    If missing and ``regenerate_if_missing``, build from folder name + metadata CSV.
    """
    path = entry_dir / CONFIG_FILENAME
    if path.is_file():
        return load_dictionary_languages_file(path)

    if not regenerate_if_missing:
        raise FileNotFoundError(f"No {CONFIG_FILENAME} in {entry_dir}")

    config = build_config_from_folder(
        entry_dir.name,
        metadata_csv_path=metadata_csv_path,
    )
    write_dictionary_languages_yaml(entry_dir, config)
    logger.info("Generated %s for %s", CONFIG_FILENAME, entry_dir.name)
    return config
