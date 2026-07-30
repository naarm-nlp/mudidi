"""Per-language Stage 1 evaluation by alignment-to-gold.

Attributes every Stage 1 grapheme/word edit to the language of the gold unit it
aligns to, using the page's :class:`~mudidi.schemas.language_span.PageLanguageMap`.

Two kinds of content are excluded from per-language-script scoring, for two
different reasons:

- **Punctuation + whitespace** are *physically stripped* from both the gold and
  pred scoring strings before any grapheme/word derivation -- symmetric and
  content-derivable (a period is a period on either side), the same technique
  ``tag_parser.strip_tags`` already uses for markup.
- **``SPACE``/``META`` labels** are *not* physically removed from gold before
  alignment -- doing so would make the prediction's real transcription of meta
  content (running heads, editorial markers) look like a giant spurious
  insertion, misattributed to whichever real language-script bucket sits next
  to it. Instead, ``SPACE``/``META`` buckets are allowed to form naturally
  during alignment/bucketing and are dropped from the final
  :class:`~mudidi.evaluation.stage1.per_language_metrics.PageLanguageReport`
  afterwards.

Because ``SPACE``/``META`` buckets are excluded from the final report, the
per-language grapheme edits/gold no longer pool back to the whole-page
``compute_character_quality`` blended metric (that used to be true and was
described as a "consistency oracle" -- it no longer holds by construction).
``blended_grapheme_edits`` / ``blended_graphemes_gold`` / ``blended_gcer`` on the
returned report are instead recomputed as the sum over the *remaining* (real
language-script) buckets only -- "combined stats across real language-script
tags," a different and narrower question than the whole-page GCER.

Two entry points:

- :func:`compute_per_language_quality` -- the core. Takes the *clean* (tag-stripped,
  normalized, NOT yet casefolded) collapsed gold/pred strings plus the *raw* gold and
  its span map. This is what :class:`FlatStage1Evaluator` feeds from a collapsed
  ``AlignmentResult`` (``pair.gold.text`` / ``pair.pred.text``).
- :func:`evaluate_per_language` -- a file-based convenience wrapper that reads the
  prediction, gold flat file, and ``*_lang.json``, builds the collapsed clean strings
  itself, and calls the core.

This module is deliberately **scipy-free**. The blended page metric for a *collapsed*
page is one gold string vs one pred string, so the OmniDocBench ``quick_match`` /
``linear_sum_assignment`` machinery in :mod:`mudidi.evaluation.stage1.alignment` is
not needed here; the collapsed clean string is reproduced from the scipy-free
``tag_parser`` primitives, exactly mirroring ``alignment.clean_text`` /
``collapse_rows_to_page``::

    collapsed = " ".join(line for line in load_flat_lines(path) if line)
    pair_text = normalize_line_text(strip_tags(collapsed))   # == pair.gold.text

Keeping it scipy-free means per-language eval runs even where the prebuilt scipy
PROPACK extension fails to load, and keeps scipy isolated in ``alignment.py``.

Graphemes are encoded to single sentinel codepoints before ``Levenshtein.editops`` /
``opcodes`` so edits are counted per grapheme cluster, regardless of how many
codepoints a cluster spans.
"""

from __future__ import annotations

import dataclasses
import unicodedata
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

import grapheme
import jiwer
import Levenshtein

from mudidi.evaluation.stage1.flatten import load_flat_lines
from mudidi.evaluation.stage1.language_projection import (
    grapheme_languages,
    project_clean_languages,
    word_languages,
)
from mudidi.evaluation.stage1.per_language_metrics import (
    PageLanguageReport,
    PerLanguageMetrics,
)
from mudidi.evaluation.stage1.tag_parser import (
    casefold_letters_for_eval,
    normalize_line_text,
    strip_tags,
)
from mudidi.schemas.language_span import META, SPACE, PageLanguageMap

# Identical to ``character_quality._JIWER_WORD_TRANSFORM`` (re-declared so this
# module stays scipy-free; importing ``character_quality`` pulls in ``alignment`` and
# therefore scipy).
_JIWER_WORD_TRANSFORM = jiwer.Compose([jiwer.ReduceToListOfListOfWords()])

# Private-use codepoints used to encode grapheme clusters as single characters.
_PUA_BASE = 0xE000


def _bump(counter: Dict[str, int], key: str, amount: int = 1) -> None:
    counter[key] = counter.get(key, 0) + amount


