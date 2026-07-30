"""LLM labeler: tag-injection gold language-and-script span maps.

Sends raw gold to ``gemini/gemini-3-flash-preview`` and asks the model to wrap each
run in short tag codes mapped to **Language-Script** compound labels, e.g.
``Japanese-Hiragana``, ``Japanese-Kanji``, ``English-Latin``, ``Canala-IPA``.
Recovery (:mod:`annotation.tier2_recovery`) turns the tagged text into validated
``*_lang.json`` maps.

Deterministic script-only drafts: :mod:`script_labeler` (``LABELER_MODE=script`` in
``run_labeler.sh``).

Language seeding: ``dictionary_languages.yaml`` plus optional ``language_rules.yaml``
and CLI ``--rules`` files.
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

import yaml

StageMode = Literal["all", "legend", "tagging"]
LegendScope = Literal["dictionary", "page"]
DICTIONARY_LEGEND_NAME = "language_legend.yaml"
_RULES_SUFFIXES = {".txt", ".md", ".markdown", ".yaml", ".yml"}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labeler_common import (  # noqa: E402  (flat sibling import; reuse discovery)
    OUTPUT_ROOT,
    _dictionary_name,
    _lang_map_path,
    _page_number,
    discover_gold_pages,
    list_dictionaries,
    read_languages,
)
from tier2_recovery import detect_markup_tags, recover_page_map  # noqa: E402

from mudidi.llm.client import complete_with_usage  # noqa: E402
from mudidi.schemas.language_span import META, PageLanguageMap  # noqa: E402

DEFAULT_MODEL = "gemini/gemini-3-flash-preview"
DEFAULT_MAX_TOKENS = 32000
DEFAULT_REASONING_EFFORT = "low"


@dataclass
class PageResult:
    """Outcome of labeling one gold page.

    ``status`` is ``"ok"`` (``page_map`` populated, ready to write), ``"skipped"``
    (an output ``*_lang.json`` already existed and ``--skip-existing`` was set), or
    ``"failed"`` (the LLM output drifted past the gate or the map failed its
    invariants -- ``error`` carries the reason; the page is left for manual
    labeling). One failed page never aborts the batch.
    """

    gold_path: Path
    page: int
    out_path: Path
    status: str
    page_map: Optional[PageLanguageMap] = None
    used: List[str] = field(default_factory=list)
    drift: float = 0.0
    error: Optional[str] = None
    legend_path: Optional[Path] = None


@dataclass(frozen=True)
class LanguageRules:
    """Per-dictionary labeling hints loaded from ``language_rules.yaml`` and CLI paths."""

    extra_languages: Tuple[str, ...] = ()
    rules_text: str = ""
    code_overrides: Dict[str, str] = field(default_factory=dict)


def _normalize_code_overrides(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for code, name in raw.items():
        if isinstance(code, str) and isinstance(name, str) and code.strip() and name.strip():
            out[code.strip().lower()] = name.strip()
    return out


def _load_rules_file(path: Path) -> str:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8").strip()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            parts: List[str] = []
            rules = data.get("rules")
            if isinstance(rules, str) and rules.strip():
                parts.append(rules.strip())
            langs = data.get("languages")
            if isinstance(langs, list):
                names = [str(item).strip() for item in langs if str(item).strip()]
                if names:
                    parts.append(
                        "Additional languages that may appear: " + ", ".join(names) + "."
                    )
            overrides = _normalize_code_overrides(data.get("code_overrides"))
            if overrides:
                lines = [f"  {code} -> {name}" for code, name in sorted(overrides.items())]
                parts.append("Code remapping hints:\n" + "\n".join(lines))
            return "\n\n".join(parts).strip()
    return text


def read_dictionary_rules(
    dictionary_dir: str | Path,
    extra_rule_paths: Optional[Iterable[str | Path]] = None,
) -> LanguageRules:
    """Load ``language_rules.yaml`` from the dictionary folder plus optional CLI rule files."""
    dictionary_dir = Path(dictionary_dir)
    extra_languages: List[str] = []
    rule_chunks: List[str] = []
    code_overrides: Dict[str, str] = {}

    rules_path = dictionary_dir / "language_rules.yaml"
    if rules_path.is_file():
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            for name in data.get("languages") or []:
                if isinstance(name, str) and name.strip():
                    extra_languages.append(name.strip())
            rules = data.get("rules")
            if isinstance(rules, str) and rules.strip():
                rule_chunks.append(rules.strip())
            code_overrides.update(_normalize_code_overrides(data.get("code_overrides")))

    for raw_path in extra_rule_paths or ():
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"rules file not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in _RULES_SUFFIXES:
            raise ValueError(
                f"unsupported rules file type {path.suffix!r} ({path}); "
                f"expected one of {sorted(_RULES_SUFFIXES)}"
            )
        rule_chunks.append(_load_rules_file(path))

    return LanguageRules(
        extra_languages=tuple(extra_languages),
        rules_text="\n\n".join(chunk for chunk in rule_chunks if chunk).strip(),
        code_overrides=code_overrides,
    )


def read_language_seed(
    dictionary_dir: str | Path,
    extra_rule_paths: Optional[Iterable[str | Path]] = None,
) -> List[str]:
    """Return the seed language list: yaml languages + optional ``language_rules.yaml``."""
    dictionary_dir = Path(dictionary_dir)
    source, targets = read_languages(dictionary_dir)
    rules = read_dictionary_rules(dictionary_dir, extra_rule_paths)
    seed: List[str] = [source, *targets, *rules.extra_languages]
    return list(dict.fromkeys(seed))


def _rules_section(rules_text: str) -> str:
    if not rules_text.strip():
        return ""
    return (
        "\nAdditional labeling rules (follow strictly):\n"
        f"{rules_text.strip()}\n"
    )


def _markup_preserve_note(markup_tags: frozenset) -> str:
    extra = sorted(markup_tags - {"b", "i"})
    if not extra:
        return ""
    tags_str = ", ".join(f"<{t}>...</{t}>" for t in extra)
    return (
        f" Also keep {tags_str} exactly as they appear — "
        "they are content characters, not markup to remove."
    )


def _language_script_labeling_goal() -> str:
    """Shared instructions for Language-Script compound labels."""
    return (
        "You are labeling the language AND writing system of every part of one "
        "OCR'd dictionary page.\n\n"
        "Each label is a compound name: Language-Script (exactly one hyphen between "
        "the language name and the script name).\n"
        "Examples: English-Latin, French-Latin, Canala-IPA, Canala-Latin, "
        "Japanese-Hiragana, Japanese-Katakana, Japanese-Kanji, Japanese-Latin, "
        "Russian-Cyrillic, Chukchi-Cyrillic Extended, Chinese-Han, Greek-Greek, "
        "Arabic-Arabic, Bengali-Bengali.\n\n"
        "Split by BOTH language and script. When one language appears in multiple "
        "scripts on the page, use separate labels per script (e.g. Japanese-Hiragana "
        "vs Japanese-Kanji vs Japanese-Latin for romaji/English glosses in Latin).\n"
        "When a language uses only one script on the page, still include the script "
        "suffix (English-Latin, Telugu-Telugu).\n"
        "Romanizations and glosses inherit the language they belong to, with the "
        "script they are written in (English-Latin, not bare Latin).\n"
    )


def _legend_format_instructions() -> str:
    return (
        "Identify every distinct Language-Script pair on the page. Assign each a "
        "short lowercase tag code (2–12 characters; letters, digits, hyphens only) "
        "and write them under a line that reads exactly\n"
        "LANGUAGES:\n"
        "with one entry per line in the form 'code = Language-Script'.\n"
        "Include pairs even if they are not in the hint list below.\n"
    )


def _tagging_format_instructions(*, meta: str = META) -> str:
    return (
        "Under a line that reads exactly\n"
        "TAGGED:\n"
        "re-emit the ENTIRE page text, wrapping each contiguous run in a tag named "
        "by one of those codes. Use "
        f"<{meta}>...</{meta}> for editorial markers that are not language text "
        "(entry numbers, running heads, reference markers like [NK]).\n"
    )


def _legend_lines(code_to_language: Dict[str, str]) -> str:
    return "\n".join(
        f"{code} = {name}"
        for code, name in sorted(code_to_language.items(), key=lambda item: item[0])
    )


def build_legend_prompt(
    raw_gold: str,
    seed_languages: List[str],
    *,
    rules_text: str = "",
    markup_tags: frozenset = frozenset({"b", "i"}),
) -> str:
    """Prompt for the legend-only stage (STEP 1)."""
    languages = ", ".join(seed_languages)
    return (
        f"{_language_script_labeling_goal()}"
        f"{_legend_format_instructions()}\n"
        f"Hint — languages known to appear in this dictionary: {languages}.\n"
        f"{_rules_section(rules_text)}"
        "ABSOLUTE RULES:\n"
        "1. Do NOT modify the page text — output ONLY the LANGUAGES: block.\n"
        "2. Right-hand names must be Language-Script compounds; left-hand codes must "
        "be short lowercase tag codes (not the full compound name).\n"
        "3. Do NOT output a TAGGED: block in this stage.\n"
        "4. Output no commentary and no code fences.\n\n"
        "The page text below is for context only — do not re-emit it:\n"
        f"{raw_gold}"
    )


def build_tagging_prompt(
    raw_gold: str,
    code_to_language: Dict[str, str],
    *,
    rules_text: str = "",
    markup_tags: frozenset = frozenset({"b", "i"}),
) -> str:
    """Prompt for the tagging-only stage (STEP 2) with a fixed legend."""
    preserve_note = _markup_preserve_note(markup_tags)
    legend = _legend_lines(code_to_language)
    allowed = ", ".join(f"<{code}>" for code in sorted(code_to_language))
    return (
        f"{_language_script_labeling_goal()}"
        "The Language-Script pairs for this page are fixed — use ONLY these tag codes:\n"
        f"{legend}\n\n"
        f"{_tagging_format_instructions()}"
        f"Example wrappers: {allowed}.\n"
        f"{_rules_section(rules_text)}"
        "ABSOLUTE RULES:\n"
        "1. Do NOT add, delete, reorder, correct, or change ANY character of the "
        "original text. Only insert wrapper tags.\n"
        "2. Use ONLY the short codes listed above as tag names; never use a "
        "Language-Script compound name as a tag.\n"
        "3. Keep existing <b>...</b> and <i>...</i> typography markup exactly as it "
        f"appears, as part of the wrapped text (do not treat it as a language).{preserve_note}\n"
        "4. Preserve all whitespace and line breaks exactly.\n"
        "5. Output ONLY the TAGGED: block — no LANGUAGES: block, no commentary, and "
        "no code fences.\n\n"
        "PAGE TEXT:\n"
        f"{raw_gold}"
    )


def build_prompt(
    raw_gold: str,
    seed_languages: List[str],
    markup_tags: frozenset = frozenset({"b", "i"}),
    *,
    rules_text: str = "",
) -> str:
    """Assemble the Language-Script tag-injection instruction for one raw gold page."""
    languages = ", ".join(seed_languages)
    preserve_note = _markup_preserve_note(markup_tags)
    return (
        f"{_language_script_labeling_goal()}"
        "STEP 1 — "
        f"{_legend_format_instructions()}\n"
        "STEP 2 — "
        f"{_tagging_format_instructions()}\n"
        f"Hint — languages known to appear in this dictionary: {languages}.\n"
        f"{_rules_section(rules_text)}"
        "ABSOLUTE RULES:\n"
        "1. Do NOT add, delete, reorder, correct, or change ANY character of the "
        "original text. Only insert wrapper tags.\n"
        "2. Use ONLY the short tag codes from STEP 1 as tag names (e.g. <jph>, "
        "<eng-lat>); never use a Language-Script compound name as a tag.\n"
        "3. Keep existing <b>...</b> and <i>...</i> typography markup exactly as it "
        f"appears, as part of the wrapped text (do not treat it as a language).{preserve_note}\n"
        "4. Preserve all whitespace and line breaks exactly.\n"
        "5. Output ONLY the LANGUAGES: block then the TAGGED: block — no commentary "
        "and no code fences.\n\n"
        "PAGE TEXT:\n"
        f"{raw_gold}"
    )


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```...``` markdown code fence if the model added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_TAGGED_MARKER = re.compile(r"(?mi)^[ \t]*TAGGED:[ \t]*\r?\n?")
_LANGUAGES_MARKER = re.compile(r"(?mi)^[ \t]*LANGUAGES:[ \t]*\r?\n?")
_LEGEND_LINE = re.compile(r"^\s*([a-z][\w-]{1,15})\s*=\s*(.+?)\s*$", re.IGNORECASE)


def parse_llm_output(text: str) -> Tuple[Dict[str, str], str]:
    """Split the model output into ``(code -> language legend, tagged text)``.

    The expected shape is a ``LANGUAGES:`` block of ``code = Name`` lines followed by a
    ``TAGGED:`` block holding the code-tagged page text. If the ``TAGGED:`` marker is
    absent (looser output) the whole thing is treated as the tagged text with an empty
    legend -- recovery then uses the tag names as languages directly.
    """
    stripped = _strip_code_fence(text)
    marker = _TAGGED_MARKER.search(stripped)
    if not marker:
        return parse_legend_output(stripped)[0], stripped
    legend_block = _LANGUAGES_MARKER.sub("", stripped[: marker.start()])
    tagged = stripped[marker.end() :]
    return _parse_legend_block(legend_block), tagged


def parse_legend_output(text: str) -> Tuple[Dict[str, str], str]:
    """Parse a legend-only LLM response into ``(code -> language, raw legend block)``."""
    stripped = _strip_code_fence(text)
    marker = _TAGGED_MARKER.search(stripped)
    legend_block = stripped[: marker.start()] if marker else stripped
    legend_block = _LANGUAGES_MARKER.sub("", legend_block)
    return _parse_legend_block(legend_block), legend_block.strip()


def _parse_legend_block(legend_block: str) -> Dict[str, str]:
    code_to_language: Dict[str, str] = {}
    for line in legend_block.splitlines():
        match = _LEGEND_LINE.match(line)
        if match:
            code_to_language[match.group(1).lower()] = match.group(2).strip()
    return code_to_language


def apply_code_overrides(
    code_to_language: Dict[str, str],
    overrides: Dict[str, str],
) -> Dict[str, str]:
    """Return a copy of ``code_to_language`` with ``overrides`` merged in."""
    if not overrides:
        return dict(code_to_language)
    merged = dict(code_to_language)
    for code, name in overrides.items():
        merged[code.lower()] = name
    return merged


def _legend_path(gold_path: Path, output_root: str | Path = OUTPUT_ROOT) -> Path:
    stem = gold_path.name.split("_stage1_GOLD_flat.txt")[0]
    return Path(output_root) / _dictionary_name(gold_path) / f"{stem}_legend.yaml"


def _legend_raw_path(gold_path: Path, output_root: str | Path = OUTPUT_ROOT) -> Path:
    stem = gold_path.name.split("_stage1_GOLD_flat.txt")[0]
    return Path(output_root) / _dictionary_name(gold_path) / f"{stem}_legend.raw.txt"


def _tagged_raw_path(gold_path: Path, output_root: str | Path = OUTPUT_ROOT) -> Path:
    stem = gold_path.name.split("_stage1_GOLD_flat.txt")[0]
    return Path(output_root) / _dictionary_name(gold_path) / f"{stem}_tagged.raw.txt"


def save_legend(path: Path, code_to_language: Dict[str, str], *, raw_text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(sorted(code_to_language.items(), key=lambda item: item[0]))
    path.write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    if raw_text:
        raw_path = path.with_name(f"{path.stem}.raw.txt")
        raw_path.write_text(raw_text, encoding="utf-8")


def dictionary_legend_path(dictionary_dir: str | Path) -> Path:
    """Path to the shared legend beside ``dictionary_languages.yaml``."""
    return Path(dictionary_dir) / DICTIONARY_LEGEND_NAME


def resolve_legend_path(
    dictionary_dir: str | Path,
    gold_path: Path,
    output_root: str | Path,
    *,
    legend_override: Optional[str | Path] = None,
) -> Optional[Path]:
    """Return the legend file to use for tagging, in priority order.

    1. ``legend_override`` (``--legend``)
    2. ``<dictionary>/language_legend.yaml``
    3. ``<output>/<dict>/page_<N>_legend.yaml`` (legacy per-page fallback)
    """
    if legend_override is not None:
        path = Path(legend_override)
        return path if path.is_file() else None
    dict_path = dictionary_legend_path(dictionary_dir)
    if dict_path.is_file():
        return dict_path
    page_path = _legend_path(gold_path, output_root)
    if page_path.is_file():
        return page_path
    return dict_path if dict_path.is_file() else None


def resolve_legend_for_tagging(
    dictionary_dir: str | Path,
    gold_path: Path,
    output_root: str | Path,
    *,
    legend_override: Optional[str | Path] = None,
) -> Tuple[Path, Dict[str, str]]:
    """Load the legend mapping for a tagging pass. Raises if no legend file exists."""
    path = resolve_legend_path(
        dictionary_dir,
        gold_path,
        output_root,
        legend_override=legend_override,
    )
    if path is None or not path.is_file():
        expected = legend_override or dictionary_legend_path(dictionary_dir)
        raise FileNotFoundError(f"missing legend file: {expected}")
    return path, load_legend(path)


def load_legend(path: Path) -> Dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"legend file must be a mapping: {path}")
    return _normalize_code_overrides(data)


def _call_llm(
    prompt: str,
    *,
    model: str,
    reasoning_effort: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, Dict[str, Any]]:
    return complete_with_usage(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def run_legend_stage(
    raw_gold: str,
    *,
    seed_languages: List[str],
    rules_text: str = "",
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.1,
) -> Tuple[Dict[str, str], str, Dict[str, Any]]:
    """Infer a language-code legend for one page. Returns ``(legend, raw_text, usage)``."""
    markup_tags = detect_markup_tags(raw_gold)
    prompt = build_legend_prompt(
        raw_gold,
        seed_languages,
        rules_text=rules_text,
        markup_tags=markup_tags,
    )
    text, usage = _call_llm(
        prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    legend, _block = parse_legend_output(text)
    if not legend:
        raise ValueError("legend stage returned no 'code = Language-Script' entries")
    return legend, text, usage


def run_tagging_stage(
    raw_gold: str,
    code_to_language: Dict[str, str],
    *,
    dictionary: str,
    page: int,
    rules_text: str = "",
    code_overrides: Optional[Dict[str, str]] = None,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.1,
    max_drift: float = 0.02,
) -> Tuple[PageLanguageMap, List[str], float, str, Dict[str, Any]]:
    """Tag one page with a fixed legend. Returns ``(page_map, used, drift, raw_text, usage)``."""
    markup_tags = detect_markup_tags(raw_gold)
    merged_legend = apply_code_overrides(code_to_language, code_overrides or {})
    prompt = build_tagging_prompt(
        raw_gold,
        merged_legend,
        rules_text=rules_text,
        markup_tags=markup_tags,
    )
    text, usage = _call_llm(
        prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    stripped = _strip_code_fence(text)
    marker = _TAGGED_MARKER.search(stripped)
    tagged = stripped[marker.end() :] if marker else stripped
    page_map, used, drift = recover_page_map(
        raw_gold,
        tagged,
        dictionary=dictionary,
        page=page,
        markup_tags=markup_tags,
        code_to_language=merged_legend,
        max_drift=max_drift,
    )
    return page_map, used, drift, text, usage


def label_page(
    raw_gold: str,
    *,
    dictionary: str,
    page: int,
    seed_languages: List[str],
    rules_text: str = "",
    code_overrides: Optional[Dict[str, str]] = None,
    stage: StageMode = "all",
    legend: Optional[Dict[str, str]] = None,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.1,
    max_drift: float = 0.02,
) -> Tuple[PageLanguageMap, List[str], float, Dict[str, Any]]:
    """Label one raw gold page via the LLM and deterministic recovery.

    ``stage`` selects ``all`` (default), ``legend`` (not supported here — use
    :func:`run_legend_stage`), or ``tagging`` with a pre-built ``legend``.

    Returns ``(page_map, used_languages, drift, usage)``.

    Raises:
        Tier2DriftError: if the de-tagged LLM output drifts from the gold too far.
        SpanMapError: if the recovered map fails its invariants.
        ValueError: if ``stage='tagging'`` but ``legend`` is missing/empty.
    """
    if stage == "tagging":
        if not legend:
            raise ValueError("tagging stage requires a non-empty legend mapping")
        page_map, used, drift, _raw, usage = run_tagging_stage(
            raw_gold,
            legend,
            dictionary=dictionary,
            page=page,
            rules_text=rules_text,
            code_overrides=code_overrides,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            temperature=temperature,
            max_drift=max_drift,
        )
        return page_map, used, drift, usage

    markup_tags = detect_markup_tags(raw_gold)
    prompt = build_prompt(
        raw_gold,
        seed_languages,
        markup_tags=markup_tags,
        rules_text=rules_text,
    )
    text, usage = _call_llm(
        prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    code_to_language, tagged = parse_llm_output(text)
    code_to_language = apply_code_overrides(code_to_language, code_overrides or {})
    page_map, used, drift = recover_page_map(
        raw_gold,
        tagged,
        dictionary=dictionary,
        page=page,
        markup_tags=markup_tags,
        code_to_language=code_to_language,
        max_drift=max_drift,
    )
    return page_map, used, drift, usage


def label_dictionary(
    dictionary_dir: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.1,
    max_drift: float = 0.15,
    limit: Optional[int] = None,
    pages_only: Optional[Iterable[int]] = None,
    skip_existing: bool = True,
    output_root: str | Path = OUTPUT_ROOT,
    batch_size: int = 1,
    stage: StageMode = "all",
    extra_rule_paths: Optional[List[str]] = None,
    legend_path_override: Optional[str | Path] = None,
    legend_scope: LegendScope = "dictionary",
) -> List[PageResult]:
    """Label gold pages of a Tier-2 dictionary, one :class:`PageResult` per page.

    A page whose LLM output drifts past the gate (:class:`Tier2DriftError`), fails
    its map invariants (``SpanMapError``), or errors mid-call is recorded as a
    ``"failed"`` result and the batch continues -- a single bad page never aborts
    the run. By default (``skip_existing=True``) a page whose ``*_lang.json`` already
    exists is returned as ``"skipped"`` without an LLM call; pass ``skip_existing=
    False`` (CLI ``--overwrite``) to re-label it.

    ``batch_size`` controls how many pages are labeled concurrently via a thread pool
    (each thread makes an independent ``litellm.completion`` call). The global rate-limit
    pause in ``llm/client.py`` is shared across threads, matching the main pipeline
    behaviour.
    """
    dictionary_dir = Path(dictionary_dir)
    dictionary = dictionary_dir.name
    rules = read_dictionary_rules(dictionary_dir, extra_rule_paths)
    seed = read_language_seed(dictionary_dir, extra_rule_paths)
    pages = discover_gold_pages(dictionary_dir)
    if pages_only is not None:
        allowed = set(pages_only)
        pages = [path for path in pages if _page_number(path) in allowed]
    if limit is not None:
        pages = pages[:limit]

    if stage == "legend" and legend_scope == "dictionary":
        if not pages:
            return []
        dict_legend_path = (
            Path(legend_path_override)
            if legend_path_override is not None
            else dictionary_legend_path(dictionary_dir)
        )
        first = pages[0]
        page = _page_number(first)
        out_path = _lang_map_path(first, output_root)
        if skip_existing and dict_legend_path.is_file():
            return [
                PageResult(
                    first,
                    page,
                    out_path,
                    status="skipped",
                    legend_path=dict_legend_path,
                )
            ]
        raw_gold = first.read_text(encoding="utf-8")
        try:
            legend, raw_text, _usage = run_legend_stage(
                raw_gold,
                seed_languages=seed,
                rules_text=rules.rules_text,
                model=model,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            save_legend(dict_legend_path, legend, raw_text=raw_text)
        except Exception as exc:  # noqa: BLE001
            return [
                PageResult(
                    first,
                    page,
                    out_path,
                    status="failed",
                    legend_path=dict_legend_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            ]
        return [
            PageResult(
                first,
                page,
                out_path,
                status="legend_ok",
                used=sorted(set(legend.values())),
                legend_path=dict_legend_path,
            )
        ]

    def _process(gold_path: Path) -> PageResult:
        page = _page_number(gold_path)
        out_path = _lang_map_path(gold_path, output_root)
        page_legend_path = _legend_path(gold_path, output_root)
        raw_gold = gold_path.read_text(encoding="utf-8")

        if stage == "legend":
            if skip_existing and page_legend_path.is_file():
                return PageResult(
                    gold_path,
                    page,
                    out_path,
                    status="skipped",
                    legend_path=page_legend_path,
                )
            try:
                legend, raw_text, _usage = run_legend_stage(
                    raw_gold,
                    seed_languages=seed,
                    rules_text=rules.rules_text,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                save_legend(page_legend_path, legend, raw_text=raw_text)
            except Exception as exc:  # noqa: BLE001
                return PageResult(
                    gold_path,
                    page,
                    out_path,
                    status="failed",
                    legend_path=page_legend_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            return PageResult(
                gold_path,
                page,
                out_path,
                status="legend_ok",
                used=sorted(set(legend.values())),
                legend_path=page_legend_path,
            )

        if stage == "tagging":
            if skip_existing and out_path.is_file():
                return PageResult(gold_path, page, out_path, status="skipped")
            try:
                legend_path, legend = resolve_legend_for_tagging(
                    dictionary_dir,
                    gold_path,
                    output_root,
                    legend_override=legend_path_override,
                )
                page_map, used, drift, raw_text, _usage = run_tagging_stage(
                    raw_gold,
                    legend,
                    dictionary=dictionary,
                    page=page,
                    rules_text=rules.rules_text,
                    code_overrides=rules.code_overrides,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_drift=max_drift,
                )
                tagged_raw_path = _tagged_raw_path(gold_path, output_root)
                tagged_raw_path.parent.mkdir(parents=True, exist_ok=True)
                tagged_raw_path.write_text(raw_text, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                return PageResult(
                    gold_path,
                    page,
                    out_path,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            return PageResult(
                gold_path,
                page,
                out_path,
                status="ok",
                page_map=page_map,
                used=used,
                drift=drift,
                legend_path=legend_path,
            )

        if skip_existing and out_path.is_file():
            return PageResult(gold_path, page, out_path, status="skipped")
        try:
            page_map, used, drift, _usage = label_page(
                raw_gold,
                dictionary=dictionary,
                page=page,
                seed_languages=seed,
                rules_text=rules.rules_text,
                code_overrides=rules.code_overrides,
                stage="all",
                model=model,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
                temperature=temperature,
                max_drift=max_drift,
            )
        except Exception as exc:  # noqa: BLE001
            return PageResult(
                gold_path,
                page,
                out_path,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        return PageResult(
            gold_path,
            page,
            out_path,
            status="ok",
            page_map=page_map,
            used=used,
            drift=drift,
        )

    if batch_size > 1:
        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            return list(pool.map(_process, pages))
    return [_process(p) for p in pages]


def tier2_dictionaries(dictionaries_root: str | Path) -> List[str]:
    """Return dictionary folder names with gold pages (legacy alias)."""
    return list_dictionaries(dictionaries_root)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="LLM labeler: write Language-Script span maps (tag-injection + recovery).",
    )
    parser.add_argument(
        "--dictionaries-root",
        default="dataset/MUDIDI/dictionaries",
        help="Root holding per-dictionary folders.",
    )
    parser.add_argument(
        "--dictionary",
        action="append",
        dest="dictionaries",
        default=None,
        help="Dictionary folder name to label (repeatable). Default: all Tier-2 dicts.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="litellm model string.")
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        choices=("none", "low", "medium", "high"),
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature (default: 0.1). Ignored for Gemini 3+ "
        "(locked to 1.0 by litellm) and GPT-5 family (locked to 1.0).",
    )
    parser.add_argument(
        "--max-drift",
        type=float,
        default=0.15,
        dest="max_drift",
        help="Max tolerated character drift between de-tagged LLM output and gold "
        "(default: 0.15). Recovery always uses original gold characters; drift only "
        "affects language attribution accuracy. Pages above this are flagged for manual labeling.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        dest="batch_size",
        help="Concurrent page workers per dictionary (default: 1). "
        "Uses a thread pool over separate litellm.completion calls — not a "
        "provider batch-API flag. The global rate-limit pause is shared across threads.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max pages per dictionary (smoke test)."
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        type=int,
        metavar="N",
        help="Only label these page numbers (repeatable per dictionary).",
    )
    parser.add_argument(
        "--output-root",
        default=str(OUTPUT_ROOT),
        help="Root for *_lang.json output, one subfolder per dictionary "
        f"(default: {OUTPUT_ROOT}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-label pages even if a *_lang.json already exists "
        "(default: skip existing pages, no LLM call).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt for the first page of each dictionary; no LLM call.",
    )
    parser.add_argument(
        "--stage",
        choices=("all", "legend", "tagging"),
        default="all",
        help="Tier-2 stage: all (default), legend-only, or tagging-only.",
    )
    parser.add_argument(
        "--rules",
        action="append",
        dest="rules_paths",
        default=None,
        help="Extra labeling rules file (.txt, .md, .yaml). Repeatable. Merged with "
        "language_rules.yaml beside dictionary_languages.yaml.",
    )
    parser.add_argument(
        "--legend",
        default=None,
        help="Legend YAML for tagging (default: <dictionary>/language_legend.yaml).",
    )
    parser.add_argument(
        "--legend-scope",
        choices=("dictionary", "page"),
        default="dictionary",
        dest="legend_scope",
        help="Legend stage writes one dictionary legend (default) or per-page files.",
    )
    args = parser.parse_args(argv)

    root = Path(args.dictionaries_root)
    names = args.dictionaries or tier2_dictionaries(root)
    written = 0
    legends = 0
    skipped = 0
    failures: List[Tuple[str, int, str]] = []
    for name in names:
        dictionary_dir = root / name
        if not dictionary_dir.is_dir():
            print(f"skip {name}: not found under {root}")
            continue
        rules = read_dictionary_rules(dictionary_dir, args.rules_paths)
        seed = read_language_seed(dictionary_dir, args.rules_paths)
        if args.dry_run:
            pages = discover_gold_pages(dictionary_dir)
            if not pages:
                print(f"{name}: no gold pages")
                continue
            raw = pages[0].read_text(encoding="utf-8")
            markup = detect_markup_tags(raw)
            print(f"\n===== {name} (stage={args.stage}, seed: {', '.join(seed)}) =====")
            if args.stage == "legend":
                print(
                    build_legend_prompt(
                        raw,
                        seed,
                        rules_text=rules.rules_text,
                        markup_tags=markup,
                    )
                )
            elif args.stage == "tagging":
                try:
                    legend_path, legend = resolve_legend_for_tagging(
                        dictionary_dir,
                        pages[0],
                        args.output_root,
                        legend_override=args.legend,
                    )
                except FileNotFoundError:
                    legend_path = dictionary_legend_path(dictionary_dir)
                    legend = {"eng": "English", "fra": "French"}
                    print(f"(dry-run: no {legend_path.name}; using placeholder legend)\n")
                print(
                    build_tagging_prompt(
                        raw,
                        legend,
                        rules_text=rules.rules_text,
                        markup_tags=markup,
                    )
                )
            else:
                print(
                    build_prompt(
                        raw,
                        seed,
                        markup_tags=markup,
                        rules_text=rules.rules_text,
                    )
                )
            continue
        print(f"\n===== {name} (stage={args.stage}, seed: {', '.join(seed)}) =====")
        for result in label_dictionary(
            dictionary_dir,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            max_drift=args.max_drift,
            limit=args.limit,
            pages_only=args.pages,
            skip_existing=not args.overwrite,
            output_root=args.output_root,
            batch_size=args.batch_size,
            stage=args.stage,
            extra_rule_paths=args.rules_paths,
            legend_path_override=args.legend,
            legend_scope=args.legend_scope,
        ):
            if result.status == "skipped":
                skipped += 1
                if args.stage == "legend" and result.legend_path:
                    print(
                        f"  [skip] {name} :: legend exists "
                        f"-> {result.legend_path.name}"
                    )
                else:
                    print(
                        f"  [skip] {name} :: page {result.page} exists "
                        f"-> {result.out_path.name}"
                    )
                continue
            if result.status == "failed":
                failures.append((name, result.page, result.error or ""))
                print(f"  [FAIL] {name} :: page {result.page}: {result.error}")
                continue
            if result.status == "legend_ok":
                legends += 1
                assert result.legend_path is not None
                print(
                    f"  [legend] {name} "
                    f"({len(result.used)} languages) -> {result.legend_path.name}"
                )
                continue
            result.out_path.parent.mkdir(parents=True, exist_ok=True)
            result.page_map.save(result.out_path)
            written += 1
            extra = [lang for lang in result.used if lang not in seed]
            note = f"  (+discovered: {', '.join(extra)})" if extra else ""
            print(
                f"  [tier2] {name} :: page {result.page_map.page} "
                f"({len(result.page_map.spans)} spans, drift {result.drift:.1%}) "
                f"-> {result.out_path.name}{note}"
            )
    if args.stage == "legend":
        summary = f"\nTier-2 legend stage: {legends} legend file(s) written"
    else:
        summary = f"\nTier-2 labeling: {written} span map(s) written"
    if skipped:
        summary += f", {skipped} skipped (existing)"
    if failures:
        summary += f", {len(failures)} failed -> manual labeling"
    print(summary + (" (dry run)." if args.dry_run else "."))
    if failures:
        print("Pages needing manual labeling (drift/invariant gate):")
        for dict_name, page, error in failures:
            print(f"  - {dict_name} page {page}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
