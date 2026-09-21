from pathlib import Path

from mudidi.cli.evaluate_stage1 import (
    find_flat_and_vlm_ocr_experiments_from_pred_root,
    list_stage1_experiments_from_pred_root,
)


def _mkdir(root: Path, experiment: str) -> None:
    (root / "Dict-English" / "stage-1" / experiment).mkdir(parents=True)


def test_stage1_benchmark_discovery_excludes_partial_legacy_slots(
    tmp_path: Path,
) -> None:
    _mkdir(tmp_path, "GLM-OCR")
    _mkdir(tmp_path, "qwen3vl235_flat_noalpha_ocr")
    _mkdir(tmp_path, "GLM-OCR-flat_alpha")
    _mkdir(tmp_path, "qwen3vl235_flat_noalpha")
    _mkdir(tmp_path, "gemini35flash_flat_alpha_agentic")
    _mkdir(tmp_path, "MinerU2.5-Pro")

    flat_and_ocr = find_flat_and_vlm_ocr_experiments_from_pred_root(
        tmp_path,
        languages=None,
    )
    all_discovered = list_stage1_experiments_from_pred_root(
        tmp_path,
        languages=None,
        name_contains=None,
    )

    assert "GLM-OCR" not in flat_and_ocr
    assert "qwen3vl235_flat_noalpha_ocr" not in flat_and_ocr
    assert "gemini35flash_flat_alpha_agentic" not in flat_and_ocr
    assert "GLM-OCR" not in all_discovered
    assert "qwen3vl235_flat_noalpha_ocr" not in all_discovered
    assert "gemini35flash_flat_alpha_agentic" in all_discovered
    assert "GLM-OCR-flat_alpha" in flat_and_ocr
    assert "qwen3vl235_flat_noalpha" in flat_and_ocr
    assert "MinerU2.5-Pro" in flat_and_ocr