def collapsed_clean_text(path: str | Path) -> str:
    """Reproduce ``align_page_collapsed`` gold/pred span text for one flat page."""
    collapsed = " ".join(line for line in load_flat_lines(path) if line)
    return normalize_line_text(strip_tags(collapsed))


def lang_map_path_for_gold(gold_path: str | Path) -> Path:
    """Return the ``*_lang.json`` path co-located with a gold flat file.

    Follows the ``Stage 1 Gold OCR/<stem>/<stem>_lang.json`` convention (same
    ``stem = gold_path.parent.name`` rule used by
    :func:`~mudidi.evaluation.stage1.stage1_task_discovery.discover_dataset_tasks`).
    Does not check existence -- callers decide what to do when the file is absent.
    """
    gold_path = Path(gold_path)
    return gold_path.parent / f"{gold_path.parent.name}_lang.json"


def _source_language(lang_map: PageLanguageMap) -> str:
    for span in lang_map.spans:
        if span.language not in (SPACE, META):
            return span.language
    return SPACE


def _encode_graphemes(gg: Sequence[str], pg: Sequence[str]) -> Tuple[str, str]:
    """Map grapheme clusters to distinct single codepoints (shared alphabet)."""
    alphabet: Dict[str, str] = {}

    def encode(seq: Sequence[str]) -> str:
        out: List[str] = []
        for cluster in seq:
            if cluster not in alphabet:
                alphabet[cluster] = chr(_PUA_BASE + len(alphabet))
            out.append(alphabet[cluster])
        return "".join(out)

    return encode(gg), encode(pg)


def _is_scoring_noise(ch: str) -> bool:
    """Whitespace or punctuation: symmetric, content-derivable on both gold and pred.

    Physically stripped from the character-level (grapheme) scoring strings before
    diffing -- a period is a period on either side, so removing it can't create a
    spurious edit, unlike ``SPACE``/``META`` labels (see module docstring).
    """
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def _is_scoring_punct(ch: str) -> bool:
    """Punctuation only (not whitespace).

    Used to build the word-level scoring strings: punctuation is stripped so
    trailing/standalone punctuation doesn't inflate WER, but whitespace is kept
    because it is the delimiter ``word_languages``/jiwer split words on.
    """
    return unicodedata.category(ch).startswith("P")


def _filter_text(text: str, noise: Callable[[str], bool]) -> str:
    """Drop characters where ``noise(ch)`` is true."""
    return "".join(ch for ch in text if not noise(ch))


def _filter_text_with_langs(
    text: str, langs: Sequence[str], noise: Callable[[str], bool]
) -> Tuple[str, List[str]]:
    """Drop characters where ``noise(ch)`` is true, keeping ``text``/``langs`` aligned."""
    kept_chars: List[str] = []
    kept_langs: List[str] = []
    for ch, lang in zip(text, langs):
        if not noise(ch):
            kept_chars.append(ch)
            kept_langs.append(lang)
    return "".join(kept_chars), kept_langs


def _drop_reserved_buckets(report: PageLanguageReport) -> PageLanguageReport:
    """Drop ``SPACE``/``META`` buckets; recompute blended totals from real buckets.

    ``SPACE``/``META`` are allowed to accumulate naturally during alignment (see
    module docstring), then dropped here so they never appear in the final report.
    ``blended_*`` fields are redefined as the sum over the remaining real
    language-script buckets only -- not the whole-page ``compute_character_quality``
    GCER, which this report no longer needs to match.
    """
    per_language = {
        language: metrics
        for language, metrics in report.per_language.items()
        if language not in (SPACE, META)
    }
    blended_edits = sum(m.total_grapheme_edits for m in per_language.values())
    blended_gold = sum(m.total_graphemes_gold for m in per_language.values())
    return dataclasses.replace(
        report,
        per_language=per_language,
        blended_grapheme_edits=blended_edits,
        blended_graphemes_gold=blended_gold,
        blended_gcer=blended_edits / blended_gold if blended_gold else 0.0,
    )


