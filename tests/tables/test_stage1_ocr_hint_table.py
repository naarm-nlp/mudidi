from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).parents[2]
SCRIPT = REPO_ROOT / "tables" / "scripts" / "stage1_ocr_hint_table.py"


def test_generator_uses_fixed_gemini_pro_alphabet_protocol(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
    per_dictionary = (tmp_path / "stage1_ocr_hint_per_dictionary.tex").read_text(
        encoding="utf-8"
    )
    summary = (tmp_path / "stage1_ocr_hint_summary.tex").read_text(encoding="utf-8")

    assert per_dictionary.count(r"& Gemini-Pro & \cmark") == 30
    assert "Gemini-Flash" not in per_dictionary
    assert "Claude-Opus" not in per_dictionary
    assert "GPT-5.5" not in per_dictionary
    assert "Gemini 3.1 Pro + alphabet" in summary
    assert "Best LLM + alphabet per language" not in summary
    assert r"0.043 & 0.045 & 0.122 & \textbf{0.800} & 0.039" in summary
    assert (
        r"\textbf{0.041} & \textbf{0.043} & \textbf{0.112} & 0.784 & \textbf{0.036}"
        in summary
    )
    assert r"$-0.002$ & $-0.002$ & $-0.010$ & $-0.016$ & $-0.004$" in summary
