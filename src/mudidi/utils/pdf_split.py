"""Split individual pages from a PDF using PyMuPDF."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Sequence

import pymupdf


def parse_page_spec(spec: str) -> list[int]:
    """Expand a page specification into an ordered list of 1-based page numbers.

    Supports comma-separated singletons and hyphen-separated inclusive ranges,
    e.g. ``"97-123, 179-182"`` or ``"19, 83, 162"``. Returns ``[]`` for empty input.

    Raises:
        ValueError: When a token or range is malformed.
    """
    if not spec or not spec.strip():
        return []

    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", chunk)
            if not match:
                raise ValueError(f"Unrecognised page range: {chunk!r}")
            start, end = int(match.group(1)), int(match.group(2))
            if start < 1 or end < 1:
                raise ValueError(f"Page numbers must be >= 1: {chunk!r}")
            if end < start:
                raise ValueError(f"Descending range not supported: {chunk!r}")
            pages.extend(range(start, end + 1))
        else:
            if not chunk.isdigit():
                raise ValueError(f"Unrecognised page token: {chunk!r}")
            page = int(chunk)
            if page < 1:
                raise ValueError(f"Page numbers must be >= 1: {chunk!r}")
            pages.append(page)
    return pages


def extract_pdf_subset(
    source_pdf: Path,
    page_numbers: Sequence[int],
    output_pdf: Path,
) -> Path:
    """Write selected source pages to one atomically replaced PDF artifact."""

    pages = tuple(page_numbers)
    if not pages:
        raise ValueError("at least one PDF page is required")
    if not source_pdf.is_file():
        raise ValueError(f"source PDF does not exist: {source_pdf}")
    if source_pdf.stat().st_size == 0:
        raise ValueError(f"source PDF is empty: {source_pdf}")

    source_digest = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    fingerprint = {
        "source_sha256": source_digest,
        "page_numbers": list(pages),
    }
    fingerprint_path = output_pdf.with_name(output_pdf.name + ".fingerprint")
    encoded_fingerprint = json.dumps(
        fingerprint, sort_keys=True, separators=(",", ":")
    )
    if output_pdf.is_file() and fingerprint_path.is_file():
        try:
            if fingerprint_path.read_text(encoding="utf-8") == encoded_fingerprint:
                return output_pdf
        except OSError:
            pass

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    temporary_pdf: Path | None = None
    temporary_fingerprint: Path | None = None
    try:
        with pymupdf.open(str(source_pdf)) as source:
            for page in pages:
                if not 1 <= page <= source.page_count:
                    raise ValueError(
                        f"page {page} is outside source PDF "
                        f"(1-{source.page_count})"
                    )
            with pymupdf.open() as destination:
                for page in pages:
                    destination.insert_pdf(
                        source,
                        from_page=page - 1,
                        to_page=page - 1,
                    )
                fd, temporary_name = tempfile.mkstemp(
                    prefix=f".{output_pdf.name}.",
                    suffix=".tmp",
                    dir=str(output_pdf.parent),
                )
                os.close(fd)
                temporary_pdf = Path(temporary_name)
                destination.save(str(temporary_pdf))
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{fingerprint_path.name}.",
            suffix=".tmp",
            dir=str(output_pdf.parent),
        )
        os.close(fd)
        temporary_fingerprint = Path(temporary_name)
        temporary_fingerprint.write_text(encoded_fingerprint, encoding="utf-8")
        os.replace(temporary_pdf, output_pdf)
        os.replace(temporary_fingerprint, fingerprint_path)
    finally:
        for temporary in (temporary_pdf, temporary_fingerprint):
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
    return output_pdf


def extract_pdf_pages(
    source_pdf: Path,
    page_numbers: list[int],
    output_dir: Path,
    *,
    stem_template: str = "page_{page}.pdf",
    overwrite: bool = False,
) -> list[Path]:
    """Extract ``page_numbers`` from ``source_pdf`` into ``output_dir``.

    Output files are named ``page_{N}.pdf`` by default (``N`` = source PDF page
    number). Returns paths in the same order as ``page_numbers``.

    The source PDF is opened once, regardless of how many pages are requested.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    total = len(page_numbers)
    print(
        f"PDF split: {source_pdf.name} ({total} requested page(s))",
        flush=True,
    )
    with pymupdf.open(str(source_pdf)) as source:
        for index, page in enumerate(page_numbers, start=1):
            if not 1 <= page <= source.page_count:
                raise ValueError(
                    f"page {page} is outside source PDF "
                    f"(1-{source.page_count})"
                )
            out_path = output_dir / stem_template.format(page=page)
            if out_path.exists() and not overwrite:
                action = "reused"
            else:
                with pymupdf.open() as destination:
                    destination.insert_pdf(
                        source,
                        from_page=page - 1,
                        to_page=page - 1,
                    )
                    destination.save(str(out_path))
                action = "wrote"
            print(
                f"PDF split: {action} source page {page} "
                f"({index}/{total})",
                flush=True,
            )
            results.append(out_path)
    print(
        f"PDF split complete: {source_pdf.name} ({len(results)} page(s))",
        flush=True,
    )
    return results
