#!/usr/bin/env python3
"""Write dictionary and page statistics for the MUDIDI dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from mudidi.utils.dataset_statistics import (  # noqa: E402
    build_dataset_statistics,
    write_dataset_statistics,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dictionaries-dir",
        type=Path,
        default=REPO_ROOT / "dataset" / "MUDIDI" / "dictionaries",
        help="Dictionary dataset root.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dataset" / "MUDIDI",
        help="Directory in which to write the reports.",
    )
    args = parser.parse_args(argv)

    try:
        statistics = build_dataset_statistics(args.dictionaries_dir)
        paths = write_dataset_statistics(statistics, args.output_dir)
    except ValueError as exc:
        parser.error(str(exc))

    print(
        "Wrote statistics for "
        f"{statistics['summary']['dictionary_count']} dictionaries, "
        f"{statistics['summary']['stage1_page_count']} Stage 1 pages, and "
        f"{statistics['summary']['stage2_page_count']} Stage 2 pages: "
        + ", ".join(str(path) for path in paths)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
