from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest

from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.schemas.ocr_result import OCRPageResult


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "chukchi_hallucinations"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
DEFAULT_AGENTIC_MODEL = "gemini/gemini-3.5-flash"
REASONING_LEVELS = {"none", "low", "medium", "high"}


@dataclass(frozen=True)
class HallucinationCase:
    case_id: str
    page: str
    fixture_path: Path
    gold_path: Path
    image_path: Path
    expected_path: str
    forbidden_markers: tuple[str, ...]
    fixture_markers: tuple[str, ...]
    must_remove_markers: tuple[str, ...]
    gold_required_tokens: tuple[str, ...]


def strip_stage1_markup(text: str) -> str:
    """Remove deprecated Stage 1 typography tags from gold fixtures."""
    text = re.sub(r"</?(?:b|i)>", "", text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.strip(), right.strip()).ratio()


def marker_count(text: str, markers: tuple[str, ...]) -> int:
    return sum(text.count(marker) for marker in markers)


def load_cases() -> list[HallucinationCase]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases: list[HallucinationCase] = []
    for item in data["cases"]:
        cases.append(
            HallucinationCase(
                case_id=item["id"],
                page=item["page"],
                fixture_path=FIXTURE_DIR / item["fixture"],
                gold_path=REPO_ROOT / item["gold"],
                image_path=REPO_ROOT / item["image"],
                expected_path=item["expected_path"],
                forbidden_markers=tuple(item["forbidden_markers"]),
                fixture_markers=tuple(item["fixture_markers"]),
                must_remove_markers=tuple(item.get("must_remove_markers", [])),
                gold_required_tokens=tuple(item["gold_required_tokens"]),
            )
        )
    return cases


def require_case_assets(case: HallucinationCase) -> None:
    """Skip dataset-backed checks when the local MUDIDI corpus is unavailable."""
    missing = [
        path
        for path in (case.gold_path, case.image_path)
        if not path.is_file()
    ]
    if missing:
        missing_paths = ", ".join(str(path) for path in missing)
        pytest.skip(
            "Local MUDIDI dataset is not installed; "
            f"missing required case assets: {missing_paths}"
        )


def env_choice(name: str, default: str) -> str:
    value = os.getenv(name, default)
    if value not in REASONING_LEVELS:
        raise ValueError(
            f"{name} must be one of {sorted(REASONING_LEVELS)}, got {value!r}"
        )
    return value


CASES = load_cases()


def test_missing_case_assets_are_skipped(tmp_path: Path) -> None:
    case = HallucinationCase(
        case_id="missing-assets",
        page="page_1",
        fixture_path=tmp_path / "fixture.txt",
        gold_path=tmp_path / "missing-gold.txt",
        image_path=tmp_path / "missing-image.png",
        expected_path="localized_retry",
        forbidden_markers=(),
        fixture_markers=(),
        must_remove_markers=(),
        gold_required_tokens=(),
    )

    with pytest.raises(pytest.skip.Exception, match="Local MUDIDI dataset"):
        require_case_assets(case)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_chukchi_hallucination_fixture_integrity(case: HallucinationCase) -> None:
    assert case.fixture_path.is_file()
    require_case_assets(case)

    bad_text = case.fixture_path.read_text(encoding="utf-8")
    plain_gold = strip_stage1_markup(case.gold_path.read_text(encoding="utf-8"))

    assert bad_text.strip()
    assert plain_gold.strip()
    assert bad_text != plain_gold
    assert "<b>" not in bad_text
    assert "<i>" not in bad_text
    assert "ocr_reference" not in bad_text.casefold()

    for marker in case.fixture_markers:
        assert marker in bad_text
    for token in case.gold_required_tokens:
        assert token in plain_gold

    before_similarity = text_similarity(bad_text, plain_gold)
    assert before_similarity < 0.99


def test_chukchi_hallucination_manifest_covers_expected_paths() -> None:
    expected_paths = {case.expected_path for case in CASES}
    assert expected_paths == {"localized_retry", "llm_rewrite", "recover"}
    assert {case.page for case in CASES} == {"page_3", "page_48"}


@pytest.mark.skipif(
    os.getenv("MUDIDI_RUN_AGENTIC_LLM_EVAL") != "1",
    reason="Set MUDIDI_RUN_AGENTIC_LLM_EVAL=1 to run paid image-grounded LLM eval.",
)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_chukchi_agentic_hallucination_recovery(
    case: HallucinationCase,
    tmp_path: Path,
) -> None:
    require_case_assets(case)

    model = os.getenv("MUDIDI_AGENTIC_EVAL_MODEL", DEFAULT_AGENTIC_MODEL)
    evaluator_reasoning = env_choice("MUDIDI_AGENTIC_EVAL_REASONING", "high")
    rewriter_reasoning = env_choice("MUDIDI_AGENTIC_REWRITE_REASONING", "low")

    bad_text = case.fixture_path.read_text(encoding="utf-8")
    plain_gold = strip_stage1_markup(case.gold_path.read_text(encoding="utf-8"))
    before_similarity = text_similarity(bad_text, plain_gold)
    before_marker_count = marker_count(bad_text, case.forbidden_markers)
    assert before_marker_count > 0

    strategy = TwoStageLLMExtraction(
        transcribe_model=model,
        stage1_mode="flat",
        stage1_typography=False,
        stage1_agentic=True,
        agentic_max_iterations=3,
        agentic_evaluator_model=model,
        agentic_rewriter_model=model,
        agentic_reasoning_effort="low",
        agentic_evaluator_reasoning_effort=evaluator_reasoning,
        agentic_rewriter_reasoning_effort=rewriter_reasoning,
    )
    ocr_result = OCRPageResult(
        source_image=str(case.image_path),
        backend="disabled",
        raw_text="",
    )

    fixed_text, usage = strategy._run_stage1_agentic_loop(
        initial_output=bad_text,
        image_path=str(case.image_path),
        ocr_result=ocr_result,
        artifact_dir=tmp_path / case.case_id / "agentic" / "stage1",
        output_suffix=".txt",
        page_context=None,
    )

    after_similarity = text_similarity(fixed_text, plain_gold)
    after_marker_count = marker_count(fixed_text, case.forbidden_markers)

    assert usage["stop_reason"] != "agentic_error"
    assert after_similarity > before_similarity
    assert after_marker_count < before_marker_count
    for marker in case.must_remove_markers:
        assert marker not in fixed_text

    if case.expected_path == "recover":
        final_decision_path = tmp_path / case.case_id / "agentic" / "stage1" / "final_decision.json"
        final_decision: dict[str, Any] = json.loads(
            final_decision_path.read_text(encoding="utf-8")
        )
        decisions = [
            attempt["decision"]["decision"]
            for attempt in final_decision.get("attempts", [])
        ]
        assert "recover" in decisions
