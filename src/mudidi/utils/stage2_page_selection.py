"""Select a single snippet page for Stage-2 extraction runs."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple, Union

from mudidi.utils.stage1_input import (
    Stage1InputPreference,
    Stage1Source,
    stage1_transcript_for_stage2,
)

logger = logging.getLogger(__name__)

_PAGE_NUM_RE = re.compile(r"^page_(\d+)(?:_p(\d+))?$", re.IGNORECASE)
PageSortKey = Tuple[int, int, int, str]


def page_sort_key(path_or_stem: Union[Path, str]) -> PageSortKey:
    """Sort key for snippet paths or page stems (numeric ``page_N`` order).

    Non-``page_*`` stems sort after numbered pages, lexicographically.
    Multi-page PDF renders use ``page_N_pK`` (sorted by N, then K).
    """
    stem = path_or_stem.stem if isinstance(path_or_stem, Path) else path_or_stem
    match = _PAGE_NUM_RE.match(stem)
    if match:
        page_num = int(match.group(1))
        sub_page = int(match.group(2)) if match.group(2) else 0
        return (0, page_num, sub_page, stem)
    return (1, 0, 0, stem)


def sort_snippet_pages(images: List[Path]) -> List[Path]:
    """Return snippet paths sorted by numeric page number."""
    return sorted(images, key=page_sort_key)


def list_stage2_gold_stems(output_dir: Path) -> List[str]:
    """Return page stems with gold MDF under ``outputs/stage-2-gold/`` (numeric order)."""
    gold_root = output_dir / "stage-2-gold"
    if not gold_root.is_dir():
        return []

    stems: List[str] = []
    for page_dir in gold_root.iterdir():
        if not page_dir.is_dir():
            continue
        stem = page_dir.name
        if (page_dir / f"{stem}.mdf.txt").is_file():
            stems.append(stem)
    return sorted(stems, key=page_sort_key)


def select_one_stage2_page(
    images: List[Path],
    output_dir: Path,
    stage1_input: Stage1InputPreference,
    *,
    stage1_source: Stage1Source = "gold",
    experiment_name: str = "default",
) -> List[Path]:
    """Pick one snippet page for a Stage-2 run.

    Priority:
      1. Lowest-numbered page with stage-2-gold MDF (if several are labeled).
      2. Lowest-numbered snippet that has a usable stage-1 transcript.
      3. Lowest-numbered snippet page discovered.

    Page order uses numeric ``page_N`` / ``page_N_pK`` stems, not lexicographic
    filename order.

    Args:
        images: Snippet image/PDF paths for the dictionary entry.
        output_dir: Entry ``outputs/`` directory.
        stage1_input: Stage-1 transcript preference for stage-2-only runs.
        stage1_source: Gold vs predicted Stage-1 slot.
        experiment_name: Stage-1 experiment when ``stage1_source=predictions``.

    Returns:
        A one-element list, or empty when ``images`` is empty.
    """
    if not images:
        return []

    ordered = sort_snippet_pages(images)
    by_stem = {path.stem: path for path in ordered}

    for stem in list_stage2_gold_stems(output_dir):
        match = by_stem.get(stem)
        if match is not None:
            logger.info(
                "One-page mode: selected %s (stage-2-gold MDF present)", match.name
            )
            return [match]

    for image in ordered:
        stem = image.stem
        if stage1_transcript_for_stage2(
            output_dir,
            stem,
            stage1_input,
            source=stage1_source,
            experiment_name=experiment_name,
        ):
            label = (
                "stage-1 gold"
                if stage1_source == "gold"
                else f"stage-1/{experiment_name}"
            )
            logger.info(
                "One-page mode: selected %s (lowest page with %s)",
                image.name,
                label,
            )
            return [image]

    logger.info("One-page mode: selected %s (lowest page number)", ordered[0].name)
    return [ordered[0]]
