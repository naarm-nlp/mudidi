"""Stage 2 MDF evaluation: record detection, marker F1, read order."""

from __future__ import annotations

import csv
import json
import logging
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Sequence

import Levenshtein

from mudidi.evaluation.stage2.mdf_align import align_lines, align_records
from mudidi.evaluation.stage2.mdf_marker_equiv import markers_equivalent
from mudidi.evaluation.stage2.mdf_marker_roles import (
    GLOSS_MARKERS,
    HEADWORD_MARKERS,
    load_language_map_for_page,
    marker_role_bucket,
)
from mudidi.evaluation.stage2.mdf_metrics import (
    MarkerErrorSample,
    MdfPageMetrics,
    PrfCounts,
    ReadOrderMetrics,
    RecordIndexSample,
    RecordSample,
)
from mudidi.evaluation.stage2.mdf_parser import parse_mdf, normalize_field_value
from mudidi.evaluation.stage1.stage1_metrics import CharacterQualityMetrics
from mudidi.evaluation.text_quality import aggregate_text_quality, merge_character_quality
from mudidi.schemas.language_span import META

logger = logging.getLogger(__name__)

DEFAULT_RECORD_THRESHOLD = 0.6
DEFAULT_LINE_THRESHOLD = 0.7


@dataclass(frozen=True)
class MdfEvalTask:
    """One Stage 2 MDF evaluation unit."""

    experiment: str
    pred_path: Path
    gold_path: Path
    page_id: str


def compute_read_order_metrics(
    matched_gold_indices: Sequence[int],
    matched_pred_indices: Sequence[int],
    n_gold: int,
) -> ReadOrderMetrics:
    """Compute ReadOrderEdit from matched record index pairs."""
    pairs = sorted(zip(matched_gold_indices, matched_pred_indices), key=lambda x: x[1])
    gt = list(range(n_gold))
    pred_order = [gold_idx for gold_idx, _ in pairs]
    dist = Levenshtein.distance(gt, pred_order)
    maxlen = max(len(gt), len(pred_order), 1)
    return ReadOrderMetrics(
        read_order_edit=dist / maxlen,
        edit_distance=dist,
        max_length=maxlen,
    )