class _Accumulator:
    """Accumulates per-language character and word counts."""

    def __init__(self, source_language: str) -> None:
        self.source = source_language
        self.gold_graphemes: Dict[str, int] = {}
        self.pred_graphemes: Dict[str, int] = {}
        self.grapheme_edits: Dict[str, int] = {}
        self.words_gold: Dict[str, int] = {}
        self.word_edits: Dict[str, int] = {}
        self.tp: Dict[str, int] = {}
        self.fp: Dict[str, int] = {}
        self.fn: Dict[str, int] = {}

    def _insert_language(self, gold_langs: Sequence[str], index: int) -> str:
        if index > 0 and gold_langs:
            return gold_langs[min(index - 1, len(gold_langs) - 1)]
        return gold_langs[0] if gold_langs else self.source

    def add_chars(self, gg: List[str], pg: List[str], gold_langs: List[str]) -> None:
        for language in gold_langs:
            _bump(self.gold_graphemes, language)

        encoded_g, encoded_p = _encode_graphemes(gg, pg)

        # Edit counting: len(editops) == Levenshtein.distance == blended total.
        for op, i, _j in Levenshtein.editops(encoded_g, encoded_p):
            if op in ("delete", "replace"):
                _bump(self.grapheme_edits, gold_langs[i])
            else:  # insert
                _bump(self.grapheme_edits, self._insert_language(gold_langs, i))

        # Attribution (P/R/F1) and pred-grapheme counts via block opcodes.
        for tag, i1, i2, j1, j2 in Levenshtein.opcodes(encoded_g, encoded_p):
            if tag == "equal":
                for k in range(i2 - i1):
                    language = gold_langs[i1 + k]
                    _bump(self.tp, language)
                    _bump(self.pred_graphemes, language)
            elif tag == "replace":
                for k in range(i2 - i1):
                    _bump(self.fn, gold_langs[i1 + k])
                for k in range(j2 - j1):
                    language = gold_langs[i1 + min(k, (i2 - i1) - 1)]
                    _bump(self.fp, language)
                    _bump(self.pred_graphemes, language)
            elif tag == "delete":
                for k in range(i2 - i1):
                    _bump(self.fn, gold_langs[i1 + k])
            elif tag == "insert":
                language = self._insert_language(gold_langs, i1)
                for _k in range(j2 - j1):
                    _bump(self.fp, language)
                    _bump(self.pred_graphemes, language)

    def add_words(
        self, gold_clean: str, pred_clean: str, word_langs: List[Tuple[str, str]]
    ) -> None:
        for _word, language in word_langs:
            _bump(self.words_gold, language)

        if not gold_clean.split() and not pred_clean.split():
            return

        output = jiwer.process_words(
            [gold_clean],
            [pred_clean],
            reference_transform=_JIWER_WORD_TRANSFORM,
            hypothesis_transform=_JIWER_WORD_TRANSFORM,
        )
        for chunk in output.alignments[0]:
            if chunk.type == "equal":
                continue
            if chunk.type in ("substitute", "delete"):
                for ref in range(chunk.ref_start_idx, chunk.ref_end_idx):
                    language = (
                        word_langs[ref][1] if ref < len(word_langs) else self.source
                    )
                    _bump(self.word_edits, language)
            elif chunk.type == "insert":
                ref = chunk.ref_start_idx
                if ref > 0 and word_langs:
                    language = word_langs[min(ref - 1, len(word_langs) - 1)][1]
                else:
                    language = word_langs[0][1] if word_langs else self.source
                for _hyp in range(chunk.hyp_start_idx, chunk.hyp_end_idx):
                    _bump(self.word_edits, language)

    def to_report(
        self,
        page_id: str,
        blended_edits: int,
        blended_gold: int,
    ) -> PageLanguageReport:
        languages = (
            set(self.gold_graphemes)
            | set(self.pred_graphemes)
            | set(self.grapheme_edits)
            | set(self.words_gold)
            | set(self.word_edits)
            | set(self.tp)
            | set(self.fp)
            | set(self.fn)
        )
        per_language: Dict[str, PerLanguageMetrics] = {}
        for language in languages:
            gold = self.gold_graphemes.get(language, 0)
            pred = self.pred_graphemes.get(language, 0)
            edits = self.grapheme_edits.get(language, 0)
            words_gold = self.words_gold.get(language, 0)
            word_edits = self.word_edits.get(language, 0)
            per_language[language] = PerLanguageMetrics(
                language=language,
                gcer=edits / gold if gold else 0.0,
                wer=word_edits / words_gold if words_gold else 0.0,
                text_edit=edits / max(gold, pred, 1),
                total_graphemes_gold=gold,
                total_graphemes_pred=pred,
                total_grapheme_edits=edits,
                total_words_gold=words_gold,
                total_word_edits=word_edits,
                attr_tp=self.tp.get(language, 0),
                attr_fp=self.fp.get(language, 0),
                attr_fn=self.fn.get(language, 0),
            )
        return PageLanguageReport(
            page_id=page_id,
            per_language=per_language,
            blended_gcer=blended_edits / blended_gold if blended_gold else 0.0,
            blended_grapheme_edits=blended_edits,
            blended_graphemes_gold=blended_gold,
        )


