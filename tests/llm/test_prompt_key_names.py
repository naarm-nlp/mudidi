"""Contracts for unambiguous Stage 2 prompt identifiers."""

from mudidi.llm.prompt_mode import prompt_id_for_mode
from mudidi.llm.prompt_store import get_prompt_store


EXPECTED_STAGE_2_PROMPT_IDS = {
    "stage_2_pass_1_system",
    "stage_2_pass_1_user_single",
    "stage_2_pass_1_user_multi",
    "stage_2_pass_2_system_benchmark",
    "stage_2_pass_2_system_inference",
    "stage_2_pass_2_user_benchmark",
    "stage_2_pass_2_user_inference",
}

RETIRED_STAGE_2_PROMPT_IDS = {
    "stage_2_pass_1",
    "stage_2_pass_2",
    "stage_2_pass_2_multi",
    "stage_2_direct_mdf_system_benchmark",
    "stage_2_direct_mdf_system_inference",
    "stage_2_direct_mdf_user_benchmark",
    "stage_2_direct_mdf_user_inference",
}


def test_stage_2_prompt_ids_name_pass_role_and_variant() -> None:
    prompt_ids = set(get_prompt_store().prompt_ids())

    assert EXPECTED_STAGE_2_PROMPT_IDS <= prompt_ids
    assert RETIRED_STAGE_2_PROMPT_IDS.isdisjoint(prompt_ids)


def test_stage_2_pass_2_mode_prompt_ids_resolve() -> None:
    assert (
        prompt_id_for_mode("stage_2_pass_2_system", "benchmark")
        == "stage_2_pass_2_system_benchmark"
    )
    assert (
        prompt_id_for_mode("stage_2_pass_2_user", "inference")
        == "stage_2_pass_2_user_inference"
    )
