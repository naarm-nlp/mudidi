"""
CLI: extract dictionary entries from a directory of page images.
Usage: python -m mudidi.cli.extract [options]
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mudidi.ocr.mathpix import MathpixBackend
from mudidi.ocr.vlm.prompts import find_ocr_hint_file
from mudidi.schemas.ocr_result import OCRPageResult
from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.evaluation.stage2.mdf_lexical_repair import (
    repair_mdf_text,
    normalize_stage1_text_for_repair,
    write_repair_audit,
)
from mudidi.evaluation.stage2.mdf_evaluator import MdfEvaluator
from mudidi.evaluation.stage1.flat_evaluator import FlatStage1Evaluator
from mudidi.paths import (
    MDF_PARSING_GUIDE_FILENAME,
    MDF_PARSING_GUIDE_USAGE_FILENAME,
)
from mudidi.extraction.sample_entry import (
    configure_benchmark_entry_args,
    report_entry_input_failures,
    stage1_context_inputs_apply,
    validate_configured_sample_entry,
)
from mudidi.extraction.vlm_ocr import run_vlm_ocr_batch, run_vlm_ocr_entry
from mudidi.ocr.vlm.registry import get_vlm_spec, list_vlm_keys
from mudidi.ocr.vlm.runner import create_vlm_runner
from mudidi.utils.dictionary_languages import (
    config_to_yaml_dict,
    load_pass1_dictionary_languages,
)
from mudidi.utils.stage2_direct_mdf_io import save_direct_mdf_outputs
from mudidi.utils.stage1_input import (
    stage1_transcript_for_stage2,
    stage1_experiment_dir,
    stage1_flat_path,
    stage1_gold_dir,
    stage1_gold_flat_path,
    stage1_gold_tsv_path,
    stage1_tsv_path,
    stage1_transcript_kind,
)
from mudidi.utils.stage2_page_selection import select_one_stage2_page, sort_snippet_pages
from mudidi.config.output_paths import output_layout_from_config
from mudidi.config.run_config import (
    EXTRACT_STAGE_CHOICES,
    RunConfig,
    page_run_phases,
    runs_stage1,
    runs_stage2_any,
    runs_stage2_pass1,
    runs_stage2_pass2,
)
from mudidi.utils.page_context import build_page_context
from mudidi.utils.parse_rules_pages import (
    normalize_parse_rules_page_stems,
    select_parse_rules_sample_images,
)
from mudidi.utils.pdf_render import render_pdf_pages, run_needs_pdf_rasterization
from mudidi.cli.model_args import attach_stage_models, register_model_arguments
from mudidi.utils.pdf_split import extract_pdf_pages, parse_page_spec
from mudidi.utils.stage1_input import read_stage1_transcript_text
from mudidi.llm.client import (
    configure_page_concurrency,
    is_retryable_transient_error,
    page_concurrency_slot,
    wait_for_provider_backoff,
)
from mudidi.llm.prompt_store import configure_prompts, default_prompts_path

_DEFAULT_METADATA_CSV = (
    Path(__file__).resolve().parents[3]
    / "assets/dictionaries/full dictionaries/dictionary_metadata.csv"
)


def _gold_mdf_path_for_entry(
    output_dir: Path,
    stem: str,
    explicit: Optional[str],
    entry_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve gold MDF path for comparison."""
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    if entry_dir is not None:
        dataset_candidate = entry_dir / "Stage 2 MDF file" / stem / f"{stem}.mdf.txt"
        if dataset_candidate.is_file():
            return dataset_candidate
    for candidate in (
        output_dir / "stage-2-gold" / stem / f"{stem}_mdf",
        output_dir / "stage-2-gold" / stem / stem,
    ):
        if candidate.is_file():
            return candidate
    return None


def _attempt_number(path: Path) -> int:
    match = re.search(r"attempt_(\d+)_output", path.name)
    return int(match.group(1)) if match else -1


def _load_agentic_verifier_summary(agentic_dir: Path, attempt: int) -> dict[str, str]:
    verifier_path = agentic_dir / f"attempt_{attempt}_verifier.json"
    if not verifier_path.is_file():
        return {"verifier_decision": "", "verifier_confidence": "", "verifier_issues": ""}
    data = json.loads(verifier_path.read_text(encoding="utf-8"))
    issues = data.get("issues") or []
    return {
        "verifier_decision": str(data.get("decision", "")),
        "verifier_confidence": str(data.get("confidence", "")),
        "verifier_issues": ";".join(str(issue.get("type", "")) for issue in issues),
    }