def compute_per_language_quality(
    gold_clean: str,
    pred_clean: str,
    raw_gold: str,
    lang_map: PageLanguageMap,
    *,
    page_id: str = "",
) -> PageLanguageReport:
    """Attribute Stage 1 character/word error per language for one collapsed page.

    Args:
        gold_clean: Collapsed *clean* gold (tag-stripped + normalized, NOT casefolded)
            -- i.e. ``pair.gold.text`` of a collapsed ``AlignmentResult``.
        pred_clean: Collapsed *clean* prediction (``pair.pred.text``).
        raw_gold: The immutable raw gold text the span map is bound to.
        lang_map: The page's validated language span map.
        page_id: Optional page identifier for the report.

    Returns:
        A :class:`PageLanguageReport` with ``SPACE``/``META`` buckets excluded and
        ``blended_*`` fields summed over the remaining real language-script buckets
        (see module docstring -- this no longer equals the whole-page
        ``compute_character_quality`` GCER).

    Raises:
        SpanMapError: if the span map does not bind to / fully cover ``raw_gold``.
    """
    # ``language_char_map`` validates the gold binding (sha + full coverage) and
    # raises SpanMapError on mismatch, so a separate ``validate_against`` pass would
    # only re-hash the whole page for nothing.
    raw_char_lang = lang_map.language_char_map(raw_gold)
    source_language = _source_language(lang_map)

    gc = casefold_letters_for_eval(gold_clean)
    pc = casefold_letters_for_eval(pred_clean)
    clean_lang = project_clean_languages(raw_gold, raw_char_lang, gc)

    # -- Grapheme (character-level) scoring: punctuation + whitespace physically
    # stripped from both sides before diffing (safe -- symmetric/content-derivable).
    gc_chars, clean_lang_chars = _filter_text_with_langs(gc, clean_lang, _is_scoring_noise)
    pc_chars = _filter_text(pc, _is_scoring_noise)
    gg = list(grapheme.graphemes(gc_chars))
    pg = list(grapheme.graphemes(pc_chars))
    gold_langs = grapheme_languages(gc_chars, clean_lang_chars)

    # -- Word-level scoring: punctuation stripped, whitespace kept as the delimiter
    # ``word_languages``/jiwer split words on (stripping it would collapse every
    # line into a single "word", destroying WER).
    gc_words, clean_lang_words = _filter_text_with_langs(gc, clean_lang, _is_scoring_punct)
    pc_words = _filter_text(pc, _is_scoring_punct)
    word_langs = word_languages(gc_words, clean_lang_words, source_language=source_language)

    acc = _Accumulator(source_language)
    acc.add_chars(gg, pg, gold_langs)
    acc.add_words(gc_words, pc_words, word_langs)
    # SPACE/META buckets form naturally above (unfiltered by construction -- meta
    # content is real text the prediction also transcribes); drop them here and
    # recompute blended totals from the remaining real language-script buckets.
    return _drop_reserved_buckets(acc.to_report(page_id, blended_edits=0, blended_gold=0))


def evaluate_per_language(
    pred_path: str | Path,
    gold_path: str | Path,
    lang_map_path: str | Path,
    page_id: str = "",
) -> PageLanguageReport:
    """Evaluate a prediction file per language against gold + its span map.

    Reads the prediction and gold flat files, collapses each to a single clean page
    string (matching ``align_page_collapsed``), loads the ``*_lang.json`` span map,
    and delegates to :func:`compute_per_language_quality`.

    Raises:
        SpanMapError: if the span map does not bind to / fully cover the gold text.
    """
    raw_gold = Path(gold_path).read_text(encoding="utf-8")
    lang_map = PageLanguageMap.load(lang_map_path)
    return compute_per_language_quality(
        collapsed_clean_text(gold_path),
        collapsed_clean_text(pred_path),
        raw_gold,
        lang_map,
        page_id=page_id,
    )
