"""Shared I/O and validation helpers for paper table generators."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path


class TableDataError(ValueError):
    """Raised when a table generator input violates its data contract."""


def default_repo_root(script_file: str | Path) -> Path:
    """Return the repository root for a script under ``tables/scripts``."""
    return Path(script_file).resolve().parents[2]


def parse_paths(
    script_file: str | Path,
    description: str,
    argv: Sequence[str] | None = None,
) -> tuple[Path, Path]:
    """Parse common repository and output-directory CLI options."""
    default_root = default_repo_root(script_file)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_root,
        help=f"Repository root (default: {default_root})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="LaTeX output directory (default: <repo-root>/tables/tables)",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else repo_root / "tables" / "tables"
    )
    return repo_root, output_dir


def read_delimited(
    path: Path,
    *,
    required_columns: Iterable[str] = (),
    delimiter: str = ",",
) -> list[dict[str, str]]:
    """Read a delimited text file and validate its required columns."""
    if not path.is_file():
        raise TableDataError(f"Input file not found: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if reader.fieldnames is None:
                raise TableDataError(f"{path}: missing header row")
            missing = sorted(set(required_columns) - set(reader.fieldnames))
            if missing:
                raise TableDataError(
                    f"{path}: missing required columns: {', '.join(missing)}"
                )
            return [dict(row) for row in reader]
    except UnicodeDecodeError as exc:
        raise TableDataError(f"{path}: invalid UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise TableDataError(f"{path}: invalid delimited data: {exc}") from exc


def index_unique(
    rows: Iterable[dict[str, str]],
    key_columns: Sequence[str],
    *,
    source: Path,
) -> dict[tuple[str, ...], dict[str, str]]:
    """Index rows by a composite key, rejecting duplicate keys."""
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        key = tuple(row[column] for column in key_columns)
        if key in indexed:
            rendered = ", ".join(repr(value) for value in key)
            raise TableDataError(
                f"{source}: duplicate key ({rendered}) for columns "
                f"{', '.join(key_columns)} at row {row_number}"
            )
        indexed[key] = row
    return indexed


def parse_bool(value: str, *, source: Path, field: str, row_number: int) -> bool:
    """Parse a strict CSV boolean with source context."""
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise TableDataError(
        f"{source}: row {row_number}: {field} must be true or false, got {value!r}"
    )


def parse_float(value: str, *, source: Path, field: str, row_number: int) -> float:
    """Parse a finite numeric CSV field with source context."""
    try:
        number = float(value)
    except ValueError as exc:
        raise TableDataError(
            f"{source}: row {row_number}: {field} must be numeric, got {value!r}"
        ) from exc
    if not (float("-inf") < number < float("inf")):
        raise TableDataError(
            f"{source}: row {row_number}: {field} must be finite, got {value!r}"
        )
    return number


def write_text_atomic(path: Path, content: str) -> None:
    """Atomically replace ``path`` with complete UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def lines_to_text(lines: Iterable[str]) -> str:
    """Join LaTeX lines with the repository's trailing-newline convention."""
    return "\n".join(lines) + "\n"
