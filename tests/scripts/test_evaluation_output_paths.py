from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_SCRIPTS = REPO_ROOT / "examples" / "evaluation"
ARCHIVED_SCRIPTS = EVALUATION_SCRIPTS / "archive"

CURRENT_RESULT_RUNNERS = {
    "run_stage1_benchmark_per_lang_script_eval.sh",
    "run_stage2_benchmark_per_lang_script_eval.sh",
    "run_stage2_e2e_lexical_repair.sh",
    "run_stage2_no_typography_eval.sh",
}
ACTIVE_SUPPORT_SCRIPTS = {
    "run_stage2_mdf_stage1_lang_projection.sh",
}
OUTDATED_RUNNERS = {
    "run_stage1_benchmark_eval.sh",
    "run_stage1_eval.sh",
    "run_stage2_e2e_eval.sh",
    "run_stage2_e2e_per_lang_script_eval.sh",
    "run_stage2_eval.sh",
}


@pytest.mark.parametrize(
    ("script_name", "expected_path", "outdated_path"),
    [
        (
            "run_stage1_benchmark_per_lang_script_eval.sh",
            "evaluations/stage1_flat_per_lang_script_eval",
            "evaluations/stage1_flat_per_lang-script_eval",
        ),
        (
            "run_stage2_benchmark_per_lang_script_eval.sh",
            "evaluations/stage2_mdf_lang_script_eval",
            "evaluations/stage2_mdf_per_lang-script_eval",
        ),
        (
            "run_stage2_no_typography_eval.sh",
            "evaluations/stage2_mdf_lang_script_eval/stage2_mdf_eval_summary.csv",
            "evaluations/stage2_mdf_eval/stage2_mdf_eval_summary.csv",
        ),
    ],
)
def test_scripts_use_current_evaluation_paths(
    script_name: str,
    expected_path: str,
    outdated_path: str,
) -> None:
    script = (EVALUATION_SCRIPTS / script_name).read_text()

    assert expected_path in script
    assert outdated_path not in script


def test_current_evaluation_paths_exist() -> None:
    assert (REPO_ROOT / "evaluations" / "stage1_flat_per_lang_script_eval").is_dir()
    assert (REPO_ROOT / "evaluations" / "stage2_mdf_lang_script_eval").is_dir()
    assert (
        REPO_ROOT
        / "evaluations"
        / "stage2_mdf_lang_script_eval"
        / "stage2_mdf_eval_summary.csv"
    ).is_file()


def test_only_current_result_runners_and_dependencies_remain_active() -> None:
    active_scripts = {path.name for path in EVALUATION_SCRIPTS.glob("*.sh")}

    assert active_scripts == CURRENT_RESULT_RUNNERS | ACTIVE_SUPPORT_SCRIPTS


def test_outdated_result_runners_are_archived() -> None:
    archived_scripts = {path.name for path in ARCHIVED_SCRIPTS.glob("*.sh")}

    assert OUTDATED_RUNNERS <= archived_scripts
