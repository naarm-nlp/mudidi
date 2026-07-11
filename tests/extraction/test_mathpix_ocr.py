from __future__ import annotations

import argparse
import json
from pathlib import Path

from mudidi.extraction.mathpix_ocr import run_mathpix_ocr_entry


class _FakeMathpixClient:
    def convert_pdf_page(
        self,
        _snippet: Path,
        *,
        md_path: Path,
        lines_json_path: Path,
        upload_cache_dir: Path,
    ) -> Path:
        upload_cache_dir.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# converted", encoding="utf-8")
        lines_json_path.write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_width": 100,
                            "page_height": 100,
                            "lines": [
                                {
                                    "text": "headword definition",
                                    "cnt": [[0, 0], [90, 10]],
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return md_path


def test_mathpix_entry_writes_flat_output_and_manifest(tmp_path: Path) -> None:
    pages = tmp_path / "Dictionary pages"
    pages.mkdir()
    (pages / "page_1.pdf").write_bytes(b"%PDF-placeholder")
    output = tmp_path / "output"
    args = argparse.Namespace(
        experiment_name="Mathpix-OCR",
        overwrite=False,
        limit=None,
    )

    result = run_mathpix_ocr_entry(
        args,
        pages,
        output,
        client=_FakeMathpixClient(),
    )

    assert result == 0
    page_dir = output / "stage-1" / "Mathpix-OCR" / "page_1"
    assert (page_dir / "page_1_stage1_flat.txt").read_text(encoding="utf-8") == (
        "headword definition\n"
    )
    assert (page_dir / "output.md").is_file()
    assert (page_dir / "mathpix.lines.json").is_file()
    assert (
        output / "ocr-hints" / "Mathpix-OCR" / "page_1.md"
    ).read_text(encoding="utf-8") == "# converted"
    manifest = json.loads(
        (output / "stage-1" / "Mathpix-OCR" / "run_config.json").read_text()
    )
    assert manifest["strategy"] == "mathpix_ocr"
    assert manifest["inputs"]["page_count"] == 1