class MdfEvaluator:
    """Evaluate predicted MDF against gold using fuzzy record and line alignment."""

    def __init__(
        self,
        *,
        record_threshold: float = DEFAULT_RECORD_THRESHOLD,
        line_threshold: float = DEFAULT_LINE_THRESHOLD,
        marker_sub_list_path: str | Path | None = None,
        dictionary_languages_path: str | Path | None = None,
    ) -> None:
        self.record_threshold = record_threshold
        self.line_threshold = line_threshold
        self.marker_sub_list_path = str(marker_sub_list_path) if marker_sub_list_path else None
        self.dictionary_languages_path = (
            Path(dictionary_languages_path) if dictionary_languages_path else None
        )

    @staticmethod
    def _projection_path_for_gold(gold_path: Path) -> Path:
        page_id = gold_path.parent.name
        return gold_path.with_name(f"{page_id}_mdf_lang_projection.json")

    @classmethod
    def _load_language_script_by_line(cls, gold_path: Path) -> Dict[int, str]:
        projection_path = cls._projection_path_for_gold(gold_path)
        if not projection_path.is_file():
            return {}
        data = json.loads(projection_path.read_text(encoding="utf-8"))
        by_line: Dict[int, str] = {}
        for field in data.get("fields", []):
            line_index = field.get("line_index")
            language = str(field.get("primary_language") or "").strip()
            if isinstance(line_index, int) and language:
                by_line[line_index] = language
        return by_line

    @staticmethod
    def _lang_script_scoring_value(value: str) -> str:
        normalized = normalize_field_value(value)
        return "".join(
            ch
            for ch in normalized
            if not ch.isspace() and not unicodedata.category(ch).startswith("P")
        )

    @classmethod
    def _append_language_script_pair(
        cls,
        pairs: DefaultDict[str, List[tuple[str, str]]],
        *,
        language_by_line: Dict[int, str],
        line_index: int,
        gold_value: str,
        pred_value: str,
    ) -> None:
        language = language_by_line.get(line_index)
        if not language or language == META:
            return
        gold_scoring = cls._lang_script_scoring_value(gold_value)
        pred_scoring = cls._lang_script_scoring_value(pred_value)
        if not gold_scoring and not pred_scoring:
            return
        pairs[language].append((gold_scoring, pred_scoring))

    def evaluate(
        self,
        pred_path: str | Path,
        gold_path: str | Path,
        page_id: str = "",
    ) -> MdfPageMetrics:
        """Evaluate one predicted MDF page against gold."""
        pred_path = Path(pred_path)
        gold_path = Path(gold_path)
        gold_text = gold_path.read_text(encoding="utf-8")
        pred_text = pred_path.read_text(encoding="utf-8")

        gold_records = parse_mdf(gold_text)
        pred_records = parse_mdf(pred_text)
        record_alignment = align_records(
            gold_records,
            pred_records,
            threshold=self.record_threshold,
        )

        marker_counts = PrfCounts()
        confusion: DefaultDict[str, Counter[str]] = defaultdict(Counter)
        marker_errors: List[MarkerErrorSample] = []
        record_samples: List[RecordSample] = []
        all_value_pairs: List[tuple[str, str]] = []
        headword_pairs: List[tuple[str, str]] = []
        gloss_pairs: List[tuple[str, str]] = []
        language_pairs: DefaultDict[str, List[tuple[str, str]]] = defaultdict(list)
        language_script_pairs: DefaultDict[str, List[tuple[str, str]]] = defaultdict(list)

        language_map = load_language_map_for_page(
            pred_path,
            dictionary_languages_path=self.dictionary_languages_path,
        )
        language_script_by_line = self._load_language_script_by_line(gold_path)

        for match in record_alignment.matched:
            gold_record = gold_records[match.gold_index]
            pred_record = pred_records[match.pred_index]
            line_alignment = align_lines(
                gold_record.lines,
                pred_record.lines,
                threshold=self.line_threshold,
            )

            for line_match in line_alignment.matched:
                gold_line = gold_record.lines[line_match.gold_index]
                pred_line = pred_record.lines[line_match.pred_index]
                gold_value = normalize_field_value(gold_line.value)
                pred_value = normalize_field_value(pred_line.value)
                all_value_pairs.append((gold_value, pred_value))
                if gold_line.marker in HEADWORD_MARKERS:
                    headword_pairs.append((gold_value, pred_value))
                if gold_line.marker in GLOSS_MARKERS:
                    gloss_pairs.append((gold_value, pred_value))
                bucket = marker_role_bucket(gold_line.marker, language_map)
                if bucket:
                    language_pairs[bucket].append((gold_value, pred_value))
                self._append_language_script_pair(
                    language_script_pairs,
                    language_by_line=language_script_by_line,
                    line_index=gold_line.line_index,
                    gold_value=gold_line.value,
                    pred_value=pred_line.value,
                )
                if markers_equivalent(
                    gold_line.marker,
                    pred_line.marker,
                    sub_list_path=self.marker_sub_list_path,
                ):
                    marker_counts.tp += 1
                else:
                    marker_counts.fp += 1
                    marker_counts.fn += 1
                    confusion[gold_line.marker][pred_line.marker] += 1
                    if len(marker_errors) < 15:
                        marker_errors.append(
                            MarkerErrorSample(
                                headword=gold_record.headword,
                                gold=f"\\{gold_line.marker} {gold_line.value}",
                                pred=f"\\{pred_line.marker} {pred_line.value}",
                                value_sim=round(line_match.similarity, 3),
                            )
                        )

            marker_counts.fn += len(line_alignment.missing_gold)
            marker_counts.fp += len(line_alignment.extra_pred)

            for gold_idx in line_alignment.missing_gold:
                gold_line = gold_record.lines[gold_idx]
                self._append_language_script_pair(
                    language_script_pairs,
                    language_by_line=language_script_by_line,
                    line_index=gold_line.line_index,
                    gold_value=gold_line.value,
                    pred_value="",
                )

            if match.similarity < 0.95:
                record_samples.append(
                    RecordSample(
                        gold_index=match.gold_index,
                        pred_index=match.pred_index,
                        similarity=round(match.similarity, 3),
                        headword_gold=gold_record.headword,
                        headword_pred=pred_record.headword,
                        gold_lines=len(gold_record.lines),
                        pred_lines=len(pred_record.lines),
                        line_pairs=len(line_alignment.matched),
                        missing_lines=len(line_alignment.missing_gold),
                        extra_lines=len(line_alignment.extra_pred),
                    )
                )

        for gold_record_idx in record_alignment.missing_gold:
            for gold_line in gold_records[gold_record_idx].lines:
                self._append_language_script_pair(
                    language_script_pairs,
                    language_by_line=language_script_by_line,
                    line_index=gold_line.line_index,
                    gold_value=gold_line.value,
                    pred_value="",
                )

        read_order = compute_read_order_metrics(
            [m.gold_index for m in record_alignment.matched],
            [m.pred_index for m in record_alignment.matched],
            len(gold_records),
        )

        language_quality = {
            bucket: aggregate_text_quality(pairs)
            for bucket, pairs in sorted(language_pairs.items())
        }
        language_script_quality = {
            bucket: aggregate_text_quality(pairs)
            for bucket, pairs in sorted(language_script_pairs.items())
        }

        return MdfPageMetrics(
            page_id=page_id or gold_path.parent.name,
            gold_path=str(gold_path),
            pred_path=str(pred_path),
            n_pred_records=len(pred_records),
            record=PrfCounts(
                tp=len(record_alignment.matched),
                fp=len(record_alignment.extra_pred),
                fn=len(record_alignment.missing_gold),
            ),
            marker=marker_counts,
            read_order=read_order,
            field_value_quality=aggregate_text_quality(all_value_pairs),
            headword_quality=aggregate_text_quality(headword_pairs),
            gloss_quality=aggregate_text_quality(gloss_pairs),
            language_quality=language_quality,
            language_script_quality=language_script_quality,
            marker_confusion={g: dict(c) for g, c in confusion.items()},
            record_samples=record_samples[:12],
            marker_error_samples=marker_errors,
            missing_record_samples=[
                RecordIndexSample(
                    index=idx,
                    headword=gold_records[idx].headword,
                    fingerprint_preview=gold_records[idx].fingerprint()[:120],
                )
                for idx in record_alignment.missing_gold[:8]
            ],
            extra_record_samples=[
                RecordIndexSample(
                    index=idx,
                    headword=pred_records[idx].headword,
                    fingerprint_preview=pred_records[idx].fingerprint()[:120],
                )
                for idx in record_alignment.extra_pred[:8]
            ],
        )

    @staticmethod
    def discover_tasks(
        samples_dir: str | Path,
        experiments: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
    ) -> List[MdfEvalTask]:
        """Discover gold/pred MDF pairs under a samples tree."""
        samples_dir = Path(samples_dir)
        tasks: List[MdfEvalTask] = []
        selected_languages = set(languages) if languages else None

        golds_by_lang: Dict[Path, List[Path]] = {}
        for gold_path in sorted(samples_dir.glob("*/outputs/stage-2-gold/*/*.mdf.txt")):
            lang_dir = gold_path.parents[3]
            if selected_languages and lang_dir.name not in selected_languages:
                continue
            golds_by_lang.setdefault(lang_dir, []).append(gold_path)

        for lang_dir, gold_paths in sorted(golds_by_lang.items()):
            stage2_root = lang_dir / "outputs" / "stage-2"
            if not stage2_root.is_dir():
                continue
            available = sorted(
                p.name for p in stage2_root.iterdir() if p.is_dir() and not p.name.startswith(".")
            )
            exp_names = (
                available
                if experiments is None
                else [e for e in experiments if e in available]
            )
            for exp in exp_names:
                for gold_path in gold_paths:
                    stem = gold_path.parent.name
                    pred_path = stage2_root / exp / stem / f"{stem}.mdf.txt"
                    if not pred_path.is_file():
                        logger.debug("skip missing pred: %s", pred_path)
                        continue
                    tasks.append(
                        MdfEvalTask(
                            experiment=exp,
                            pred_path=pred_path,
                            gold_path=gold_path,
                            page_id=f"{lang_dir.name}/{stem}",
                        )
                    )
        return tasks

    @staticmethod
    def discover_dataset_tasks(
        dataset_dir: str | Path,
        pred_root: str | Path,
        experiments: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
    ) -> List[MdfEvalTask]:
        """Discover gold/pred MDF pairs under the MUDIDI dataset layout.

        Gold files are read from:
            dataset_dir/<language>/Stage 2 MDF file/<page>/<page>.mdf.txt

        Predictions are read from:
            pred_root/<language>/stage-2/<experiment>/<page>/<page>.mdf.txt
        """
        dataset_dir = Path(dataset_dir)
        pred_root = Path(pred_root)
        tasks: List[MdfEvalTask] = []
        selected_languages = set(languages) if languages else None

        for lang_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
            if selected_languages and lang_dir.name not in selected_languages:
                continue

            gold_root = lang_dir / "Stage 2 MDF file"
            stage2_root = pred_root / lang_dir.name / "stage-2"
            if not gold_root.is_dir() or not stage2_root.is_dir():
                continue

            available = sorted(
                p.name
                for p in stage2_root.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )
            exp_names = available if experiments is None else [e for e in experiments if e in available]
            for exp in exp_names:
                for gold_path in sorted(gold_root.glob("*/*.mdf.txt")):
                    stem = gold_path.parent.name
                    pred_path = stage2_root / exp / stem / f"{stem}.mdf.txt"
                    if not pred_path.is_file():
                        logger.debug("skip missing pred: %s", pred_path)
                        continue
                    tasks.append(
                        MdfEvalTask(
                            experiment=exp,
                            pred_path=pred_path,
                            gold_path=gold_path,
                            page_id=f"{lang_dir.name}/{stem}",
                        )
                    )
        return tasks

    @staticmethod
    def _quality_fields(prefix: str, quality: CharacterQualityMetrics) -> dict:
        return {
            f"{prefix}_TextEdit": round(quality.text_edit, 6),
            f"{prefix}_GCER": round(quality.gcer, 6),
            f"{prefix}_WER": round(quality.wer, 6),
        }

    @staticmethod
    def _language_quality_columns(results: List[MdfPageMetrics]) -> List[str]:
        buckets: set[str] = set()
        for page in results:
            buckets.update(page.language_quality.keys())
        cols: List[str] = []
        for bucket in sorted(buckets):
            safe = bucket.replace(":", "_")
            cols.extend([f"{safe}_GCER", f"{safe}_WER"])
        return cols

    @staticmethod
    def _language_quality_row(page: MdfPageMetrics, columns: List[str]) -> dict:
        row = {col: "" for col in columns}
        for bucket, quality in page.language_quality.items():
            safe = bucket.replace(":", "_")
            gcer_col = f"{safe}_GCER"
            wer_col = f"{safe}_WER"
            if gcer_col in row:
                row[gcer_col] = round(quality.gcer, 6)
            if wer_col in row:
                row[wer_col] = round(quality.wer, 6)
        return row

    @staticmethod
    def _parse_page_id(page_id: str) -> tuple[str, str]:
        language, sep, page = page_id.partition("/")
        if sep:
            return language, page
        return "", page_id

    @staticmethod
    def _language_script_metric_row(quality: CharacterQualityMetrics) -> dict:
        return {
            "gold_word_count": quality.total_words_gold,
            "gold_grapheme_count": quality.total_graphemes_gold,
            "GCER": round(quality.gcer, 6),
            "WER": round(quality.wer, 6),
            "TextEdit": round(quality.text_edit, 6),
            "total_graphemes_pred": quality.total_graphemes_pred,
            "total_grapheme_edits": quality.total_grapheme_edits,
            "total_word_edits": quality.total_word_edits,
        }

    @classmethod
    def _language_script_detailed_rows(cls, page: MdfPageMetrics) -> List[dict]:
        language, page_name = cls._parse_page_id(page.page_id)
        return [
            {
                "language": language,
                "page": page_name,
                "language_script": language_script,
                **cls._language_script_metric_row(quality),
            }
            for language_script, quality in sorted(page.language_script_quality.items())
        ]

    @staticmethod
    def metrics_to_dict(metrics: MdfPageMetrics) -> dict:
        """Serialize page metrics for JSON reports."""
        return {
            "page_id": metrics.page_id,
            "gold_path": metrics.gold_path,
            "pred_path": metrics.pred_path,
            "n_pred_records": metrics.n_pred_records,
            "Record_Accuracy": round(metrics.record_accuracy, 6),
            "MDF_Fields_F1": round(metrics.mdf_fields_f1, 6),
            "record_counts": {
                "tp": metrics.record.tp,
                "fp": metrics.record.fp,
                "fn": metrics.record.fn,
            },
            "marker_counts": {
                "tp": metrics.marker.tp,
                "fp": metrics.marker.fp,
                "fn": metrics.marker.fn,
            },
            "read_order": {
                "ReadOrderEdit": round(metrics.read_order.read_order_edit, 6),
                "edit_distance": metrics.read_order.edit_distance,
                "max_length": metrics.read_order.max_length,
            },
            "field_value_quality": MdfEvaluator._quality_fields(
                "FieldValue", metrics.field_value_quality
            ),
            "headword_quality": MdfEvaluator._quality_fields(
                "Headword", metrics.headword_quality
            ),
            "gloss_quality": MdfEvaluator._quality_fields("Gloss", metrics.gloss_quality),
            "language_quality": {
                bucket: MdfEvaluator._quality_fields(bucket.replace(":", "_"), q)
                for bucket, q in metrics.language_quality.items()
            },
            "language_script_quality": {
                bucket: MdfEvaluator._quality_fields(
                    f"LangScript_{bucket.replace(':', '_')}", q
                )
                for bucket, q in metrics.language_script_quality.items()
            },
            "marker_confusion": metrics.marker_confusion,
            "record_samples": [asdict(s) for s in metrics.record_samples],
            "marker_error_samples": [asdict(s) for s in metrics.marker_error_samples],
            "missing_record_samples": [asdict(s) for s in metrics.missing_record_samples],
            "extra_record_samples": [asdict(s) for s in metrics.extra_record_samples],
        }

    def generate_json_report(self, results: List[MdfPageMetrics], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "settings": {
                "record_threshold": self.record_threshold,
                "line_threshold": self.line_threshold,
                "marker_sub_list_path": self.marker_sub_list_path,
            },
            "pages": [self.metrics_to_dict(m) for m in results],
        }
        if len(results) > 1:
            payload["aggregate"] = self._aggregate_dict(results)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def generate_text_report(self, results: List[MdfPageMetrics], output_path: Path) -> str:
        lines = [
            "=" * 72,
            "STAGE 2 MDF EVALUATION REPORT",
            "=" * 72,
            f"record_threshold={self.record_threshold}  line_threshold={self.line_threshold}",
            "",
        ]
        for metrics in results:
            lines.extend(self._format_page(metrics))
            lines.append("")
        if len(results) > 1:
            agg = self._aggregate_dict(results)
            lines.extend(
                [
                    "=" * 72,
                    f"AGGREGATE ({len(results)} pages)",
                    "=" * 72,
                    f"  Record Accuracy: {agg['Record_Accuracy']:.4f}",
                    f"  MDF Fields F1:   {agg['MDF_Fields_F1']:.4f}",
                    f"  ReadOrderEdit:   {agg['read_order']['ReadOrderEdit']:.4f}",
                    f"  Field Value GCER:{agg['Field_Value_GCER']:.4f}",
                    f"  Headword GCER:   {agg['Headword_GCER']:.4f}",
                    f"  Gloss GCER:     {agg['Gloss_GCER']:.4f}",
                    "",
                ]
            )
        report = "\n".join(lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        return report

    def generate_summary_csv(self, results_by_exp: Dict[str, List[MdfPageMetrics]], output_path: Path) -> None:
        all_pages = [page for pages in results_by_exp.values() for page in pages]
        language_cols = self._language_quality_columns(all_pages)
        base_fields = [
            "experiment",
            "language",
            "page",
            "Record_Accuracy",
            "MDF_Fields_F1",
            "ReadOrderEdit",
            "Field_Value_GCER",
            "Field_Value_WER",
            "Headword_GCER",
            "Gloss_GCER",
            *language_cols,
        ]
        rows: List[dict] = []
        for exp, pages in results_by_exp.items():
            for page in pages:
                language, page_name = self._parse_page_id(page.page_id)
                row = {
                    "experiment": exp,
                    "language": language,
                    "page": page_name,
                    "Record_Accuracy": round(page.record_accuracy, 6),
                    "MDF_Fields_F1": round(page.mdf_fields_f1, 6),
                    "ReadOrderEdit": round(page.read_order.read_order_edit, 6),
                    "Field_Value_GCER": round(page.field_value_quality.gcer, 6),
                    "Field_Value_WER": round(page.field_value_quality.wer, 6),
                    "Headword_GCER": round(page.headword_quality.gcer, 6),
                    "Gloss_GCER": round(page.gloss_quality.gcer, 6),
                    **self._language_quality_row(page, language_cols),
                }
                rows.append(row)
            if len(pages) > 1:
                agg = self._aggregate_dict(pages)
                row = {
                    "experiment": exp,
                    "language": "__aggregate__",
                    "page": "__aggregate__",
                    "Record_Accuracy": agg["Record_Accuracy"],
                    "MDF_Fields_F1": agg["MDF_Fields_F1"],
                    "ReadOrderEdit": agg["read_order"]["ReadOrderEdit"],
                    "Field_Value_GCER": agg["Field_Value_GCER"],
                    "Field_Value_WER": agg["Field_Value_WER"],
                    "Headword_GCER": agg["Headword_GCER"],
                    "Gloss_GCER": agg["Gloss_GCER"],
                }
                for col in language_cols:
                    row[col] = agg.get(col, "")
                rows.append(row)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=base_fields)
            writer.writeheader()
            writer.writerows(rows)

    def generate_per_language_script_detailed_csv(
        self,
        results_by_exp: Dict[str, List[MdfPageMetrics]],
        output_path: Path,
    ) -> None:
        fields = [
            "experiment",
            "language",
            "page",
            "language_script",
            "gold_word_count",
            "gold_grapheme_count",
            "GCER",
            "WER",
            "TextEdit",
            "total_graphemes_pred",
            "total_grapheme_edits",
            "total_word_edits",
        ]
        rows = [
            {"experiment": exp, **row}
            for exp, pages in results_by_exp.items()
            for page in pages
            for row in self._language_script_detailed_rows(page)
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def generate_per_language_script_summary_csv(
        self,
        results_by_exp: Dict[str, List[MdfPageMetrics]],
        output_path: Path,
    ) -> None:
        fields = [
            "experiment",
            "language",
            "language_script",
            "gold_word_count",
            "gold_grapheme_count",
            "GCER",
            "WER",
            "TextEdit",
        ]
        rows: List[dict] = []
        for exp, pages in results_by_exp.items():
            grouped: DefaultDict[tuple[str, str], List[CharacterQualityMetrics]] = defaultdict(list)
            for page in pages:
                language, _page_name = self._parse_page_id(page.page_id)
                for language_script, quality in page.language_script_quality.items():
                    grouped[(language, language_script)].append(quality)
            for (language, language_script), qualities in sorted(grouped.items()):
                merged = merge_character_quality(qualities)
                metric_row = self._language_script_metric_row(merged)
                rows.append(
                    {
                        "experiment": exp,
                        "language": language,
                        "language_script": language_script,
                        "gold_word_count": metric_row["gold_word_count"],
                        "gold_grapheme_count": metric_row["gold_grapheme_count"],
                        "GCER": metric_row["GCER"],
                        "WER": metric_row["WER"],
                        "TextEdit": metric_row["TextEdit"],
                    }
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _format_page(metrics: MdfPageMetrics) -> List[str]:
        lines = [
            f"--- {metrics.page_id} ---",
            f"  Records: matched={metrics.record.tp} missing={metrics.record.fn} "
            f"extra={metrics.record.fp} (pred={metrics.n_pred_records})",
            f"  Record Accuracy: {metrics.record_accuracy:.4f}",
            f"  MDF Fields F1:   {metrics.mdf_fields_f1:.4f}",
            f"  ReadOrderEdit:   {metrics.read_order.read_order_edit:.4f}",
            f"  Field Value GCER:{metrics.field_value_quality.gcer:.4f}",
            f"  Headword GCER:   {metrics.headword_quality.gcer:.4f}",
            f"  Gloss GCER:     {metrics.gloss_quality.gcer:.4f}",
        ]
        for bucket, quality in sorted(metrics.language_quality.items()):
            lines.append(f"  {bucket} GCER:       {quality.gcer:.4f}")
        for bucket, quality in sorted(metrics.language_script_quality.items()):
            lines.append(f"  lang-script {bucket} GCER: {quality.gcer:.4f}")
        return lines

    @staticmethod
    def _aggregate_dict(results: List[MdfPageMetrics]) -> dict:
        record_tp = sum(m.record.tp for m in results)
        record_fp = sum(m.record.fp for m in results)
        record_fn = sum(m.record.fn for m in results)
        marker_tp = sum(m.marker.tp for m in results)
        marker_fp = sum(m.marker.fp for m in results)
        marker_fn = sum(m.marker.fn for m in results)
        ro_edit = sum(m.read_order.read_order_edit for m in results) / len(results)

        record_accuracy = (
            record_tp / (record_tp + record_fn) if (record_tp + record_fn) else 0.0
        )
        marker_p = marker_tp / (marker_tp + marker_fp) if (marker_tp + marker_fp) else 0.0
        marker_r = marker_tp / (marker_tp + marker_fn) if (marker_tp + marker_fn) else 0.0
        mdf_fields_f1 = (
            2 * marker_p * marker_r / (marker_p + marker_r) if (marker_p + marker_r) else 0.0
        )

        field_q = merge_character_quality([m.field_value_quality for m in results])
        headword_q = merge_character_quality([m.headword_quality for m in results])
        gloss_q = merge_character_quality([m.gloss_quality for m in results])

        agg: dict = {
            "Record_Accuracy": round(record_accuracy, 6),
            "MDF_Fields_F1": round(mdf_fields_f1, 6),
            "record_counts": {"tp": record_tp, "fp": record_fp, "fn": record_fn},
            "marker_counts": {"tp": marker_tp, "fp": marker_fp, "fn": marker_fn},
            "read_order": {"ReadOrderEdit": round(ro_edit, 6)},
            "Field_Value_GCER": round(field_q.gcer, 6),
            "Field_Value_WER": round(field_q.wer, 6),
            "Headword_GCER": round(headword_q.gcer, 6),
            "Gloss_GCER": round(gloss_q.gcer, 6),
        }

        lang_buckets: set[str] = set()
        for page in results:
            lang_buckets.update(page.language_quality.keys())
        for bucket in sorted(lang_buckets):
            safe = bucket.replace(":", "_")
            merged = merge_character_quality(
                [page.language_quality[bucket] for page in results if bucket in page.language_quality]
            )
            agg[f"{safe}_GCER"] = round(merged.gcer, 6)
            agg[f"{safe}_WER"] = round(merged.wer, 6)

        lang_script_buckets: set[str] = set()
        for page in results:
            lang_script_buckets.update(page.language_script_quality.keys())
        for bucket in sorted(lang_script_buckets):
            safe = bucket.replace(":", "_")
            merged = merge_character_quality(
                [
                    page.language_script_quality[bucket]
                    for page in results
                    if bucket in page.language_script_quality
                ]
            )
            agg[f"LangScript_{safe}_GCER"] = round(merged.gcer, 6)
            agg[f"LangScript_{safe}_WER"] = round(merged.wer, 6)

        return agg
