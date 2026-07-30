"""Frozen v1 typography normalization for eval-flat and OCR adapters.

Converts markdown/HTML/LaTeX wrappers to gold-style ``<b>`` / ``<i>`` tags.
Idempotent on already-normalized lines.
"""

from __future__ import annotations

import html
import re
import unicodedata

from mudidi.evaluation.stage1.tag_parser import normalize_unicode

TYPOGRAPHY_SPEC_VERSION = "v1"

# Markdown bold/italic (non-greedy; processed bold before italic).
_MD_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD_UNDER = re.compile(r"__(.+?)__")
_MD_ITALIC_STAR = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_ITALIC_UNDER = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")

_HTML_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)>", re.IGNORECASE)
_BOLD_HTML = frozenset({"b", "strong"})
_ITALIC_HTML = frozenset({"i", "em"})
_PRESERVED_HTML = frozenset({"n"})  # part-of-speech marker ``<N>``

# LaTeX inline delimiters — keep inner text, drop markers.
_LATEX_INLINE = re.compile(
    r"\\\((.+?)\\\)|\$(?!\$)(.+?)(?<!\$)\$(?!\$)|\\\[(.+?)\\\]",
    re.DOTALL,
)

# MinerU / VLM OCR artifacts — strip before tag conversion.
_MINERU_XI_RUN = re.compile(r"(?:x<i>\{\d+\}</i>\s*)+", re.IGNORECASE)
_MALFORMED_HTML = re.compile(r"<x\{[^>]*>", re.IGNORECASE)
_LATEX_TEXT_CMD = re.compile(r"\\text\s*\{([^{}]*)\}")
_LATEX_MATHRM_CMD = re.compile(r"\\mathrm\{([^{}]*)\}")
_LATEX_MATHBB_CMD = re.compile(r"\\mathbb\{([^{}]*)\}")
_LATEX_OVERLINE_CMD = re.compile(r"\\overline\{([^{}]*)\}")
_LATEX_DROP_BARE = re.compile(r"\\(?:complement|dashv|lrcorner|circledast|wedge)\b")
_LATEX_TEXT_ARTIFACT = re.compile(r"[_^]\s*text\b", re.IGNORECASE)
_LATEX_TEXT_BRACED = re.compile(r"(?<![A-Za-z])text\s*\{([^{}]*)\}", re.IGNORECASE)
_LATEX_WEDGE_WORD = re.compile(r"(?<=[a-zA-Z])wedge(?=[-<])|\bwedge\b", re.IGNORECASE)
_HTML_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BARE_DOLLAR_SPAN = re.compile(r"\$(?!\$)(.{1,60}?)(?<!\$)\$(?!\$)")
_LATEX_BOLD_CMD = re.compile(
    r"\\(?:mathbf|boldsymbol|textbf|bm)\{([^{}]*)\}",
    re.IGNORECASE,
)
_LATEX_ITALIC_CMD = re.compile(
    r"\\(?:textit|mathit|emph|itshape)\{([^{}]*)\}",
    re.IGNORECASE,
)
_LATEX_CMD = re.compile(
    r"\\([a-zA-Z]+)(?:\{([^{}]*)\})?(?:_\{([^{}]*)\})?(?:\^\{([^{}]*)\})?"
)
_LATEX_DELIM = re.compile(r"\\\(|\\\)|\\\[|\\\]")
_BARE_LATEX_SCRIPT = re.compile(r"[_^]\{([^{}]*)\}")
_BRACED_SPAN = re.compile(r"(?<![<{])\{([^{}]{1,200})\}(?![>}])")
_LATEX_ARRAY_BLOCK = re.compile(
    r"\\?\{?\s*arrayl\s*|\s*\\\\\s*|\s*array\.?\s*",
    re.IGNORECASE,
)
_STRAY_BRACES = re.compile(r"[{}]")
_STRAY_BACKSLASH = re.compile(r"\\+")
_SWASTIKA_RUN = re.compile(r"卍{3,}")
_UNREADABLE_MARKER = re.compile(r"\[Unreadable\]", re.IGNORECASE)
_CHECKBOX = re.compile(r"☐\s*")
_MODIFIER_LETTERS = re.compile(r"[\u2070-\u209f\u02b0-\u02ff]+")
_ALLOWED_INLINE_TAG = re.compile(r"</?(?:b|i|N)>", re.IGNORECASE)

# Residual HTML-like tags after conversion (preserve dictionary <b>/<i>).
_RESIDUAL_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)>")
_ALLOWED_INLINE_TAGS = frozenset({"b", "i", "n"})

_JUNK_DIGIT_RATIO = 0.85
_JUNK_MIN_DIGIT_LEN = 80
_JUNK_LOW_DIVERSITY_LEN = 200
_JUNK_LOW_DIVERSITY_UNIQUE = 8
_JUNK_CHAR_REPEAT = 12
_JUNK_CJK_MIN_LEN = 80
_JUNK_CJK_RATIO = 0.55
_JUNK_CJK_MAX_LATIN_RATIO = 0.15
_REPLACEMENT_CHAR = "\ufffd"

