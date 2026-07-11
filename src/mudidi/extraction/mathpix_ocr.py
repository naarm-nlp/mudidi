"""Typed Stage 1 Mathpix OCR execution for benchmark runs."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from mudidi.evaluation.stage1.flatten import FLAT_SPEC_VERSION, write_flat_text
from mudidi.ocr.adapters.mathpix_lines import mathpix_transcript_from_lines_json
from mudidi.ocr.mathpix_convert import MathpixConvertClient, MathpixConvertError
from mudidi.ocr.vlm.page_inputs import list_snippet_pages

logger = logging.getLogger(__name__)


class MathpixPageClient(Protocol):
    """Minimal Mathpix client interface used by the benchmark runner."""

    def convert_pdf_page(
        self,
        snippet_path: Path,
        *,
        md_path: Path,
        lines_json_path: Path,
        upload_cache_dir: Path,
    ) -> Path: ...


def _write_manifest(
    stage_dir: Path,
    args: Any,
    pages_dir: Path,
    pages: list[Path],
) -> None:
    path = stage_dir / "run_config.json"
    if path.exists() and not args.overwrite:
        return
    stage_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "stage": "1",
                "experiment_name": args.experiment_name,
                "created_utc": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "strategy": "mathpix_ocr",
                "flat_spec_version": FLAT_SPEC_VERSION,
                "inputs": {
                    "snippets_dir": str(pages_dir),
                    "page_count": len(pages),
                },
                "per_page": [
                    {"stem": page.stem, "snippet_path": str(page)} for page in pages
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_mathpix_ocr_entry(
    args: Any,
    pages_dir: Path,
    output_dir: Path,
    *,
    client: MathpixPageClient,
) -> int:
    """Convert one dictionary's pages with Mathpix and export flat Stage 1 text."""

    pages = list_snippet_pages(pages_dir)
    if args.limit:
        pages = pages[: args.limit]
    stage_dir = output_dir / "stage-1" / args.experiment_name
    _write_manifest(stage_dir, args, pages_dir, pages)
    upload_cache = output_dir / ".mathpix_upload_cache"
    failed = 0
    for index, page in enumerate(pages, start=1):
        page_dir = stage_dir / page.stem
        flat_path = page_dir / f"{page.stem}_stage1_flat.txt"
        if flat_path.is_file() and flat_path.stat().st_size > 0 and not args.overwrite:
            print(f"[{index}/{len(pages)}] SKIP {page.name} (already complete)")
            continue
        page_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = page_dir / "output.md"
        lines_path = page_dir / "mathpix.lines.json"
        try:
            client.convert_pdf_page(
                page,
                md_path=markdown_path,
                lines_json_path=lines_path,
                upload_cache_dir=upload_cache,
            )
            transcript = mathpix_transcript_from_lines_json(lines_path)
            write_flat_text(flat_path, transcript.all_lines())
            hint_path = (
                output_dir
                / "ocr-hints"
                / args.experiment_name
                / f"{page.stem}.md"
            )
            hint_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(markdown_path, hint_path)
            print(f"[{index}/{len(pages)}] Mathpix {page.name} -> {flat_path}")
        except (MathpixConvertError, OSError, ValueError) as exc:
            logger.error("Mathpix failed for %s: %s", page, exc)
            failed += 1
    return 1 if failed else 0


def run_mathpix_ocr_batch(args: Any, entries: list[Path]) -> int:
    """Run Mathpix over benchmark entries using one authenticated client."""

    load_dotenv()
    try:
        client = MathpixConvertClient(
            poll_interval_seconds=args.mathpix_poll_interval_seconds,
            max_wait_seconds=args.mathpix_max_wait_seconds,
            request_timeout_seconds=args.mathpix_request_timeout_seconds,
        )
    except MathpixConvertError as exc:
        logger.error("%s", exc)
        return 1

    output_root = Path(args.output)
    any_failure = False
    attempted = 0
    for entry in entries:
        pages_dir = entry / "Dictionary pages"
        if not pages_dir.is_dir():
            pages_dir = entry / "snippets"
        if not pages_dir.is_dir():
            logger.warning("Skipping %s: no supported pages directory", entry.name)
            continue
        attempted += 1
        rc = run_mathpix_ocr_entry(
            args,
            pages_dir,
            output_root / entry.name,
            client=client,
        )
        any_failure = any_failure or rc != 0
    if attempted == 0:
        logger.error("No runnable Mathpix entries found")
        return 1
    return 1 if any_failure else 0
