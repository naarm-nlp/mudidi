from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).parents[2]
SCRIPTS_DIR = REPO_ROOT / "tables" / "scripts"
TRACKED_TABLES_DIR = REPO_ROOT / "tables" / "tables"

GENERATORS = (
    ("dictionary_info_table.py", ("dictionary_info.tex",)),
    ("dictionary_stats_table.py", ("dictionary_stats.tex",)),
    ("stage1_summary_table.py", ("stage1_summary.tex",)),
    ("stage1_script_table.py", ("stage1_by_script.tex",)),
    ("stage1_dictionary_table.py", ("stage1_by_dictionary.tex",)),
    (
        "stage1_ocr_hint_table.py",
        ("stage1_ocr_hint_per_dictionary.tex", "stage1_ocr_hint_summary.tex"),
    ),
    ("stage2_summary_table.py", ("stage2_summary.tex",)),
    ("stage2_dictionary_table.py", ("stage2_by_dictionary.tex",)),
    ("stage2_gold_cheatsheet_table.py", ("stage2_gold_cheatsheet.tex",)),
    ("stage2_e2e_summary_table.py", ("stage2_e2e_summary.tex",)),
    ("stage2_no_typography_table.py", ("stage2_no_typography.tex",)),
)


@pytest.mark.parametrize(("script_name", "output_names"), GENERATORS)
def test_generator_reproduces_tracked_tables(
    script_name: str,
    output_names: tuple[str, ...],
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / script_name),
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in tmp_path.iterdir()} == set(output_names)
    for output_name in output_names:
        assert (tmp_path / output_name).read_bytes() == (
            TRACKED_TABLES_DIR / output_name
        ).read_bytes()


def test_r_table_generators_are_fully_removed() -> None:
    assert list(SCRIPTS_DIR.glob("*_table.R")) == []