_UNICODE_DASH_CHARS = "‐‑‒–—―−"
_HEADER_EM_DASH = re.compile(
    r"^\s*\S.*(?:"
    + r"[\-"
    + _UNICODE_DASH_CHARS
    + r"])\s*\d+\s*(?:[\-"
    + _UNICODE_DASH_CHARS
    + r"])\s+\S"
)

_CURLY_APOSTROPHE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u2039": "'",
        "\u203a": "'",
    }
)

# Sumero-Akkadian cuneiform (U+12000–U+1247F).
_CUNEIFORM_SPACE_CUNEIFORM = re.compile(
    r"([\U00012000-\U0001247F])\s+(?=[\U00012000-\U0001247F])"
)


def normalize_cuneiform_spacing(text: str) -> str:
    """Remove whitespace between consecutive cuneiform sign characters."""
    prev = None
    while prev != text:
        prev = text
        text = _CUNEIFORM_SPACE_CUNEIFORM.sub(r"\1", text)
    return text

def _strip_combining_marks(text: str) -> str:
    """NFD and drop Mn category so repeat detection works on Hebrew OCR spam."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _letter_ratios(text: str) -> tuple[float, float]:
    """Return (latin_ratio, cjk_ratio) over non-space characters."""
    nonspace = [ch for ch in text if not ch.isspace()]
    if not nonspace:
        return 0.0, 0.0
    n = len(nonspace)
    latin = sum(1 for ch in nonspace if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in nonspace if "\u4e00" <= ch <= "\u9fff")
    return latin / n, cjk / n


def is_junk_ocr_line(line: str) -> bool:
    """True if a normalized line is OCR noise with no dictionary content."""
    stripped = line.strip()
    if not stripped:
        return True
    if _REPLACEMENT_CHAR in stripped:
        return True
    if re.fullmatch(r"[\s\\]+", stripped):
        return True
    if len(stripped) >= _JUNK_MIN_DIGIT_LEN:
        digit_ratio = sum(ch.isdigit() for ch in stripped) / len(stripped)
        if digit_ratio >= _JUNK_DIGIT_RATIO:
            return True
    if len(stripped) > _JUNK_LOW_DIVERSITY_LEN:
        compact = stripped.replace(" ", "")
        if compact and len(set(compact)) <= _JUNK_LOW_DIVERSITY_UNIQUE:
            return True
    repeat_pat = r"(.)\1{" + str(_JUNK_CHAR_REPEAT - 1) + r",}"
    if re.search(repeat_pat, stripped):
        return True
    if re.search(repeat_pat, _strip_combining_marks(stripped)):
        return True
    latin_ratio, cjk_ratio = _letter_ratios(stripped)
    if (
        len(stripped) >= _JUNK_CJK_MIN_LEN
        and cjk_ratio >= _JUNK_CJK_RATIO
        and latin_ratio <= _JUNK_CJK_MAX_LATIN_RATIO
    ):
        return True
    return False


def _strip_unknown_tags(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name in _ALLOWED_INLINE_TAGS:
            return match.group(0)
        return ""

    return _RESIDUAL_TAG_RE.sub(repl, text)


def _html_to_dictionary_tags(text: str) -> str:
    """Map HTML bold/italic tags to ``<b>`` / ``<i>``; strip other tags."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        closing = match.group(0).startswith("</")
        if name in _BOLD_HTML:
            return "</b>" if closing else "<b>"
        if name in _ITALIC_HTML:
            return "</i>" if closing else "<i>"
        if name in _PRESERVED_HTML:
            return "</N>" if closing else "<N>"
        return ""

    return _HTML_TAG_RE.sub(repl, text)


def _markdown_to_dictionary_tags(text: str) -> str:
    """Convert markdown emphasis to ``<b>`` / ``<i>``."""
    text = _MD_BOLD_STAR.sub(r"<b>\1</b>", text)
    text = _MD_BOLD_UNDER.sub(r"<b>\1</b>", text)
    text = _MD_ITALIC_STAR.sub(r"<i>\1</i>", text)
    text = _MD_ITALIC_UNDER.sub(r"<i>\1</i>", text)
    return text


def _latex_typography_to_dictionary_tags(text: str) -> str:
    """Map Mathpix-style LaTeX bold/italic commands to ``<b>`` / ``<i>``."""
    prev = None
    while prev != text:
        prev = text
        text = _LATEX_BOLD_CMD.sub(r"<b>\1</b>", text)
        text = _LATEX_ITALIC_CMD.sub(r"<i>\1</i>", text)
    return text