def _write_stage2_agentic_attempt_metrics(
    *,
    agentic_dir: Path,
    gold_path: Optional[Path],
    page_id: str,
    dictionary_languages_path: Optional[Path],
) -> Optional[Path]:
    """Evaluate each Stage 2 agentic attempt against gold when available."""
    if gold_path is None or not gold_path.is_file() or not agentic_dir.is_dir():
        return None

    attempt_paths = sorted(
        agentic_dir.glob("attempt_*_output.mdf.txt"),
        key=_attempt_number,
    )
    if not attempt_paths:
        return None

    evaluator = MdfEvaluator(dictionary_languages_path=dictionary_languages_path)
    rows: list[dict[str, object]] = []
    json_pages: list[dict[str, object]] = []
    for attempt_path in attempt_paths:
        attempt = _attempt_number(attempt_path)
        metrics = evaluator.evaluate(attempt_path, gold_path, page_id=page_id)
        verifier = _load_agentic_verifier_summary(agentic_dir, attempt)
        row: dict[str, object] = {
            "attempt": attempt,
            "output_path": str(attempt_path),
            **verifier,
            "Record_Accuracy": round(metrics.record_accuracy, 6),
            "MDF_Fields_F1": round(metrics.mdf_fields_f1, 6),
            "ReadOrderEdit": round(metrics.read_order.read_order_edit, 6),
            "Field_Value_GCER": round(metrics.field_value_quality.gcer, 6),
            "Field_Value_WER": round(metrics.field_value_quality.wer, 6),
            "Headword_GCER": round(metrics.headword_quality.gcer, 6),
            "Gloss_GCER": round(metrics.gloss_quality.gcer, 6),
        }
        rows.append(row)
        json_pages.append(
            {
                "attempt": attempt,
                "verifier": verifier,
                "metrics": MdfEvaluator.metrics_to_dict(metrics),
            }
        )

    csv_path = agentic_dir / "attempt_metrics.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = agentic_dir / "attempt_metrics.json"
    json_path.write_text(
        json.dumps(
            {
                "page_id": page_id,
                "gold_path": str(gold_path),
                "attempts": json_pages,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return csv_path


def _stage1_gold_flat_path_for_entry(
    entry_dir: Optional[Path],
    stem: str,
) -> Optional[Path]:
    if entry_dir is None:
        return None
    candidate = (
        entry_dir
        / "Stage 1 Gold OCR"
        / stem
        / f"{stem}_stage1_GOLD_flat.txt"
    )
    return candidate if candidate.is_file() else None


def _write_stage1_agentic_attempt_metrics(
    *,
    agentic_dir: Path,
    gold_path: Optional[Path],
    page_id: str,
) -> Optional[Path]:
    """Evaluate each Stage 1 agentic attempt against gold when available."""
    if gold_path is None or not gold_path.is_file() or not agentic_dir.is_dir():
        return None

    attempt_paths = sorted(
        (
            p
            for p in agentic_dir.glob("attempt_*_output*")
            if p.is_file() and not p.name.endswith(".json")
        ),
        key=_attempt_number,
    )
    if not attempt_paths:
        return None

    evaluator = FlatStage1Evaluator(per_language_script=True)
    rows: list[dict[str, object]] = []
    json_pages: list[dict[str, object]] = []
    for attempt_path in attempt_paths:
        attempt = _attempt_number(attempt_path)
        metrics = evaluator.evaluate(attempt_path, gold_path, page_id=page_id)
        verifier = _load_agentic_verifier_summary(agentic_dir, attempt)
        row: dict[str, object] = {
            "attempt": attempt,
            "output_path": str(attempt_path),
            **verifier,
            "TextEdit": round(metrics.character_quality.text_edit, 6),
            "GCER": round(metrics.character_quality.gcer, 6),
            "WER": round(metrics.character_quality.wer, 6),
            "typography_f1": round(metrics.markup_quality.typography.f1, 6),
            "ReadOrderEdit": round(metrics.read_order.read_order_edit, 6),
        }
        rows.append(row)
        json_pages.append(
            {
                "attempt": attempt,
                "verifier": verifier,
                "metrics": {
                    "page_id": metrics.page_id,
                    "TextEdit": row["TextEdit"],
                    "GCER": row["GCER"],
                    "WER": row["WER"],
                    "typography_f1": row["typography_f1"],
                    "ReadOrderEdit": row["ReadOrderEdit"],
                },
            }
        )

    csv_path = agentic_dir / "attempt_metrics.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = agentic_dir / "attempt_metrics.json"
    json_path.write_text(
        json.dumps(
            {
                "page_id": page_id,
                "gold_path": str(gold_path),
                "attempts": json_pages,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return csv_path


def _entry_dir_for_run(
    args: argparse.Namespace,
    output_dir: Path,
    input_dir: Path,
) -> Optional[Path]:
    """Infer sample entry directory for dictionary_languages.yaml loading."""
    entry_dir = getattr(args, "entry_dir", None)
    if entry_dir:
        return Path(entry_dir)
    if output_dir.name == "outputs" and (
        output_dir.parent / "dictionary_languages.yaml"
    ).is_file():
        return output_dir.parent
    if (input_dir.parent / "dictionary_languages.yaml").is_file():
        return input_dir.parent
    return None


_STRATEGIES = {
    "two_stage": TwoStageLLMExtraction,
}

_STRATEGY_CHOICES = list(_STRATEGIES.keys()) + ["vlm_ocr", "mathpix_ocr"]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_PDF_EXTS = {".pdf"}
_TEXT_EXTS = {".txt", ".md", ".docx"}
def _render_pdf_pages(pdf_path: Path, cache_dir: Path, dpi: int = 200) -> List[Path]:
    """Render each page of ``pdf_path`` to a PNG under ``cache_dir``."""
    return render_pdf_pages(pdf_path, cache_dir, dpi=dpi)


def _materialize_page_inputs(
    input_dir: Path, cache_dir: Path, *, render_pdfs: bool
) -> List[Path]:
    """Return sorted page input paths from ``input_dir``.

    When ``render_pdfs`` is True, PDFs are rasterized to PNG in ``cache_dir``.
    Required for VLMs that only accept raster images
    (e.g. OpenRouter/Parasail). When False, PDFs pass through as
    ``application/pdf`` inline data (Gemini-only path).
    """
    collected: List[Path] = []
    for f in sorted(input_dir.iterdir()):
        if f.name.startswith((".", "~")):
            continue
        suffix = f.suffix.lower()
        if suffix in _IMAGE_EXTS:
            collected.append(f)
        elif suffix in _PDF_EXTS:
            if render_pdfs:
                collected.extend(_render_pdf_pages(f, cache_dir))
            else:
                collected.append(f)
    return sort_snippet_pages(collected)


def _materialize_snippet_inputs(
    pages_path: Path,
    cache_dir: Path,
    *,
    dict_pages_spec: Optional[str],
    render_pdfs: bool,
    overwrite: bool,
) -> List[Path]:
    """Resolve dictionary page inputs from a snippets directory or source PDF."""
    if pages_path.is_dir():
        return _materialize_page_inputs(pages_path, cache_dir, render_pdfs=render_pdfs)

    if pages_path.suffix.lower() not in _PDF_EXTS:
        raise ValueError(f"--pages must be a directory or PDF file: {pages_path}")

    if not dict_pages_spec:
        raise ValueError(
            "--dict-pages is required when --pages is a PDF (e.g. '1-10' or '1,3,5')"
        )

    try:
        page_numbers = parse_page_spec(dict_pages_spec)
    except ValueError as exc:
        raise ValueError(f"Invalid --dict-pages: {exc}") from exc
    if not page_numbers:
        raise ValueError("--dict-pages must list at least one page")

    split_dir = cache_dir / "split"
    pdfs = extract_pdf_pages(
        pages_path,
        page_numbers,
        split_dir,
        overwrite=overwrite,
    )

    if render_pdfs:
        collected: List[Path] = []
        for pdf in pdfs:
            collected.extend(_render_pdf_pages(pdf, cache_dir))
        return sort_snippet_pages(collected)

    return sort_snippet_pages(pdfs)


def _collect_intro_from_pdf(
    source_pdf: Path,
    intro_pages_spec: str,
    pdf_cache_dir: Path,
    *,
    render_pdfs: bool,
    overwrite: bool = False,
) -> Tuple[str, List[str]]:
    """Extract introduction pages from ``source_pdf`` and return vision inputs."""
    try:
        page_numbers = parse_page_spec(intro_pages_spec)
    except ValueError as exc:
        raise ValueError(f"Invalid --intro-pages: {exc}") from exc
    if not page_numbers:
        raise ValueError("--intro-pages must list at least one page")

    split_dir = pdf_cache_dir / "split"
    pdfs = extract_pdf_pages(
        source_pdf,
        page_numbers,
        split_dir,
        overwrite=overwrite,
    )
    image_paths: List[str] = []
    for pdf in pdfs:
        if render_pdfs:
            image_paths.extend(str(p) for p in _render_pdf_pages(pdf, pdf_cache_dir))
        else:
            image_paths.append(str(pdf))
    return "", image_paths


def _collect_intro(
    intro_path: Path,
    pdf_cache_dir: Path,
    *,
    render_pdfs: bool,
) -> Tuple[str, List[str]]:
    """
    Load intro context from a file or directory. Supports images, PDFs,
    and text files.

    When ``render_pdfs`` is True, PDF intro pages are rendered to PNG via
    ``pdf_cache_dir``; otherwise they are passed through as PDF paths for
    the LLM to ingest directly.
    """

    def _as_vision_inputs(f: Path) -> List[str]:
        if render_pdfs:
            return [str(p) for p in _render_pdf_pages(f, pdf_cache_dir)]
        return [str(f)]

    if intro_path.is_file():
        suffix = intro_path.suffix.lower()
        if suffix in _IMAGE_EXTS:
            return "", [str(intro_path)]
        if suffix in _PDF_EXTS:
            return "", _as_vision_inputs(intro_path)
        return _read_text_file(intro_path), []

    text_parts, image_paths = [], []
    for f in sorted(intro_path.iterdir()):
        if f.name.startswith((".", "~")):
            continue
        suffix = f.suffix.lower()
        if suffix in _IMAGE_EXTS:
            image_paths.append(str(f))
        elif suffix in _PDF_EXTS:
            image_paths.extend(_as_vision_inputs(f))
        elif suffix in _TEXT_EXTS:
            text_parts.append(_read_text_file(f))

    return "\n\n".join(text_parts), image_paths


def _read_text_file(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        from mudidi.utils.io import read_docx_text

        return read_docx_text(str(path))
    return path.read_text(encoding="utf-8")


def _find_ocr_file(ocr_dir: Path, stem: str) -> Optional[Path]:
    """Find an OCR hint file in ocr_dir whose stem matches the image stem."""
    return find_ocr_hint_file(ocr_dir, stem)


def _build_ocr_result(image_path: str, ocr_file: Optional[Path]) -> OCRPageResult:
    """Create an OCRPageResult, optionally populated from an OCR hint file."""
    if ocr_file:
        return MathpixBackend().run(image_path, ocr_file=str(ocr_file))
    return OCRPageResult(source_image=image_path, backend="none", blocks=[])


_IMAGE_ALPHABET_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _git_short_sha() -> Optional[str]:
    """Best-effort short git SHA of the working tree; None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        sha = out.stdout.strip()
        return sha or None
    except (OSError, subprocess.SubprocessError):
        return None


def _alphabet_manifest_entry(alphabet_path: Optional[str]) -> Dict[str, Any]:
    """Describe the alphabet input for a run manifest.

    Text-form alphabets (.txt/.md/.docx/etc.) are embedded inline so the
    exact content used by the run is preserved even if the source file is
    later edited or deleted. Image-form alphabets are referenced by path
    only (you swap them, you don't edit them).
    """
    if not alphabet_path:
        return {"used": False, "path": None, "kind": None, "text": None}
    p = Path(alphabet_path)
    if p.suffix.lower() in _IMAGE_ALPHABET_EXTS:
        return {"used": True, "path": str(p), "kind": "image", "text": None}
    try:
        text = _read_text_file(p)
    except OSError as exc:
        return {
            "used": True, "path": str(p), "kind": "text",
            "text": None, "read_error": str(exc),
        }
    return {"used": True, "path": str(p), "kind": "text", "text": text}


def _guides_manifest_entry(
    path: Optional[str], loaded_text: str
) -> Dict[str, Any]:
    """Describe an inline guides file (stage-1 or stage-2 guides)."""
    if not path:
        return {"used": False, "path": None, "text": None}
    return {"used": True, "path": path, "text": loaded_text or ""}


def _per_page_inputs_stage1(
    images: List[Path], ocr_dir: Optional[Path]
) -> List[Dict[str, Any]]:
    """Resolve the per-page input bundle for stage 1 (snippet + ocr-hint)."""
    rows: List[Dict[str, Any]] = []
    for image_file in images:
        stem = image_file.stem
        ocr_file = _find_ocr_file(ocr_dir, stem) if ocr_dir else None
        rows.append({
            "stem": stem,
            "snippet_path": str(image_file),
            "ocr_hint_file": str(ocr_file) if ocr_file else None,
        })
    return rows


def _per_page_inputs_stage2(
    images: List[Path],
    output_dir: Path,
    preference: str = "auto",
    *,
    source: str = "gold",
    experiment_name: str = "default",
    stage1_output_subdir: str = "stage-1",
) -> List[Dict[str, Any]]:
    """Collect per-page stage-1 transcript paths that stage 2 consumes."""
    rows: List[Dict[str, Any]] = []
    gold_root = stage1_gold_dir(output_dir)
    for image_file in images:
        stem = image_file.stem
        gold_page_dir = gold_root / stem
        transcript_path = stage1_transcript_for_stage2(
            output_dir,
            stem,
            preference,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            experiment_name=experiment_name,
            stage1_output_subdir=stage1_output_subdir,
        )
        row: Dict[str, Any] = {
            "stem": stem,
            "stage1_gold_tsv_path": str(stage1_gold_tsv_path(gold_page_dir, stem)),
            "stage1_gold_flat_path": str(stage1_gold_flat_path(gold_page_dir, stem)),
            "stage1_transcript_path": str(transcript_path) if transcript_path else None,
        }
        if source == "predictions":
            pred_page_dir = (
                stage1_experiment_dir(
                    output_dir, experiment_name, subdir=stage1_output_subdir
                )
                / stem
            )
            row["stage1_prediction_dir"] = str(pred_page_dir)
            row["stage1_prediction_flat_path"] = str(
                stage1_flat_path(pred_page_dir, stem)
            )
        if transcript_path is not None:
            row["stage1_transcript_kind"] = stage1_transcript_kind(transcript_path)
        rows.append(row)
    return rows


def _write_run_config(
    target_dir: Path,
    manifest: Dict[str, Any],
    *,
    force: bool,
    resolved_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a run_config.json into ``target_dir`` honoring the resume guard.

    On resume (file exists, ``force`` False) the existing manifest wins so
    the on-disk config never drifts from what produced the predictions
    sitting in the slot. With ``force=True`` (i.e. ``--overwrite``) the
    manifest is rewritten to match the fresh invocation.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "run_config.json"
    if not force and path.exists():
        print(
            f"  Keeping existing {path} (resume; pass --overwrite to refresh it)."
        )
        return
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if resolved_config is not None:
        resolved_path = target_dir / "resolved_config.json"
        if force or not resolved_path.exists():
            resolved_path.write_text(
                json.dumps(resolved_config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


def _build_stage1_manifest(
    args,
    snippets_dir: Path,
    images: List[Path],
    ocr_dir: Optional[Path],
) -> Dict[str, Any]:
    """Assemble the stage-1 manifest dict (no I/O)."""
    from mudidi.evaluation.stage1.flatten import FLAT_SPEC_VERSION

    return {
        "stage": "1",
        "experiment_name": args.experiment_name,
        "stage1_output_subdir": getattr(args, "stage1_output_subdir", "stage-1"),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": args.strategy,
        "stage1_mode": getattr(args, "stage1_mode", "column"),
        "stage1_typography": bool(getattr(args, "stage1_typography", False)),
        "flat_spec_version": FLAT_SPEC_VERSION,
        "git_sha": _git_short_sha(),
        "model": args.stage_models.stage_1,
        "reasoning_effort": args.stage1_reasoning_effort,
        "temperature": args.temperature,
        "batch_size": getattr(args, "batch_size", 1),
        "render_pdfs": run_needs_pdf_rasterization(
            args.stage_models.stage_1,
            args.stage_models.stage_2_pass_1,
            args.stage_models.stage_2_pass_2,
        ),
        "alphabet": _alphabet_manifest_entry(args.alphabet),
        "ocr_hint": {
            "used": bool(ocr_dir),
            "dir": str(ocr_dir) if ocr_dir else None,
        },
        "stage1_guides": _guides_manifest_entry(
            getattr(args, "stage1_guides_path", None),
            getattr(args, "stage1_guides_text", ""),
        ),
        "inputs": {
            "snippets_dir": str(snippets_dir),
            "page_count": len(images),
        },
        "per_page": _per_page_inputs_stage1(images, ocr_dir),
    }


def _build_stage2_manifest(
    args,
    snippets_dir: Path,
    images: List[Path],
    output_dir: Path,
    intro_image_paths: List[str],
    dictionary_languages: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the stage-2 manifest dict (no I/O)."""
    gold_dir = stage1_gold_dir(output_dir)
    manifest = {
        "stage": "2",
        "experiment_name": args.stage2_experiment_name,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": args.strategy,
        "git_sha": _git_short_sha(),
        "model": args.stage_models.stage_2_pass_2,
        "stage_2_pass_1_model": args.stage_models.stage_2_pass_1,
        "stage_2_pass_2_model": args.stage_models.stage_2_pass_2,
        "reasoning_effort": (
            getattr(args, "stage2_pass2_reasoning_effort", None)
            or args.stage2_reasoning_effort
        ),
        "stage_2_pass_1_reasoning_effort": (
            getattr(args, "stage2_pass1_reasoning_effort", None)
            or args.stage2_reasoning_effort
        ),
        "stage_2_pass_2_reasoning_effort": (
            getattr(args, "stage2_pass2_reasoning_effort", None)
            or args.stage2_reasoning_effort
        ),
        "temperature": args.temperature,
        "batch_size": getattr(args, "batch_size", 1),
        "stage2_output_format": "mdf",
        "prompts_file": str(
            getattr(args, "prompts_file", None) or default_prompts_path()
        ),
        "stage1_input": getattr(args, "stage1_input", "auto"),
        "parse_rules": str(
            output_dir
            / "stage-2"
            / args.stage2_experiment_name
            / MDF_PARSING_GUIDE_FILENAME
        ),
        "parse_rules_source": (
            "gold"
            if getattr(args, "parse_rules_gold", False)
            or getattr(args, "field_cheatsheet_gold", False)
            else "discover"
        ),
        "toolbox_pdf": str(args.toolbox_pdf) if getattr(args, "toolbox_pdf", None) else None,
        "prompt_cache": getattr(args, "prompt_cache", "auto"),
        "media_reference": getattr(args, "media_reference", "auto"),
        "prompt_cache_key": getattr(args, "prompt_cache_key", None),
        "stage1_source": {
            "kind": getattr(args, "stage1_source", "gold"),
            "stage1_input_preference": getattr(args, "stage1_input", "auto"),
            "experiment_name": args.experiment_name,
            "stage1_gold_dir": str(gold_dir),
        },
        # Intro is path-only regardless of format (image / pdf / txt / md /
        # docx) per the user's explicit choice — embedding intro PDFs/images
        # would balloon the manifest, and intro text rarely changes mid-sweep.
        "intro": {
            "used": bool(args.intro),
            "source_path": args.intro,
            "intro_image_or_pdf_paths": list(intro_image_paths),
        },
        "stage2_guides": _guides_manifest_entry(
            getattr(args, "stage2_guides_path", None),
            getattr(args, "stage2_guides_text", ""),
        ),
        "inputs": {
            "snippets_dir": str(snippets_dir),
            "page_count": len(images),
        },
        "per_page": _per_page_inputs_stage2(
            images,
            output_dir,
            getattr(args, "stage1_input", "auto"),
            source=getattr(args, "stage1_source", "gold"),
            experiment_name=args.experiment_name,
            stage1_output_subdir=getattr(args, "stage1_output_subdir", "stage-1"),
        ),
    }
    if dictionary_languages is not None:
        manifest["dictionary_languages"] = dictionary_languages
    return manifest


def _build_strategy(
    args,
    intro_text: str,
    intro_image_paths: List[str],
    dictionary_languages=None,
    dictionary_profile=None,
    stage2_experiment_dir: Optional[Path] = None,
    parse_rules_samples: Optional[List[tuple[str, str, str]]] = None,
):
    """Instantiate the extraction strategy."""
    if args.strategy == "two_stage":
        return TwoStageLLMExtraction(
            transcribe_model=args.stage_models.stage_1,
            stage2_pass1_model=args.stage_models.stage_2_pass_1,
            stage2_pass2_model=args.stage_models.stage_2_pass_2,
            alphabet_path=args.alphabet or None,
            intro_text=intro_text,
            intro_image_paths=intro_image_paths,
            stage1_reasoning_effort=getattr(args, "stage1_reasoning_effort", "low"),
            stage2_reasoning_effort=getattr(args, "stage2_reasoning_effort", "low"),
            stage2_pass1_reasoning_effort=getattr(
                args, "stage2_pass1_reasoning_effort", None
            ),
            stage2_pass2_reasoning_effort=getattr(
                args, "stage2_pass2_reasoning_effort", None
            ),
            temperature=getattr(args, "temperature", 0.1),
            stage1_guides=getattr(args, "stage1_guides_text", ""),
            stage2_guides=getattr(args, "stage2_guides_text", ""),
            stage1_mode=getattr(args, "stage1_mode", "column"),
            dictionary_languages=dictionary_languages,
            dictionary_profile=dictionary_profile,
            entry_dir=str(getattr(args, "entry_dir", "") or "") or None,
            stage2_experiment_dir=(
                str(stage2_experiment_dir) if stage2_experiment_dir else None
            ),
            overwrite=bool(getattr(args, "overwrite", False)),
            stage2_toolbox_pdf=(
                str(args.toolbox_pdf) if getattr(args, "toolbox_pdf", None) else None
            ),
            parse_rules_gold=bool(
                getattr(args, "parse_rules_gold", False)
                or getattr(args, "field_cheatsheet_gold", False)
            ),
            parse_rules_file=(
                str(args.parse_rules_file)
                if getattr(args, "parse_rules_file", None)
                else None
            ),
            approved_parse_rules=getattr(args, "approved_parse_rules", None),
            parse_rules_samples=parse_rules_samples,
            prompt_mode=getattr(args, "prompt_mode", "benchmark"),
            prompt_cache=getattr(args, "prompt_cache", "auto"),
            media_reference=getattr(args, "media_reference", "auto"),
            prompt_cache_key=getattr(args, "prompt_cache_key", None),
            stage1_typography=bool(getattr(args, "stage1_typography", False)),
            stage1_agentic=bool(getattr(args, "stage1_agentic", False)),
            stage2_agentic=bool(getattr(args, "stage2_agentic", False)),
            agentic_max_iterations=int(
                getattr(args, "agentic_max_iterations", 2) or 0
            ),
            agentic_evaluator_model=getattr(args, "agentic_evaluator_model", None),
            agentic_rewriter_model=getattr(args, "agentic_rewriter_model", None),
            agentic_reasoning_effort=getattr(args, "agentic_reasoning_effort", "low"),
            agentic_evaluator_reasoning_effort=getattr(
                args,
                "agentic_evaluator_reasoning_effort",
                None,
            ),
            agentic_rewriter_reasoning_effort=getattr(
                args,
                "agentic_rewriter_reasoning_effort",
                None,
            ),
            agentic_min_retry_confidence=float(
                getattr(args, "agentic_min_retry_confidence", 0.55)
            ),
            agentic_require_concrete_retry_issue=not getattr(
                args,
                "no_agentic_concrete_retry_gate",
                False,
            ),
            agentic_prefer_verifier_patches=not getattr(
                args,
                "no_agentic_verifier_patches",
                False,
            ),
        )
    raise ValueError(f"Unknown strategy: {args.strategy}")


def _stage1_transcript_path_for_stem(
    stage1_dir: Path,
    stem: str,
    *,
    stage1_mode: str,
) -> Path:
    page_dir = stage1_dir / stem
    if stage1_mode == "flat":
        return stage1_flat_path(page_dir, stem)
    return stage1_tsv_path(page_dir, stem)


def _prepare_parse_rules_samples(
    args,
    images: List[Path],
    stage1_dir: Path,
    output_dir: Path,
    *,
    layout,
    strategy: TwoStageLLMExtraction,
    ocr_dir: Optional[Path],
) -> List[tuple[str, str, str]]:
    """Ensure Stage 1 transcripts exist for Pass 1 sample page(s)."""
    stems = normalize_parse_rules_page_stems(getattr(args, "parse_rules_pages", None))
    sample_images = select_parse_rules_sample_images(images, stems)
    samples: List[tuple[str, str, str]] = []
    stage1_mode = getattr(args, "stage1_mode", "column")

    for page_index, image_file in enumerate(sample_images):
        stem = image_file.stem
        stage1_out = _stage1_transcript_path_for_stem(
            stage1_dir, stem, stage1_mode=stage1_mode
        )

        if args.stage == "all" and (args.overwrite or not stage1_out.is_file()):
            print(f"Pass 1 prep: Stage 1 transcription for sample page {stem} …")
            stage1_page_dir = stage1_dir / stem
            stage1_page_dir.mkdir(parents=True, exist_ok=True)
            ocr_file = _find_ocr_file(ocr_dir, stem) if ocr_dir else None
            ocr_result = _build_ocr_result(str(image_file), ocr_file)
            strategy.extract(
                ocr_result,
                str(image_file),
                page_number=page_index,
                stage1_output_path=str(stage1_out),
                run_stage="1",
            )

        transcript_path = stage1_transcript_for_stage2(
            output_dir,
            stem,
            getattr(args, "stage1_input", "auto"),
            source=getattr(args, "stage1_source", "gold"),
            experiment_name=args.experiment_name,
            stage1_output_subdir=args.stage1_output_subdir,
            inference_layout=layout.inference,
        )
        if transcript_path is None or not transcript_path.is_file():
            raise FileNotFoundError(
                f"Pass 1 sample page {stem!r} has no Stage 1 transcript. "
                f"Run Stage 1 for that page first or use --stage all."
            )
        samples.append(
            (
                stem,
                read_stage1_transcript_text(transcript_path),
                str(image_file),
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(*, resolved_args: argparse.Namespace | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch-extract dictionary entries from a directory of page images.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Two-stage on a directory of pages
  python -m mudidi.cli.extract \\
    --strategy two_stage \\
    --model gemini/gemini-3-flash-preview \\
    --input-image assets/pages/ \\
    --ocr-text assets/mathpix/ \\
    --alphabet assets/alphabet.txt \\
    --intro assets/introduction/ \\
    --output outputs/run1/ \\
    --limit 5

  # Resume: already-processed pages are skipped automatically.
  # Re-run the same command and only missing pages will be processed.
        """,
    )

    # Input — single-entry mode
    parser.add_argument(
        "--input-image",
        dest="input_image",
        help="Directory of page images (.png/.jpg/.jpeg). "
        "Required unless --samples-dir is used.",
    )
    parser.add_argument(
        "--ocr-text",
        dest="ocr_text",
        help="Directory of OCR hint files (.docx/.txt/.md). "
        "Each file must share the same stem as its matching image "
        "(e.g. page_1.png → page_1.docx). Off by default; pass this path to enable.",
    )

    # Batch mode — process every language subfolder under a samples root
    parser.add_argument(
        "--samples-dir",
        dest="samples_dir",
        help="Parent directory containing one subfolder per dictionary "
        "(e.g. assets/dictionaries/samples-2). When set, every "
        "subfolder is processed using its default layout "
        "(snippets/, introduction/, alphabet.txt) and "
        "outputs are written to {entry}/outputs/stage-1.",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=None,
        help="Optional list of language subfolder names to process when "
        "--samples-dir is used (e.g. --languages Armenian-English "
        "Yiddish-English). Defaults to every subfolder.",
    )

    # Output
    parser.add_argument(
        "-o",
        "--output",
        help="Output directory. Per page: <stem>.json (canonical) and "
        "<stem>.tsv (rendered from the JSON, with one column per discovered "
        "extra field). For two_stage runs also: <stem>_stage1.tsv. "
        "Required unless --samples-dir is used.",
    )
    # Model / strategy
    register_model_arguments(parser)
    parser.add_argument(
        "--strategy",
        choices=_STRATEGY_CHOICES,
        default="two_stage",
        help="Extraction strategy (default: two_stage). Use vlm_ocr for "
        "specialized OCR/VLM models (MinerU, PaddleOCR-VL, GLM-OCR).",
    )
    parser.add_argument(
        "--vlm-model",
        dest="vlm_model",
        choices=list_vlm_keys(),
        default=None,
        help="Specialized OCR/VLM backend when --strategy vlm_ocr: "
        "mineru2.5-pro, paddleocr-vl-1.5, glm-ocr.",
    )
    parser.add_argument(
        "--vlm-dpi",
        dest="vlm_dpi",
        type=int,
        default=200,
        help="DPI for rasterizing snippet PDFs in vlm_ocr mode (default: 200).",
    )
    parser.add_argument(
        "--mineru-batch-size",
        dest="mineru_batch_size",
        type=int,
        default=None,
        help="MinerU block batch size for --vlm-model mineru2.5-pro "
        "(default: 8, or MINERU_VL_BATCH_SIZE env).",
    )
    parser.add_argument(
        "--mineru-max-new-tokens",
        dest="mineru_max_new_tokens",
        type=int,
        default=None,
        help="MinerU max_new_tokens per layout block (default: 1024). "
        "Caps runaway generation on dictionary crops.",
    )
    parser.add_argument(
        "--vlm-backend",
        dest="vlm_backend",
        choices=("transformers", "vllm"),
        default=None,
        help="Inference backend for MinerU (--vlm-model mineru2.5-pro). "
        "Use vllm in .venv-mineru-vllm for faster block batching. "
        "Default: transformers, or MINERU_VL_BACKEND / VLM_BACKEND env.",
    )
    parser.add_argument(
        "--paddle-vl-rec-backend",
        dest="paddle_vl_rec_backend",
        choices=("native", "vllm-server"),
        default=None,
        help="PaddleOCR-VL recognition backend (default: native). "
        "Use vllm-server with --paddle-vl-rec-server-url for faster VL inference.",
    )
    parser.add_argument(
        "--paddle-vl-rec-server-url",
        dest="paddle_vl_rec_server_url",
        default=None,
        help="OpenAI-compatible Paddle vLLM server URL, e.g. http://127.0.0.1:8765/v1. "
        "When unset, --paddle-auto-vllm-server (default) starts a local server.",
    )
    parser.add_argument(
        "--paddle-auto-vllm-server",
        action="store_true",
        dest="paddle_auto_vllm_server",
        default=True,
        help="Auto-start Paddle GenAI vLLM server for paddleocr-vl-1.5 (default: on).",
    )
    parser.add_argument(
        "--no-paddle-auto-vllm-server",
        action="store_false",
        dest="paddle_auto_vllm_server",
        help="Do not spawn a local Paddle GenAI server; use native backend unless "
        "--paddle-vl-rec-server-url is set.",
    )
    parser.add_argument(
        "--paddle-vl-server-port",
        dest="paddle_vl_server_port",
        type=int,
        default=None,
        help="Local port when auto-starting Paddle GenAI server (default: 8765).",
    )
    parser.add_argument(
        "--paddle-vllm-server-python",
        dest="paddle_vllm_server_python",
        default=None,
        help="Python executable for .venv-paddle-vllm-server "
        "(default: project .venv-paddle-vllm-server/bin/python).",
    )
    parser.add_argument(
        "--glm-ocr-prompt",
        dest="glm_ocr_prompt",
        default=None,
        help='GLM-OCR prompt when --vlm-model glm-ocr (default: "Text Recognition:").',
    )
    parser.add_argument(
        "--glm-max-new-tokens",
        dest="glm_max_new_tokens",
        type=int,
        default=None,
        help="GLM-OCR max_new_tokens (default: 8192).",
    )
    parser.add_argument(
        "--glm-backend",
        dest="glm_backend",
        choices=("transformers", "vllm"),
        default=None,
        help="GLM-OCR backend when --vlm-model glm-ocr. vllm auto-starts a local "
        "server unless --no-glm-auto-vllm-server or GLM_VLLM_SERVER_URL is set.",
    )
    parser.add_argument(
        "--glm-auto-vllm-server",
        action="store_true",
        dest="glm_auto_vllm_server",
        default=True,
        help="Auto-start GLM-OCR vLLM server for glm-ocr (default: on).",
    )
    parser.add_argument(
        "--no-glm-auto-vllm-server",
        action="store_false",
        dest="glm_auto_vllm_server",
        help="Do not spawn a local GLM-OCR vLLM server.",
    )
    parser.add_argument(
        "--glm-vllm-server-url",
        dest="glm_vllm_server_url",
        default=None,
        help="OpenAI-compatible GLM-OCR vLLM server URL, e.g. http://127.0.0.1:8081/v1",
    )
    parser.add_argument(
        "--glm-vllm-server-port",
        dest="glm_vllm_server_port",
        type=int,
        default=None,
        help="Local port when auto-starting GLM-OCR vLLM (default: 8081).",
    )
    parser.add_argument(
        "--glm-vllm-server-python",
        dest="glm_vllm_server_python",
        default=None,
        help="Python for .venv-glmocr-vllm when auto-starting the server.",
    )

    # Two-stage specific
    parser.add_argument(
        "--alphabet",
        help="Alphabet/legend file (.txt/.md) or image (.png/.jpg). "
        "Sent to Stage 1 to prime the character inventory.",
    )
    parser.add_argument(
        "--intro",
        help="Dictionary introduction/preface — a file (.txt/.md/.docx) or a directory "
        "of images/PDFs. Not used when --pages is a PDF (use --intro-pages instead).",
    )
    parser.add_argument(
        "--intro-pages",
        dest="intro_pages",
        help="When --pages is a PDF: 1-based introduction pages from that same PDF "
        "(e.g. '1-5' or '1,3'). Optional; uses pdftk.",
    )
    parser.add_argument(
        "--stage1-reasoning",
        choices=["none", "low", "medium", "high"],
        default="low",
        dest="stage1_reasoning_effort",
        help="Reasoning effort for the Stage 1 transcription LLM call "
        "(default: low). Stage 1 is a faithful-copy task — higher reasoning "
        "tends to over-interpret the page (silent 'corrections', diacritic "
        "normalization, dropped chars). On Gemini 3, 'none' maps to 'low' "
        "(thinking cannot be fully disabled). On OpenRouter GPT-5 / Claude Opus, "
        "'none' sends reasoning.enabled=false when supported.",
    )
    parser.add_argument(
        "--stage2-reasoning",
        choices=["low", "medium", "high"],
        default="low",
        dest="stage2_reasoning_effort",
        help="Reasoning effort for the Stage 2 MDF extraction LLM call "
        "(default: low). High reasoning has been observed to leak chain-of-"
        "thought into output on dense pages — bump only when "
        "you've confirmed the leak doesn't happen for your model + pages.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature for all LLM steps (default: 0.1). GPT-5 family "
        "models only accept 1.0 — MUDIDI clamps automatically with a log line.",
    )
    parser.add_argument(
        "--stage-1-guides",
        dest="stage1_guides_path",
        help="Path to a .txt/.md/.docx file of extra rules appended verbatim to "
        "the Stage 1 user prompt under a 'USER DEFINED GUIDELINES' header. "
        "Optional — leave unset to use the default prompt.",
    )
    parser.add_argument(
        "--stage1-typography",
        action="store_true",
        dest="stage1_typography",
        help="Ask Stage 1 to annotate confident bold and italic text with <b>/<i> "
        "tags. By default Stage 1 emits plain OCR text.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        dest="batch_size",
        help="Concurrent page workers for two_stage LLM steps (default: 1). "
        "Uses a local thread pool — litellm.completion is one request per call; "
        "there is no litellm batch-size flag on completion(). Values >1 during "
        "Stage 1 may leave neighbor transcripts empty until those pages finish.",
    )
    parser.add_argument(
        "--stage-2-guides",
        dest="stage2_guides_path",
        help="Path to a .txt/.md/.docx file of extra rules appended verbatim to "
        "the Stage 2 user prompt under a 'USER DEFINED GUIDELINES' header. "
        "Optional — leave unset to use the default prompt.",
    )

    # Per-stage experiment namespacing + ablation toggles
    parser.add_argument(
        "--experiment-name",
        action="append",
        dest="experiment_names",
        default=None,
        help="Stage-1 experiment slot under outputs/stage-1/<name>/ (repeatable "
        "for vlm_ocr batch runs sharing one model load). Lets you keep multiple "
        "ablation runs side-by-side without overwriting each other. Default: "
        "'default' (or the VLM product label for vlm_ocr). Must match "
        "^[A-Za-z0-9_.-]+$. Also used as the stage-2 slot unless "
        "--stage2-experiment-name is set.",
    )
    parser.add_argument(
        "--stage2-experiment-name",
        dest="stage2_experiment_name",
        default=None,
        help="Stage-2 experiment slot under outputs/stage-2/<name>/. Defaults "
        "to --experiment-name. Use a different value to sweep stage-2 "
        "configurations (intro, stage-2 models, reasoning, stage-2 guides) "
        "against a fixed stage-1 baseline; the "
        "stage-2 manifest records --experiment-name as its stage1_source.",
    )
    parser.add_argument(
        "--stage1-output-subdir",
        dest="stage1_output_subdir",
        default="stage-1",
        help="Parent folder under each entry's outputs/ for Stage-1 artifacts "
        "(default: stage-1). Use stage-1-ocr to keep OCR-focused runs "
        "separate from the main stage-1 sweep.",
    )
    parser.add_argument(
        "--no-alphabet",
        action="store_true",
        dest="no_alphabet",
        help="Suppress alphabet/legend input for Stage 1. In --samples-dir mode "
        "this skips auto-discovery of <lang>/alphabet.txt; in single-entry "
        "mode it ignores --alphabet. Use for alphabet-ablation experiments.",
    )
    parser.add_argument(
        "--no-ocr-hint",
        action="store_true",
        dest="no_ocr_hint",
        help="Suppress OCR hint input for Stage 1 (clears an explicit "
        "--ocr-text). Rarely needed now that OCR hints are off by default.",
    )
    parser.add_argument(
        "--no-intro",
        action="store_true",
        dest="no_intro",
        help="Suppress dictionary introduction for Stage 2 Pass 1 (field "
        "discovery). In --samples-dir mode this skips "
        "auto-discovery of <lang>/introduction/; in single-entry mode it "
        "ignores --intro.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N images (applied after --one-page-per-entry).",
    )
    parser.add_argument(
        "--one-page-per-entry",
        action="store_true",
        dest="one_page_per_entry",
        help="Process one snippet page per dictionary entry. Prefers the lowest "
        "page number with stage-2-gold MDF labels; otherwise the lowest snippet "
        "with stage-1 gold; otherwise the lowest page number among snippets.",
    )
    parser.add_argument(
        "-p",
        "--page-offset",
        type=int,
        default=1,
        help="Page number assigned to the first image (increments per image).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-process pages even if output already exists (disables resume). "
        "In direct_mdf mode, also re-runs Pass 1 marker discovery for this "
        "stage-2 experiment slot.",
    )
    parser.add_argument(
        "--stage",
        choices=list(EXTRACT_STAGE_CHOICES),
        default="all",
        help="Run only stage 1, full stage 2 (Pass 1 + Pass 2), all stages, "
        "Stage 2 Pass 1 only (2-pass-1), or Stage 2 Pass 2 only (2-pass-2; "
        "requires existing mdf_parsing_guide.json). Default: all.",
    )
    parser.add_argument(
        "--stage1-mode",
        choices=["column", "flat"],
        default="flat",
        help="Stage-1 *write* format for two_stage when running stage 1 or all: "
        "flat text (default) or column TSV (deprecated).",
    )
    parser.add_argument(
        "--prompts-file",
        type=Path,
        default=None,
        dest="prompts_file",
        help="Path to PROMPT.json containing Stage 1 and Stage 2 LLM prompts "
        "(default: bundled assets/PROMPT.json). Edits reload on the next LLM call.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Benchmark mode: independent pages, gold-oriented defaults, samples layout.",
    )
    parser.add_argument(
        "--pages",
        dest="pages",
        help="Snippets directory or single source PDF (with --dict-pages). "
        "Alias for --input-image.",
    )
    parser.add_argument(
        "--dict-pages",
        dest="dict_pages",
        help="When --pages is a single PDF: 1-based dictionary page numbers to process "
        "(e.g. '1-10' or '1,3,5'). Required for PDF input; uses pdftk.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Output directory (alias for --output).",
    )
    parser.add_argument(
        "--parse-rules-page",
        action="append",
        dest="parse_rules_pages",
        help="1-based page number(s) for Stage 2 Pass 1 parse-rules discovery "
        "(same syntax as --dict-pages: e.g. '1', '1-4', '50,200'). Repeat the flag "
        "or comma-separate. Default: first page in --pages. Two or more pages use "
        "the multi-sample Pass 1 prompt.",
    )
    parser.add_argument(
        "--cheatsheet-page",
        action="append",
        dest="parse_rules_pages",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--parse-rules-file",
        type=Path,
        dest="parse_rules_file",
        help="Load mdf_parsing_guide.json from PATH and skip Pass 1 LLM discovery. "
        "Always reads PATH and refreshes {output_dir}/mdf_parsing_guide.json.",
    )
    parser.add_argument(
        "--dictionary-languages",
        type=Path,
        dest="dictionary_languages",
        default=None,
        help="Optional path to dictionary_languages.yaml for Stage 2 Pass 1 "
        "(layout and source/target language hint). Omitted in inference mode: "
        "Pass 1 runs without this hint unless this flag is set. Benchmark mode "
        "auto-loads {entry}/dictionary_languages.yaml when omitted.",
    )
    parser.add_argument(
        "--compare-gold",
        dest="compare_gold",
        default=None,
        help="Gold MDF file for page-level comparison (direct_mdf mode). "
        "Defaults to outputs/stage-2-gold/<page>/<page>_mdf when present.",
    )
    parser.add_argument(
        "--toolbox-pdf",
        type=Path,
        default=None,
        dest="toolbox_pdf",
        help="Optional SIL Toolbox MDF Reference Manual PDF attached during "
        "Pass 2 page extraction only (direct_mdf mode). Pass 1 field discovery "
        "uses the built-in marker text reference instead.",
    )
    parser.add_argument(
        "--prompt-cache",
        choices=["auto", "off"],
        default="auto",
        dest="prompt_cache",
        help="Prompt caching mode. auto uses litellm/provider prompt caching when "
        "supported; off sends uncached prompts (default: auto).",
    )
    parser.add_argument(
        "--media-reference",
        choices=["auto", "inline", "file-uri"],
        default="auto",
        dest="media_reference",
        help="How to attach reusable media such as toolbox PDFs. auto uses file "
        "parts/URIs when supported and falls back to inline data; inline always "
        "uses base64 data; file-uri prefers URI/file parts with inline fallback.",
    )
    parser.add_argument(
        "--prompt-cache-key",
        default=None,
        dest="prompt_cache_key",
        help="Optional stable cache key prefix for providers that accept cache "
        "routing hints (for example OpenAI prompt_cache_key).",
    )
    parser.add_argument(
        "--parse-rules-gold",
        action="store_true",
        dest="parse_rules_gold",
        help="Skip Pass 1 discovery; load "
        "outputs/stage-2-gold/mdf_parsing_guide.json for the current dictionary entry.",
    )
    parser.add_argument(
        "--field-cheatsheet-gold",
        action="store_true",
        dest="field_cheatsheet_gold",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--stage1-input",
        choices=["auto", "column", "flat"],
        default="auto",
        dest="stage1_input",
        help="Stage-2 transcript format preference: column TSV, flat text, or auto "
        "(TSV if present, else flat). With --stage1-source gold reads "
        "outputs/stage-1-gold/; with predictions reads "
        "outputs/stage-1/<experiment-name>/. Ignored for stage-1-only runs.",
    )  
    parser.add_argument(
        "--stage1-source",
        choices=["gold", "predictions"],
        default="gold",
        dest="stage1_source",
        help="Stage-2 transcript origin (default: gold). Use predictions with "
        "--experiment-name to consume Stage-1 model outputs (e.g. GLM-OCR-vllm).",
    )
    parser.add_argument(
        "--stage2-lexical-repair",
        action="store_true",
        dest="stage2_lexical_repair",
        help="After Stage 2 MDF extraction, conservatively repair lexical "
        "characters in field values from the Stage 1 transcript. MDF markers, "
        "line breaks, whitespace, and punctuation are preserved.",
    )
    parser.add_argument(
        "--stage1-agentic",
        action="store_true",
        dest="stage1_agentic",
        help="After each Stage 1 page output, run a bounded verifier-rewriter "
        "loop before saving the final Stage 1 artifact.",
    )
    parser.add_argument(
        "--stage2-agentic",
        action="store_true",
        dest="stage2_agentic",
        help="After each Stage 2 MDF page output, run a bounded verifier-rewriter "
        "loop before saving the final MDF artifact.",
    )
    parser.add_argument(
        "--agentic-max-iterations",
        type=int,
        default=2,
        dest="agentic_max_iterations",
        help="Maximum rewrite attempts for each enabled agentic stage after the "
        "initial stage output (default: 2).",
    )
    parser.add_argument(
        "--agentic-evaluator-model",
        default=None,
        dest="agentic_evaluator_model",
        help="Model for verifier calls. Defaults to the current stage model.",
    )
    parser.add_argument(
        "--agentic-rewriter-model",
        default=None,
        dest="agentic_rewriter_model",
        help="Model for correction calls. Defaults to the current stage model.",
    )
    parser.add_argument(
        "--agentic-reasoning",
        choices=["none", "low", "medium", "high"],
        default="low",
        dest="agentic_reasoning_effort",
        help="Reasoning effort for agentic verifier and rewriter calls "
        "(default: low).",
    )
    parser.add_argument(
        "--agentic-evaluator-reasoning",
        choices=["none", "low", "medium", "high"],
        default=None,
        dest="agentic_evaluator_reasoning_effort",
        help="Reasoning effort for agentic verifier/evaluator calls. Defaults "
        "to --agentic-reasoning when omitted.",
    )
    parser.add_argument(
        "--agentic-rewriter-reasoning",
        choices=["none", "low", "medium", "high"],
        default=None,
        dest="agentic_rewriter_reasoning_effort",
        help="Reasoning effort for agentic correction/rewrite calls. Defaults "
        "to --agentic-reasoning when omitted.",
    )
    parser.add_argument(
        "--agentic-min-retry-confidence",
        type=float,
        default=0.55,
        dest="agentic_min_retry_confidence",
        help="Minimum verifier confidence required before a retry can rewrite "
        "(default: 0.55).",
    )
    parser.add_argument(
        "--no-agentic-verifier-patches",
        action="store_true",
        dest="no_agentic_verifier_patches",
        help="Disable exact current_text→expected_text verifier patches before "
        "falling back to the correction model.",
    )
    parser.add_argument(
        "--no-agentic-concrete-retry-gate",
        action="store_true",
        dest="no_agentic_concrete_retry_gate",
        help="Allow retry decisions without localized evidence. Useful only for "
        "ablation; the default gate is safer.",
    )

    args = resolved_args if resolved_args is not None else parser.parse_args()
    attach_stage_models(args)

    if getattr(args, "pages", None):
        args.input_image = args.pages
    if getattr(args, "output_dir", None):
        args.output = args.output_dir

    is_benchmark = bool(getattr(args, "benchmark", False) or args.samples_dir)
    args.prompt_mode = "benchmark" if is_benchmark else "inference"
    if (
        not is_benchmark
        and args.stage in ("2", "all", "2-pass-2")
        and args.stage1_source == "gold"
    ):
        args.stage1_source = "predictions"

    if args.stage in ("2-pass-1", "2-pass-2") and args.strategy != "two_stage":
        parser.error(f"--stage {args.stage} requires --strategy two_stage")

    # ── VLM OCR strategy validation ───────────────────────────────────────────
    if args.strategy == "vlm_ocr":
        if not args.vlm_model:
            parser.error("--vlm-model is required when --strategy vlm_ocr")
        if args.stage != "1":
            parser.error("--strategy vlm_ocr only supports --stage 1")
    elif args.vlm_model:
        parser.error("--vlm-model is only valid with --strategy vlm_ocr")

    _normalize_experiment_names(args, parser)

    if args.stage1_mode == "flat" and args.strategy not in (
        "two_stage",
        "vlm_ocr",
        "mathpix_ocr",
    ):
        parser.error(
            "--stage1-mode flat requires two_stage, vlm_ocr, or mathpix_ocr"
        )

    if getattr(args, "batch_size", 1) < 1:
        parser.error("--batch-size must be >= 1")

    if getattr(args, "toolbox_pdf", None):
        if args.strategy != "two_stage":
            parser.error("--toolbox-pdf requires --strategy two_stage")
        if not args.toolbox_pdf.is_file():
            parser.error(f"--toolbox-pdf path not found: {args.toolbox_pdf}")

    _validate_pdf_page_args(args, parser)

    if getattr(args, "parse_rules_file", None) and not args.parse_rules_file.is_file():
        parser.error(f"--parse-rules-file path not found: {args.parse_rules_file}")
    if getattr(args, "parse_rules_file", None) and (
        getattr(args, "parse_rules_gold", False)
        or getattr(args, "field_cheatsheet_gold", False)
    ):
        parser.error("--parse-rules-file cannot be combined with --parse-rules-gold")

    if (
        args.stage in ("2", "2-pass-2")
        and getattr(args, "stage1_source", "gold") == "predictions"
        and args.strategy == "two_stage"
        and args.experiment_name == "default"
        and is_benchmark
    ):
        parser.error(
            "--stage1-source predictions requires an explicit --experiment-name "
            "for the Stage-1 slot to read (e.g. GLM-OCR-vllm_flat_alpha)"
        )

    # ── Apply ablation toggles to single-entry inputs too ────────────────────
    # (batch mode applies these inside _run_samples_dir before calling
    # _run_single_entry, so this only matters when --samples-dir is unset.)
    if args.no_alphabet:
        args.alphabet = None
    if args.no_ocr_hint:
        args.ocr_text = None
    if args.no_intro:
        args.intro = None

    # ── Load user-defined guides (if any) once, shared across all pages ──────
    args.stage1_guides_text = ""
    if getattr(args, "stage1_guides_path", None):
        p = Path(args.stage1_guides_path)
        if not p.exists():
            parser.error(f"--stage-1-guides path not found: {p}")
        args.stage1_guides_text = _read_text_file(p)
    args.stage2_guides_text = ""
    if getattr(args, "stage2_guides_path", None):
        p = Path(args.stage2_guides_path)
        if not p.exists():
            parser.error(f"--stage-2-guides path not found: {p}")
        args.stage2_guides_text = _read_text_file(p)

    prompts_path = args.prompts_file or default_prompts_path()
    if not prompts_path.is_file():
        parser.error(f"Prompts file not found: {prompts_path}")
    configure_prompts(prompts_path)

    # ── Dispatch: samples-dir batch mode vs. single-entry mode ────────────────
    if args.strategy == "vlm_ocr":
        if args.samples_dir:
            return _run_samples_dir_vlm(args, parser)
        if not args.input_image or not args.output:
            parser.error(
                "--input-image and --output are required for vlm_ocr "
                "unless --samples-dir is used."
            )
        return _run_single_entry_vlm(args, parser)

    if args.strategy == "mathpix_ocr":
        from mudidi.extraction.mathpix_ocr import (
            run_mathpix_ocr_batch,
            run_mathpix_ocr_entry,
        )
        from mudidi.ocr.mathpix_convert import MathpixConvertClient, MathpixConvertError

        if args.samples_dir:
            samples_root = Path(args.samples_dir)
            if not samples_root.is_dir():
                parser.error(f"--samples-dir must be a directory: {samples_root}")
            try:
                entries = _discover_sample_entries(samples_root, args.languages)
            except ValueError as exc:
                parser.error(str(exc))
            return run_mathpix_ocr_batch(args, entries)
        if not args.input_image or not args.output:
            parser.error(
                "--input-image and --output are required for mathpix_ocr "
                "unless --samples-dir is used."
            )
        try:
            client = MathpixConvertClient(
                poll_interval_seconds=args.mathpix_poll_interval_seconds,
                max_wait_seconds=args.mathpix_max_wait_seconds,
                request_timeout_seconds=args.mathpix_request_timeout_seconds,
            )
        except MathpixConvertError as exc:
            print(exc)
            return 1
        return run_mathpix_ocr_entry(
            args,
            Path(args.input_image),
            Path(args.output),
            client=client,
        )

    if args.samples_dir:
        return _run_samples_dir(args, parser)

    if not args.input_image or not args.output:
        parser.error(
            "--input-image and --output are required unless --samples-dir is used."
        )

    return _run_single_entry(args, parser)


def _validate_pdf_page_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Validate --dict-pages / --intro-pages pairing with PDF inputs."""
    pages_path = Path(args.input_image) if args.input_image else None
    is_source_pdf = bool(
        pages_path and pages_path.is_file() and pages_path.suffix.lower() in _PDF_EXTS
    )

    if is_source_pdf:
        if not getattr(args, "dict_pages", None):
            parser.error(
                "--dict-pages is required when --pages is a PDF "
                "(e.g. '1-10' or '1,3,5')"
            )
        try:
            if not parse_page_spec(args.dict_pages):
                parser.error("--dict-pages must list at least one page")
        except ValueError as exc:
            parser.error(f"Invalid --dict-pages: {exc}")
        if args.intro:
            parser.error(
                "--intro cannot be used when --pages is a PDF; "
                "pass --intro-pages to select pages from the same PDF"
            )
        if getattr(args, "intro_pages", None):
            try:
                if not parse_page_spec(args.intro_pages):
                    parser.error("--intro-pages must list at least one page")
            except ValueError as exc:
                parser.error(f"Invalid --intro-pages: {exc}")
    else:
        if getattr(args, "dict_pages", None):
            parser.error("--dict-pages is only valid when --pages is a single PDF file")
        if getattr(args, "intro_pages", None):
            parser.error("--intro-pages is only valid when --pages is a single PDF file")


def _normalize_experiment_names(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Resolve repeatable ``--experiment-name`` into ``experiment_names`` list."""
    names = args.experiment_names if args.experiment_names is not None else ["default"]

    if len(names) > 1 and args.strategy != "vlm_ocr":
        parser.error(
            "Multiple --experiment-name values are only supported with "
            "--strategy vlm_ocr"
        )

    if args.strategy == "vlm_ocr" and names == ["default"]:
        spec = get_vlm_spec(args.vlm_model)
        names = [spec.experiment_name]
        print(f"Using experiment slot: {names[0]}")

    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            parser.error(
                f"--experiment-name must match ^[A-Za-z0-9_.-]+$ (got: {name!r})"
            )

    args.experiment_names = names
    args.experiment_name = names[0]

    if args.stage2_experiment_name is None:
        args.stage2_experiment_name = names[0]
    elif not re.fullmatch(r"[A-Za-z0-9_.-]+", args.stage2_experiment_name):
        parser.error(
            f"--stage2-experiment-name must match ^[A-Za-z0-9_.-]+$ (got: "
            f"{args.stage2_experiment_name!r})"
        )

    subdir = getattr(args, "stage1_output_subdir", "stage-1")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", subdir):
        parser.error(
            f"--stage1-output-subdir must match ^[A-Za-z0-9_.-]+$ (got: {subdir!r})"
        )


def _discover_sample_entries(samples_root: Path, languages: Optional[List[str]]) -> List[Path]:
    """Return entry subfolders to process under ``samples_root``."""
    all_entries = sorted(p for p in samples_root.iterdir() if p.is_dir())
    if languages:
        requested = set(languages)
        available = {p.name for p in all_entries}
        missing = requested - available
        if missing:
            raise ValueError(
                f"--languages references unknown subfolders: {sorted(missing)}. "
                f"Available: {sorted(available)}"
            )
        return [p for p in all_entries if p.name in requested]
    return all_entries


def _run_samples_dir_vlm(args, parser) -> int:
    """Batch VLM OCR over sample entries (one model load for all languages)."""
    samples_root = Path(args.samples_dir)
    if not samples_root.is_dir():
        parser.error(f"--samples-dir must be a directory: {samples_root}")

    try:
        entries = _discover_sample_entries(samples_root, args.languages)
    except ValueError as exc:
        parser.error(str(exc))

    if not entries:
        print(f"No entry subfolders found under {samples_root}")
        return 1

    experiment_label = ", ".join(args.experiment_names)
    print(
        f"VLM OCR batch: {args.vlm_model} experiment(s) [{experiment_label}] on "
        f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} under "
        f"{samples_root}"
    )
    return run_vlm_ocr_batch(args, entries)


def _run_single_entry_vlm(args, parser) -> int:
    """Run VLM OCR on a single entry's snippets directory or source PDF."""
    input_path = Path(args.input_image)
    if not input_path.is_dir() and not (
        input_path.is_file() and input_path.suffix.lower() in _PDF_EXTS
    ):
        parser.error(f"--pages must be a directory or PDF file: {input_path}")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    snippets_cache_dir = output_dir / ".rendered_snippets"
    render_pdfs = True
    try:
        if input_path.is_dir():
            images = _materialize_page_inputs(
                input_path, snippets_cache_dir, render_pdfs=render_pdfs
            )
            input_dir = input_path
        else:
            images = _materialize_snippet_inputs(
                input_path,
                snippets_cache_dir,
                dict_pages_spec=getattr(args, "dict_pages", None),
                render_pdfs=render_pdfs,
                overwrite=bool(getattr(args, "overwrite", False)),
            )
            input_dir = snippets_cache_dir / "split"
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        print(exc)
        return 1

    if not images:
        print(f"No pages found for {input_path}")
        return 1

    if stage1_context_inputs_apply(args):
        entry_dir = Path(getattr(args, "entry_dir", None) or input_path.parent)
        input_errors = validate_configured_sample_entry(args, entry_dir, input_dir)
        if input_errors:
            report_entry_input_failures(
                entry_dir.name,
                input_errors,
                experiment_name=args.experiment_name,
            )
            return 1

    from mudidi.ocr.vlm.paddle_genai_server import ensure_paddle_vllm_server_args
    from mudidi.ocr.vlm.glm_vllm_server import ensure_glm_vllm_server_args

    paddle_server = None
    glm_server = None
    try:
        paddle_server = ensure_paddle_vllm_server_args(args)
        glm_server = ensure_glm_vllm_server_args(args)
        runner = create_vlm_runner(
            args.vlm_model,
            glm_prompt=getattr(args, "glm_ocr_prompt", None),
            glm_max_new_tokens=getattr(args, "glm_max_new_tokens", None),
            glm_backend=getattr(args, "glm_backend", None),
            glm_vllm_server_url=getattr(args, "glm_vllm_server_url", None),
            mineru_backend=getattr(args, "vlm_backend", None),
            mineru_batch_size=getattr(args, "mineru_batch_size", None),
            mineru_max_new_tokens=getattr(args, "mineru_max_new_tokens", None),
            paddle_vl_rec_backend=getattr(args, "paddle_vl_rec_backend", None),
            paddle_vl_rec_server_url=getattr(args, "paddle_vl_rec_server_url", None),
        )
        runner.load()
        try:
            return run_vlm_ocr_entry(args, input_dir, output_dir, runner)
        finally:
            runner.unload()
    finally:
        if paddle_server is not None:
            paddle_server.stop()
        if glm_server is not None:
            glm_server.stop()


def _run_samples_dir(args, parser) -> int:
    """Iterate over every language subfolder under ``args.samples_dir``."""
    samples_root = Path(args.samples_dir)
    if not samples_root.is_dir():
        parser.error(f"--samples-dir must be a directory: {samples_root}")

    try:
        entries = _discover_sample_entries(samples_root, args.languages)
    except ValueError as exc:
        parser.error(str(exc))

    if not entries:
        print(f"No entry subfolders found under {samples_root}")
        return 1

    print(
        f"Batch mode: processing {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} under {samples_root}"
    )

    any_failure = False
    attempted = 0
    configured_output_root = Path(args.output)
    for entry_dir in entries:
        pages_dir, _ = configure_benchmark_entry_args(
            args, entry_dir, configured_output_root
        )
        if not pages_dir.is_dir():
            print(
                f"[skip] {entry_dir.name}: no snippets/ or Dictionary pages/ folder"
            )
            continue

        predictions_root = getattr(args, "stage1_predictions_root", None)
        if predictions_root:
            source_slot = (
                Path(predictions_root)
                / entry_dir.name
                / args.stage1_output_subdir
                / args.experiment_name
            )
            destination_slot = (
                Path(args.output)
                / args.stage1_output_subdir
                / args.experiment_name
            )
            if not source_slot.is_dir():
                print(
                    f"[failed] {entry_dir.name}: missing Stage 1 prediction slot "
                    f"{source_slot}"
                )
                any_failure = True
                continue
            if source_slot.resolve() != destination_slot.resolve():
                shutil.copytree(source_slot, destination_slot, dirs_exist_ok=True)

        print("\n" + "#" * 60)
        print(f"# Entry: {entry_dir.name}")
        print("#" * 60)
        attempted += 1
        rc = _run_single_entry(args, parser)
        if rc != 0:
            any_failure = True

    if attempted == 0:
        print(f"No runnable entries found under {samples_root}")
        return 1
    return 1 if any_failure else 0


def _run_single_entry(args, parser) -> int:
    """Run extraction for a single entry (snippets directory or source PDF)."""
    input_path = Path(args.input_image)
    if not input_path.is_dir() and not (
        input_path.is_file() and input_path.suffix.lower() in _PDF_EXTS
    ):
        parser.error(f"--pages must be a directory or PDF file: {input_path}")

    input_dir = input_path if input_path.is_dir() else input_path.parent

    if stage1_context_inputs_apply(args) and input_path.is_dir():
        entry_dir = Path(getattr(args, "entry_dir", None) or input_dir.parent)
        input_errors = validate_configured_sample_entry(args, entry_dir, input_dir)
        if input_errors:
            report_entry_input_failures(
                entry_dir.name,
                input_errors,
                experiment_name=args.experiment_name,
            )
            return 1

    # ── Output dir (set up early so we can cache rendered PDF pages) ──────────
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_config = getattr(args, "resolved_config_snapshot", None)
    if resolved_config is not None:
        resolved_config = json.loads(json.dumps(resolved_config))
        resolved_config["input"]["pages"] = str(input_path.resolve())
        resolved_config["output"]["directory"] = str(output_dir.resolve())
    run_config = RunConfig.from_namespace(args)
    layout = output_layout_from_config(run_config)
    stage1_dir = layout.stage1_root
    stage2_dir = layout.stage2_root
    cheatsheet_root = (
        layout.output_dir if layout.inference else layout.stage2_root
    )
    snippets_cache_dir = output_dir / ".rendered_snippets"
    intro_cache_dir = output_dir / ".rendered_intro"

    # ── Collect snippet pages (render PDFs when any step needs PNG) ───────────
    render_pdfs = run_needs_pdf_rasterization(
        args.stage_models.stage_1,
        args.stage_models.stage_2_pass_1,
        args.stage_models.stage_2_pass_2,
    )
    try:
        images = _materialize_snippet_inputs(
            input_path,
            snippets_cache_dir,
            dict_pages_spec=getattr(args, "dict_pages", None),
            render_pdfs=render_pdfs,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        print(exc)
        return 1
    if not images:
        print(f"No images or PDFs found for {input_path}")
        return 1

    if getattr(args, "one_page_per_entry", False):
        images = select_one_stage2_page(
            images,
            output_dir,
            getattr(args, "stage1_input", "auto"),
            stage1_source=getattr(args, "stage1_source", "gold"),
            experiment_name=args.experiment_name,
        )
        if not images:
            print("One-page mode: no snippet page selected")
            return 1
        print(f"One-page mode: processing {images[0].name} only")

    if args.limit:
        images = images[: args.limit]

    # ── OCR text dir ───────────────────────────────────────────────────────────
    ocr_dir: Optional[Path] = None
    if args.ocr_text:
        ocr_dir = Path(args.ocr_text)
        if not ocr_dir.is_dir():
            parser.error(f"--ocr-text must be a directory: {ocr_dir}")

    # ── Intro (loaded once for all pages) ─────────────────────────────────────
    intro_text, intro_image_paths = "", []
    is_source_pdf = input_path.is_file() and input_path.suffix.lower() in _PDF_EXTS
    if is_source_pdf and getattr(args, "intro_pages", None):
        try:
            intro_text, intro_image_paths = _collect_intro_from_pdf(
                input_path,
                args.intro_pages,
                intro_cache_dir,
                render_pdfs=render_pdfs,
                overwrite=bool(getattr(args, "overwrite", False)),
            )
        except ValueError as exc:
            parser.error(str(exc))
        except RuntimeError as exc:
            print(exc)
            return 1
        print(
            f"Intro: {len(intro_text)} chars of text, {len(intro_image_paths)} images loaded."
        )
    elif args.intro:
        intro_path = Path(args.intro)
        if not intro_path.exists():
            print(f"Warning: --intro path not found: {args.intro}")
        else:
            intro_text, intro_image_paths = _collect_intro(
                intro_path, intro_cache_dir, render_pdfs=render_pdfs
            )
            print(
                f"Intro: {len(intro_text)} chars of text, {len(intro_image_paths)} images loaded."
            )

    dictionary_languages = None
    dictionary_profile = getattr(args, "dictionary_profile", None)
    entry_path = _entry_dir_for_run(args, output_dir, input_dir if input_path.is_dir() else input_path.parent)
    if entry_path:
        args.entry_dir = str(entry_path)
    if args.strategy == "two_stage":
        is_benchmark = bool(getattr(args, "benchmark", False) or args.samples_dir)
        if dictionary_profile is None:
            dictionary_languages = load_pass1_dictionary_languages(
                dictionary_languages_path=args.dictionary_languages,
                entry_dir=entry_path,
                metadata_csv_path=_DEFAULT_METADATA_CSV,
                benchmark=is_benchmark,
            )
        if dictionary_languages is not None:
            print(
                f"Pass 1 language hint: {dictionary_languages.layout} | "
                f"source={dictionary_languages.source.language} | "
                f"targets={[t.language for t in dictionary_languages.targets]}"
            )

    # ── Strategy (instantiated once, shared across pages) ─────────────────────
    parse_rules_samples: Optional[List[tuple[str, str, str]]] = None
    strategy = _build_strategy(
        args,
        intro_text,
        intro_image_paths,
        dictionary_languages,
        dictionary_profile,
        stage2_experiment_dir=cheatsheet_root if runs_stage2_any(args.stage) else None,
    )
    if (
        args.strategy == "two_stage"
        and runs_stage2_pass1(args.stage)
        and not getattr(args, "parse_rules_file", None)
        and not (
            getattr(args, "parse_rules_gold", False)
            or getattr(args, "field_cheatsheet_gold", False)
        )
    ):
        parse_rules_samples = _prepare_parse_rules_samples(
            args,
            images,
            stage1_dir,
            output_dir,
            layout=layout,
            strategy=strategy,
            ocr_dir=ocr_dir,
        )
        strategy.parse_rules_samples = parse_rules_samples
        sample_label = ", ".join(stem for stem, _, _ in parse_rules_samples)
        print(f"Pass 1 sample page(s): {sample_label}")
    elif getattr(args, "parse_rules_file", None):
        print(f"Pass 1: using parse rules file {args.parse_rules_file}")

    if args.stage == "2-pass-1":
        print("\nStage 2 Pass 1 only: discovering parse rules …")
        strategy.discover_parse_rules()
        parse_rules_path = cheatsheet_root / MDF_PARSING_GUIDE_FILENAME
        print(f"Pass 1 complete → {parse_rules_path}")
        if args.strategy == "two_stage":
            _write_run_config(
                stage2_dir,
                _build_stage2_manifest(
                    args,
                    input_dir,
                    images,
                    output_dir,
                    intro_image_paths,
                    config_to_yaml_dict(dictionary_languages)
                    if dictionary_languages
                    else None,
                ),
                force=args.overwrite,
                resolved_config=resolved_config,
            )
            _write_run_usage(
                output_dir,
                usage_roots=[],
                parse_rules_roots=[cheatsheet_root],
            )
        return 0

    transcript_cache: dict[str, str] = {}
    cache_lock = threading.Lock()

    def _transcript_loader(stem: str) -> str:
        with cache_lock:
            if stem in transcript_cache:
                return transcript_cache[stem]
        transcript_path = stage1_transcript_for_stage2(
            output_dir,
            stem,
            getattr(args, "stage1_input", "auto"),
            source=getattr(args, "stage1_source", "gold"),
            experiment_name=args.experiment_name,
            stage1_output_subdir=args.stage1_output_subdir,
            inference_layout=layout.inference,
        )
        if transcript_path is not None and transcript_path.is_file():
            text = read_stage1_transcript_text(transcript_path)
            with cache_lock:
                transcript_cache[stem] = text
            return text
        return ""

    run_phases = page_run_phases(args.stage)

    # ── Batch loop ─────────────────────────────────────────────────────────────
    total = len(images)
    skipped = 0
    processed = 0
    failed = 0

    print(f"\nFound {total} image(s) in {input_dir}")
    print(f"Output directory: {output_dir}")
    print(
        f"Strategy: {args.strategy} | Models: {args.stage_models.summary()} | "
        f"Stage: {args.stage} | Temperature: {args.temperature} | "
        f"Overwrite: {args.overwrite}"
    )
    if args.strategy == "two_stage":
        if runs_stage1(args.stage):
            print(
                f"Stage-1 slot: {args.experiment_name} | Alphabet: "
                f"{'on' if args.alphabet else 'off'} | OCR hint: "
                f"{'on' if args.ocr_text else 'off'} | "
                f"Reasoning: {args.stage1_reasoning_effort}"
            )
        if runs_stage2_any(args.stage):
            if args.stage in ("2", "2-pass-2"):
                source = getattr(args, "stage1_source", "gold")
                if source == "gold":
                    print(
                        f"Stage-1 gold input: {stage1_gold_dir(output_dir)} "
                        f"(preference={args.stage1_input}: *_stage1_GOLD.tsv / "
                        f"*_stage1_GOLD_flat.txt)"
                    )
                else:
                    print(
                        f"Stage-1 prediction input: "
                        f"{stage1_experiment_dir(output_dir, args.experiment_name, subdir=args.stage1_output_subdir)} "
                        f"(preference={args.stage1_input})"
                    )
            pass_label = {
                "2": "Pass 1 + Pass 2",
                "all": "Pass 1 + Pass 2",
                "2-pass-2": "Pass 2 only",
            }.get(args.stage, args.stage)
            print(
                f"Stage-2 slot: {args.stage2_experiment_name} | "
                f"Stage-2 mode: {pass_label} | "
                f"Reasoning: {args.stage2_reasoning_effort}"
                + (
                    f" | Toolbox PDF: {args.toolbox_pdf.name}"
                    if getattr(args, "toolbox_pdf", None)
                    else ""
                )
            )
        # Manifests describe what's on disk in each experiment slot. On
        # resume (slot already populated) the existing manifest wins so it
        # never drifts from the predictions it documents.
        if runs_stage1(args.stage):
            _write_run_config(
                stage1_dir,
                _build_stage1_manifest(args, input_dir, images, ocr_dir),
                force=args.overwrite,
                resolved_config=resolved_config,
            )
        if runs_stage2_any(args.stage):
            _write_run_config(
                stage2_dir,
                _build_stage2_manifest(
                    args,
                    input_dir,
                    images,
                    output_dir,
                    intro_image_paths,
                    config_to_yaml_dict(dictionary_languages)
                    if dictionary_languages
                    else None,
                ),
                force=args.overwrite,
                resolved_config=resolved_config,
            )
    print("=" * 60)

    for phase_index, page_run_stage in enumerate(run_phases):
        if len(run_phases) > 1:
            phase_label = "Stage 1 transcription" if page_run_stage == "1" else "Stage 2 MDF"
            print(
                f"\n{'=' * 60}\n"
                f"Phase {phase_index + 1}/{len(run_phases)}: {phase_label} "
                f"({total} page(s))\n"
                f"{'=' * 60}"
            )
        if (
            page_run_stage in ("2", "2-pass-2")
            and args.prompt_mode == "inference"
        ):
            for image_file in images:
                _transcript_loader(image_file.stem)

        batch_size = max(1, int(getattr(args, "batch_size", 1) or 1))
        use_concurrent = batch_size > 1 and args.strategy == "two_stage"
        if batch_size > 1 and page_run_stage == "1":
            print(
                "Note: --batch-size > 1 during Stage 1 may leave neighbor "
                "transcripts empty until those pages finish."
            )
        if use_concurrent:
            configure_page_concurrency(batch_size)
            print(f"Concurrent workers (--batch-size): {batch_size}")

        print_lock = threading.Lock()

        def _locked_print(*msg: object, **kwargs: object) -> None:
            with print_lock:
                print(*msg, **kwargs)

        def _run_page(idx: int, image_file: Path) -> str:
            slot_ctx = page_concurrency_slot() if use_concurrent else nullcontext()
            with slot_ctx:
                return _run_page_impl(idx, image_file)

        def _run_page_impl(idx: int, image_file: Path) -> str:
            page_number = args.page_offset + idx
            stem = image_file.stem
            stage1_page_dir = stage1_dir / stem
            stage2_page_dir = stage2_dir / stem
            stage1_tsv = stage1_tsv_path(stage1_page_dir, stem)
            stage1_flat = stage1_flat_path(stage1_page_dir, stem)
            stage1_gold_page_dir = stage1_gold_dir(output_dir) / stem
            stage1_gold_tsv = stage1_gold_tsv_path(stage1_gold_page_dir, stem)
            stage1_gold_flat = stage1_gold_flat_path(stage1_gold_page_dir, stem)
            stage1_transcript: Optional[Path] = None
            if page_run_stage in ("2", "2-pass-2"):
                stage1_transcript = stage1_transcript_for_stage2(
                    output_dir,
                    stem,
                    getattr(args, "stage1_input", "auto"),
                    source=getattr(args, "stage1_source", "gold"),
                    experiment_name=args.experiment_name,
                    stage1_output_subdir=args.stage1_output_subdir,
                    inference_layout=layout.inference,
                )
            if page_run_stage == "1" or runs_stage1(page_run_stage):
                stage1_done = (
                    stage1_flat
                    if getattr(args, "stage1_mode", "column") == "flat"
                    else stage1_tsv
                )
            else:
                stage1_done = stage1_transcript
            out_tsv = stage2_page_dir / (stem + ".tsv")
            stage2_done = stage2_page_dir / (stem + ".mdf.txt")

            if not args.overwrite:
                if page_run_stage == "1" and stage1_done.exists():
                    _locked_print(
                        f"[{idx+1}/{total}] SKIP {image_file.name} → stage1 already exists"
                    )
                    return "skipped"
                if page_run_stage in ("2", "2-pass-2") and stage2_done.exists():
                    _locked_print(
                        f"[{idx+1}/{total}] SKIP {image_file.name} → stage2 already exists"
                    )
                    return "skipped"

            if page_run_stage in ("2", "2-pass-2") and stage1_transcript is None:
                source = getattr(args, "stage1_source", "gold")
                if source == "gold":
                    _locked_print(
                        f"[{idx + 1}/{total}] SKIP {image_file.name} → no stage-1 gold transcript "
                        f"(preference={args.stage1_input}; looked for {stage1_gold_tsv.name} "
                        f"and {stage1_gold_flat.name})"
                    )
                else:
                    pred_flat = stage1_flat_path(
                        stage1_experiment_dir(
                            output_dir,
                            args.experiment_name,
                            subdir=args.stage1_output_subdir,
                        )
                        / stem,
                        stem,
                    )
                    _locked_print(
                        f"[{idx + 1}/{total}] SKIP {image_file.name} → no stage-1 prediction "
                        f"transcript in {args.experiment_name} "
                        f"(preference={args.stage1_input}; looked for {pred_flat.name})"
                    )
                return "skipped"

            phase_tag = f" [stage {page_run_stage}]" if len(run_phases) > 1 else ""
            _locked_print(
                f"\n[{idx+1}/{total}] Processing: {image_file.name}  "
                f"(page {page_number}){phase_tag}"
            )
            if runs_stage1(page_run_stage):
                stage1_page_dir.mkdir(parents=True, exist_ok=True)
            if runs_stage2_pass2(page_run_stage):
                stage2_page_dir.mkdir(parents=True, exist_ok=True)

            try:
                started = time.perf_counter()
                image_path = str(image_file)

                ocr_file = _find_ocr_file(ocr_dir, image_file.stem) if ocr_dir else None
                if ocr_dir and not ocr_file:
                    _locked_print(
                        f"  Note: no OCR hint found for {image_file.stem} in {ocr_dir}"
                    )
                ocr_result = _build_ocr_result(image_path, ocr_file)

                extract_kwargs: dict[str, object] = {}
                if args.strategy == "two_stage":
                    page_context = None
                    if args.prompt_mode == "inference":
                        page_context = build_page_context(
                            images,
                            idx,
                            transcript_loader=_transcript_loader,
                        )
                    if page_run_stage in ("2", "2-pass-2"):
                        extract_kwargs["stage1_output_path"] = str(stage1_transcript)
                    else:
                        extract_kwargs["stage1_output_path"] = str(stage1_done)
                    if runs_stage2_pass2(page_run_stage):
                        extract_kwargs["stage2_output_path"] = str(out_tsv)
                    extract_kwargs["run_stage"] = page_run_stage
                    if page_context is not None:
                        extract_kwargs["page_context"] = page_context

                page = None
                max_page_attempts = 3
                for page_attempt in range(max_page_attempts):
                    try:
                        if page_attempt > 0:
                            wait_for_provider_backoff()
                            _locked_print(
                                f"  Retrying {stem} after transient error "
                                f"(attempt {page_attempt + 1}/{max_page_attempts}) …"
                            )
                        page = strategy.extract(
                            ocr_result,
                            image_path,
                            page_number=page_number,
                            **extract_kwargs,
                        )
                        break
                    except Exception as exc:
                        if (
                            page_attempt >= max_page_attempts - 1
                            or not is_retryable_transient_error(exc)
                        ):
                            raise
                assert page is not None

                if runs_stage2_pass2(page_run_stage):
                    if args.strategy == "two_stage":
                        gold_path = _gold_mdf_path_for_entry(
                            output_dir,
                            stem,
                            getattr(args, "compare_gold", None),
                            entry_dir=entry_path,
                        )
                        mdf_text = page.mdf_text
                        repair_result = None
                        if (
                            getattr(args, "stage2_lexical_repair", False)
                            and stage1_transcript is not None
                        ):
                            stage1_repair_text = normalize_stage1_text_for_repair(
                                stage1_transcript
                            )
                            repair_result = repair_mdf_text(
                                page.mdf_text,
                                stage1_repair_text,
                            )
                            mdf_text = repair_result.text
                        result = save_direct_mdf_outputs(
                            mdf_text=mdf_text,
                            output_base=out_tsv,
                            gold_path=gold_path,
                        )
                        if repair_result is not None:
                            audit_path = stage2_page_dir / f"{stem}_lexical_repair.json"
                            write_repair_audit(repair_result, audit_path)
                            _locked_print(
                                f"  → Lexical repair changed "
                                f"{repair_result.changed_lines} MDF line(s); "
                                f"audit → {audit_path.name}"
                            )
                        if getattr(args, "stage2_agentic", False):
                            dictionary_languages_path = None
                            if entry_path is not None:
                                candidate = entry_path / "dictionary_languages.yaml"
                                if candidate.is_file():
                                    dictionary_languages_path = candidate
                            attempt_metrics = _write_stage2_agentic_attempt_metrics(
                                agentic_dir=stage2_page_dir / "agentic" / "stage2",
                                gold_path=gold_path,
                                page_id=f"{entry_path.name if entry_path else ''}/{stem}"
                                if entry_path
                                else stem,
                                dictionary_languages_path=dictionary_languages_path,
                            )
                            if attempt_metrics is not None:
                                _locked_print(
                                    f"  → Agentic attempt metrics → {attempt_metrics.name}"
                                )
                        if gold_path:
                            ok = result.get("gold_compare_ok", False)
                            report = result.get("gold_compare")
                            _locked_print(
                                f"  → MDF saved to stage-2/{stem}/{stem}.mdf.txt "
                                f"(gold compare {'ok' if ok else 'DIFFERS'}: {report})"
                            )
                        else:
                            _locked_print(
                                f"  → MDF saved to stage-2/{stem}/{stem}.mdf.txt"
                            )

                if runs_stage1(page_run_stage) and args.strategy == "two_stage":
                    if getattr(args, "stage1_agentic", False):
                        gold_flat = _stage1_gold_flat_path_for_entry(entry_path, stem)
                        attempt_metrics = _write_stage1_agentic_attempt_metrics(
                            agentic_dir=stage1_page_dir / "agentic" / "stage1",
                            gold_path=gold_flat,
                            page_id=f"{entry_path.name if entry_path else ''}/{stem}"
                            if entry_path
                            else stem,
                        )
                        if attempt_metrics is not None:
                            _locked_print(
                                f"  → Stage 1 agentic attempt metrics → {attempt_metrics.name}"
                            )
                    with cache_lock:
                        if stem not in transcript_cache:
                            if getattr(args, "stage1_mode", "column") == "flat":
                                flat_out = stage1_flat_path(stage1_page_dir, stem)
                                if flat_out.is_file():
                                    transcript_cache[stem] = read_stage1_transcript_text(
                                        flat_out
                                    )
                            else:
                                tsv_out = stage1_tsv_path(stage1_page_dir, stem)
                                if tsv_out.is_file():
                                    transcript_cache[stem] = read_stage1_transcript_text(
                                        tsv_out
                                    )

                elapsed = time.perf_counter() - started
                _locked_print(
                    f"  → Finished {stem} [stage {page_run_stage}] in {elapsed:.1f}s"
                )
                return "processed"

            except Exception as exc:
                _locked_print(f"  ERROR processing {image_file.name}: {exc}")
                import traceback

                with print_lock:
                    traceback.print_exc()
                return "failed"

        if use_concurrent:
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                outcomes = list(pool.map(_run_page, range(len(images)), images))
        else:
            outcomes = [_run_page(idx, image_file) for idx, image_file in enumerate(images)]

        for outcome in outcomes:
            if outcome == "processed":
                processed += 1
            elif outcome == "skipped":
                skipped += 1
            elif outcome == "failed":
                failed += 1

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"Done. {processed} processed, {skipped} skipped (resume), {failed} failed.")

    # Aggregate usage across all pages for this run
    if args.strategy == "two_stage" and processed > 0:
        usage_roots: list[Path] = []
        if runs_stage1(args.stage):
            usage_roots.append(stage1_dir)
        if runs_stage2_pass2(args.stage):
            usage_roots.append(stage2_dir)
        _write_run_usage(
            output_dir,
            usage_roots=usage_roots,
            parse_rules_roots=[cheatsheet_root],
        )

    return 0 if failed == 0 else 1


def _write_run_usage(
    output_dir: Path,
    *,
    usage_roots: list[Path] | None = None,
    parse_rules_roots: list[Path] | None = None,
) -> None:
    """Collect all per-page usage.json files and write a run-level summary."""
    import json

    def is_page_usage_file(path: Path) -> bool:
        return path.name == f"{path.parent.name}_usage.json"

    pages = []
    total_cost = 0.0
    total_elapsed = 0.0
    cost_available = False
    elapsed_available = False

    roots = usage_roots if usage_roots is not None else [output_dir]
    seen_usage_files: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for usage_file in sorted(root.rglob("*_usage.json")):
            if usage_file.name in {
                MDF_PARSING_GUIDE_USAGE_FILENAME,
                "run_usage.json",
            }:
                continue
            if not is_page_usage_file(usage_file):
                continue
            resolved_usage_file = usage_file.resolve()
            if resolved_usage_file in seen_usage_files:
                continue
            seen_usage_files.add(resolved_usage_file)
            data = json.loads(usage_file.read_text(encoding="utf-8"))
            page_cost = data.get("total_cost_usd")
            if page_cost is not None:
                total_cost += page_cost
                cost_available = True
            page_elapsed = data.get("total_elapsed_seconds")
            if page_elapsed is not None:
                total_elapsed += page_elapsed
                elapsed_available = True
            pages.append({"page": usage_file.parent.name, **data})

    field_discovery = None
    parse_roots = parse_rules_roots if parse_rules_roots is not None else [output_dir]
    parse_rules_usage_path = next(
        (
            root / MDF_PARSING_GUIDE_USAGE_FILENAME
            for root in parse_roots
            if (root / MDF_PARSING_GUIDE_USAGE_FILENAME).is_file()
        ),
        None,
    )
    if parse_rules_usage_path is not None:
        parse_rules_usage = json.loads(parse_rules_usage_path.read_text(encoding="utf-8"))
        field_discovery = parse_rules_usage.get("field_discovery")
        discovery_in_pages = any(page.get("field_discovery") for page in pages)
        if not discovery_in_pages:
            discovery_cost = parse_rules_usage.get("total_cost_usd")
            discovery_elapsed = parse_rules_usage.get("total_elapsed_seconds")
            if discovery_cost is not None:
                total_cost += discovery_cost
                cost_available = True
            if discovery_elapsed is not None:
                total_elapsed += discovery_elapsed
                elapsed_available = True

    run_summary = {
        "pages": pages,
        "field_discovery": field_discovery,
        "run_total_cost_usd": round(total_cost, 8) if cost_available else None,
        "run_total_elapsed_seconds": round(total_elapsed, 3) if elapsed_available else None,
    }

    out = output_dir / "run_usage.json"
    out.write_text(
        json.dumps(run_summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary_parts: list[str] = []
    if cost_available:
        summary_parts.append(f"Total estimated cost: ${total_cost:.4f}")
    if elapsed_available:
        summary_parts.append(f"Total LLM time: {total_elapsed:.1f}s")
    summary_str = f"  {' | '.join(summary_parts)}" if summary_parts else ""
    print(f"Run usage saved → {out}{summary_str}")


if __name__ == "__main__":
    exit(main())
