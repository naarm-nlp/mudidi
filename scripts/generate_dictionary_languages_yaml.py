#!/usr/bin/env python3
"""
Generate ``dictionary_languages.yaml`` for every sample under assets/dictionaries/samples.

Derives layout and language roles from folder names and
``assets/dictionaries/full dictionaries/dictionary_metadata.csv``.

MDF gloss markers follow SIL naming by language code in ``markers_for_config``
(fallback for the legacy schema export path only). The two-pass direct MDF
pipeline discovers markers in ``mdf_parsing_guide.json`` instead.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mudidi.utils.dictionary_languages import (  # noqa: E402
    build_config_from_folder,
    load_metadata_csv,
    write_dictionary_languages_yaml,
)

logger = logging.getLogger(__name__)

DEFAULT_SAMPLES = PROJECT_ROOT / "assets/dictionaries/samples"
DEFAULT_METADATA = (
    PROJECT_ROOT / "assets/dictionaries/full dictionaries/dictionary_metadata.csv"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=DEFAULT_SAMPLES,
        help="Root containing language-pair subfolders.",
    )
    parser.add_argument(
        "--metadata-csv",
        type=Path,
        default=DEFAULT_METADATA,
        help="dictionary_metadata.csv path.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rewrite existing dictionary_languages.yaml files.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.samples_dir.is_dir():
        logger.error("Samples dir not found: %s", args.samples_dir)
        return 1
    if not args.metadata_csv.is_file():
        logger.error("Metadata CSV not found: %s", args.metadata_csv)
        return 1

    rows = load_metadata_csv(args.metadata_csv)
    written = skipped = 0
    for entry_dir in sorted(args.samples_dir.iterdir()):
        if not entry_dir.is_dir() or entry_dir.name.startswith("."):
            continue
        out_path = entry_dir / "dictionary_languages.yaml"
        if out_path.exists() and not args.overwrite:
            skipped += 1
            continue
        try:
            config = build_config_from_folder(entry_dir.name, metadata_rows=rows)
            write_dictionary_languages_yaml(entry_dir, config)
            logger.info(
                "%s → layout=%s source=%s targets=%s",
                entry_dir.name,
                config.layout,
                config.source.language,
                [t.language for t in config.targets],
            )
            written += 1
        except ValueError as exc:
            logger.error("%s: %s", entry_dir.name, exc)
            return 1

    logger.info("Done: %d written, %d skipped (existing).", written, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