def _strip_stray_angles(text: str) -> str:
    """Remove orphan ``<``/``>`` while preserving dictionary inline tags."""
    placeholders: dict[str, str] = {}

    def shield(match: re.Match[str]) -> str:
        key = f"\x00T{len(placeholders)}\x00"
        placeholders[key] = match.group(0)
        return key

    protected = _ALLOWED_INLINE_TAG.sub(shield, text)
    protected = protected.replace("<", "").replace(">", "")
    for key, tag in placeholders.items():
        protected = protected.replace(key, tag)
    return protected


def _unwrap_latex_commands(text: str) -> str:
    """Unwrap ``\\cmd{inner}`` / subscripts to braced content; drop bare ``\\cmd``."""

    def repl(match: re.Match[str]) -> str:
        cmd = match.group(1) or ""
        for group in match.groups()[1:]:
            if group:
                return group
        if cmd.isalpha() and len(cmd) <= 6:
            return cmd
        return ""

    prev = None
    while prev != text:
        prev = text
        text = _LATEX_CMD.sub(repl, text)
    return text


def _normalize_dashes(text: str) -> str:
    """Use em-dash in page-header lines; hyphen-minus elsewhere."""
    if _HEADER_EM_DASH.search(text):
        for ch in _UNICODE_DASH_CHARS:
            text = text.replace(ch, "—")
        return text.replace("-", "—")
    for ch in _UNICODE_DASH_CHARS:
        text = text.replace(ch, "-")
    return text


def _normalize_apostrophes(text: str) -> str:
    """Map curly apostrophe variants to ASCII ``'``."""
    return text.translate(_CURLY_APOSTROPHE)


def _strip_ocr_garbage(text: str) -> str:
    """Remove known VLM OCR noise before markdown/HTML tag mapping."""
    text = html.unescape(text)
    text = _HTML_BR.sub(" ", text)
    text = _MINERU_XI_RUN.sub("", text)
    text = _MALFORMED_HTML.sub("", text)
    text = _latex_typography_to_dictionary_tags(text)
    text = _LATEX_TEXT_CMD.sub(r"\1", text)
    text = _LATEX_MATHRM_CMD.sub(r"\1", text)
    text = _LATEX_MATHBB_CMD.sub(r"\1", text)
    text = _LATEX_OVERLINE_CMD.sub(r"\1", text)
    text = _LATEX_DROP_BARE.sub("", text)
    prev = None
    while prev != text:
        prev = text
        text = _BARE_DOLLAR_SPAN.sub(r"\1", text)
    text = _unwrap_latex_commands(text)
    text = _LATEX_WEDGE_WORD.sub("", text)
    text = _BARE_LATEX_SCRIPT.sub(r"\1", text)
    text = _LATEX_TEXT_ARTIFACT.sub("", text)
    prev = None
    while prev != text:
        prev = text
        text = _BRACED_SPAN.sub(r"\1", text)
    text = _LATEX_TEXT_BRACED.sub(r"\1", text)
    text = _BARE_LATEX_SCRIPT.sub(r"\1", text)
    text = _LATEX_TEXT_ARTIFACT.sub("", text)
    text = _LATEX_DELIM.sub("", text)
    text = _SWASTIKA_RUN.sub(" ", text)
    text = _CHECKBOX.sub("", text)
    text = _MODIFIER_LETTERS.sub("", text)
    text = _UNREADABLE_MARKER.sub("", text)
    text = text.replace(_REPLACEMENT_CHAR, "")
    prev = None
    while prev != text:
        prev = text
        text = _LATEX_ARRAY_BLOCK.sub(" ", text)
    text = _STRAY_BACKSLASH.sub("", text)
    text = _STRAY_BRACES.sub("", text)
    text = re.sub(r" {3,}", "  ", text)
    return text


def _strip_latex_delimiters(text: str) -> str:
    """Remove common LaTeX math delimiters while keeping content."""

    def repl(match: re.Match[str]) -> str:
        for group in match.groups():
            if group is not None:
                return group
        return ""

    return _LATEX_INLINE.sub(repl, text)


def normalize_line(line: str) -> str:
    """
    Normalize one line of OCR or transcript text to dictionary tag conventions.

    Order: OCR garbage → LaTeX delimiters → markdown → HTML mapping → strip unknown tags
    → stray angles → NFC → cuneiform spacing.
    Returns empty string for known junk OCR lines (digit runs, symbol spam).
    """
    if not line:
        return ""
    if _REPLACEMENT_CHAR in line:
        return ""
    text = _strip_ocr_garbage(line)
    text = _strip_latex_delimiters(text)
    text = _markdown_to_dictionary_tags(text)
    text = _html_to_dictionary_tags(text)
    text = _strip_unknown_tags(text)
    text = _strip_stray_angles(text)
    text = re.sub(r"<b></b>|<i></i>", "", text)
    text = _normalize_dashes(text)
    text = _normalize_apostrophes(text)
    text = normalize_unicode(text)
    text = normalize_cuneiform_spacing(text)
    if is_junk_ocr_line(text):
        return ""
    return text
