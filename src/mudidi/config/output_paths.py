"""Output directory layout for benchmark vs inference runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mudidi.config.run_config import RunConfig
from mudidi.paths import MDF_PARSING_GUIDE_FILENAME
from mudidi.utils.stage1_input import stage1_experiment_dir


@dataclass(frozen=True)
class OutputLayout:
    """Resolved stage-1/stage-2 directories for one run."""

    output_dir: Path
    stage1_root: Path
    stage2_root: Path
    parse_rules_path: Path
    inference: bool

def output_layout_from_config(config: RunConfig) -> OutputLayout:
    """Return stage output roots for the given run configuration."""
    if config.benchmark:
        stage1_root = stage1_experiment_dir(
            config.output_dir,
            config.experiment_name,
            subdir=config.stage1_output_subdir,
        )
        stage2_root = config.output_dir / "stage-2" / (config.stage2_experiment_name or "default")
        parse_rules = stage2_root / MDF_PARSING_GUIDE_FILENAME
    else:
        stage1_root = config.output_dir / "stage-1"
        stage2_root = config.output_dir / "stage-2"
        parse_rules = config.output_dir / MDF_PARSING_GUIDE_FILENAME
    return OutputLayout(
        output_dir=config.output_dir,
        stage1_root=stage1_root,
        stage2_root=stage2_root,
        parse_rules_path=parse_rules,
        inference=not config.benchmark,
    )
