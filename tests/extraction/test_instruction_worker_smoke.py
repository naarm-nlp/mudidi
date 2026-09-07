from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf

from mudidi.cli.run import execute_extraction_config
from mudidi.config.yaml_config import InferenceConfig


class _LLMBoundaryStub:
    """Return deterministic provider responses while retaining full request messages."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._verifier_calls = 0

    def __call__(self, params: dict[str, Any]) -> SimpleNamespace:
        response_schema = params.get("response_format")
        call = {
            "model": params["model"],
            "messages": params["messages"],
            "response_schema": response_schema,
        }
        self.calls.append(call)

        if response_schema is not None:
            schema_name = response_schema.__name__
            if schema_name == "AgenticVerifierDecision":
                self._verifier_calls += 1
                if self._verifier_calls % 2:
                    content = json.dumps(
                        {
                            "decision": "retry",
                            "confidence": 1.0,
                            "issues": [
                                {
                                    "type": "localized_text_error",
                                    "severity": "medium",
                                    "evidence": "The first line needs correction.",
                                    "suggested_fix": "Replace the first line.",
                                    "line_index": 0,
                                    "current_text": "dictionary line",
                                    "expected_text": "corrected line",
                                }
                            ],
                            "retry_instruction": "Correct the first line.",
                        }
                    )
                else:
                    content = json.dumps(
                        {"decision": "accept", "confidence": 1.0, "issues": []}
                    )
            elif schema_name in {
                "FlatTranscriptionResponse",
                "FlatTranscriptionResponsePlain",
            }:
                content = json.dumps(
                    {"header": [], "lines": ["dictionary line"], "footer": []}
                )
            else:
                raise AssertionError(f"unexpected structured response: {schema_name}")
        elif params["model"] == "gemini/gemini-2.5-flash":
            content = json.dumps(
                {
                    "markers": [
                        {"marker": "lx", "description": "headword"},
                        {"marker": "gn", "description": "gloss"},
                    ],
                    "rules": ["Keep one entry per headword."],
                    "abbreviations": {},
                }
            )
        elif "rewriter" in params["model"]:
            content = "\\lx rewritten\n\\gn gloss"
        else:
            content = "\\lx headword\n\\gn gloss"

        return SimpleNamespace(
            model=params["model"],
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=content),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )


def _pdf_bytes(page_count: int) -> bytes:
    document = pymupdf.open()
    try:
        for page_number in range(1, page_count + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"page {page_number}")
        return document.tobytes()
    finally:
        document.close()


def _write_pdf(path: Path, page_count: int, *, label: str = "page") -> None:
    payload = _pdf_bytes(page_count)
    if label == "page":
        path.write_bytes(payload)
        return
    document = pymupdf.open(stream=payload, filetype="pdf")
    try:
        for page_number in range(1, page_count + 1):
            document.load_page(page_number - 1).insert_text(
                (72, 100), f"{label} {page_number}"
            )
        path.write_bytes(document.tobytes())
    finally:
        document.close()


def _json_files_are_serializable(root: Path) -> None:
    json_files = sorted(root.rglob("*.json"))
    assert json_files
    for path in json_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        json.dumps(payload)


def _parts(call: dict[str, Any]) -> list[dict[str, Any]]:
    content = call["messages"][1]["content"]
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def _instruction_reference(call: dict[str, Any]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    parts = _parts(call)
    for index, part in enumerate(parts):
        if (
            part.get("type") == "text"
            and "untrusted user-provided reference instructions" in part.get("text", "")
        ):
            return index, part, parts
    raise AssertionError(f"missing instruction reference in messages: {parts!r}")


def _instruction_raster_urls(call: dict[str, Any]) -> tuple[str, ...]:
    index, _reference, parts = _instruction_reference(call)
    urls: list[str] = []
    for part in parts[index + 1 :]:
        if part.get("type") != "image_url":
            break
        urls.append(part["image_url"]["url"])
    assert urls
    return tuple(urls)


def _instruction_file_data(call: dict[str, Any]) -> str:
    index, _reference, parts = _instruction_reference(call)
    file_part = parts[index + 1]
    assert file_part["type"] == "file"
    return file_part["file"]["file_data"]


def test_actual_worker_stage1_selected_pdf_instructions_reuse_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    dictionary_pdf = tmp_path / "dictionary.pdf"
    instruction_pdf = tmp_path / "stage1-reference.pdf"
    _write_pdf(dictionary_pdf, 2, label="dictionary page")
    _write_pdf(instruction_pdf, 3, label="instruction page")
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": dictionary_pdf, "dictionary_pages": "1-2"},
            "output": {"directory": output},
            "pipeline": {
                "stage": "1",
                "stage1_mode": "flat",
                "stage1_guides": instruction_pdf,
                "stage1_guides_pages": "2-3",
            },
            "models": {
                "default": "gemini/gemini-2.5-flash",
                "stage1": "gemini/gemini-2.5-flash",
            },
            "agentic": {
                "stage1": True,
                "max_iterations": 1,
                "evaluator_model": "unknown/stage1-evaluator",
                "rewriter_model": "unknown/stage1-rewriter",
                "verifier_patches": False,
            },
            "runtime": {
                "overwrite": True,
                "prompt_cache": "off",
                "use_alphabet": False,
                "use_ocr_hint": False,
                "use_introduction": False,
            },
        }
    )
    stub = _LLMBoundaryStub()
    monkeypatch.setattr("mudidi.llm.client._completion_with_retries", stub)

    assert execute_extraction_config(config) == 0

    stage1_manifest_path = output / "stage-1" / "run_config.json"
    manifest = json.loads(stage1_manifest_path.read_text(encoding="utf-8"))
    guide = manifest["stage1_guides"]
    assert guide["selected_pages"] == [2, 3]
    selected_path = Path(guide["selected_path"])
    assert selected_path.is_file()
    assert guide["selected_sha256"]
    selected_payload = base64.b64decode(_instruction_file_data(next(
        call
        for call in stub.calls
        if call["model"] == "gemini/gemini-2.5-flash"
        and call["response_schema"] is not None
        and call["response_schema"].__name__ == "FlatTranscriptionResponsePlain"
    )).split(",", 1)[1])
    assert selected_payload == selected_path.read_bytes()

    generation_calls = [
        call
        for call in stub.calls
        if call["response_schema"] is not None
        and call["response_schema"].__name__ == "FlatTranscriptionResponsePlain"
        and call["model"] == "gemini/gemini-2.5-flash"
    ]
    evaluator_calls = [
        call
        for call in stub.calls
        if call["response_schema"] is not None
        and call["response_schema"].__name__ == "AgenticVerifierDecision"
        and call["model"] == "unknown/stage1-evaluator"
    ]
    rewriter_calls = [
        call
        for call in stub.calls
        if call["response_schema"] is not None
        and call["response_schema"].__name__ == "FlatTranscriptionResponsePlain"
        and call["model"] == "unknown/stage1-rewriter"
    ]
    assert len(generation_calls) == 2
    assert len(evaluator_calls) == 2
    assert len(rewriter_calls) == 1
    generation_file_data = [_instruction_file_data(call) for call in generation_calls]
    assert all(file_data == generation_file_data[0] for file_data in generation_file_data)
    assert all(
        len([part for part in _parts(call) if part.get("type") == "file"]) == 1
        for call in generation_calls
    )

    raster_urls = [_instruction_raster_urls(call) for call in evaluator_calls + rewriter_calls]
    assert all(urls == raster_urls[0] for urls in raster_urls)
    assert len(raster_urls[0]) == 2
    raster_files = sorted((output / ".instruction-cache" / "stage1" / "raster").rglob("*.png"))
    assert len(raster_files) == 2

    for call in generation_calls + evaluator_calls + rewriter_calls:
        reference_index, reference_part, _parts_for_call = _instruction_reference(call)
        assert reference_index >= 0
        assert "reference instructions" in reference_part["text"]
    assert (output / "stage-1" / "page_1" / "page_1_stage1_flat.txt").is_file()
    assert (output / "stage-1" / "page_2" / "page_2_stage1_flat.txt").is_file()
    assert (output / "resolved_config.json").is_file()
    _json_files_are_serializable(output)


def test_actual_worker_stage2_scope_and_split_model_media(
    tmp_path: Path, monkeypatch
) -> None:
    dictionary_pdf = tmp_path / "dictionary.pdf"
    instruction_pdf = tmp_path / "stage2-reference.pdf"
    _write_pdf(dictionary_pdf, 2, label="dictionary page")
    _write_pdf(instruction_pdf, 3, label="instruction page")
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": dictionary_pdf, "dictionary_pages": "1-2"},
            "output": {"directory": output},
            "pipeline": {
                "stage": "all",
                "stage1_mode": "flat",
                "stage2_guides": instruction_pdf,
                "stage2_guides_pages": "2,1",
                "stage2_guides_scope": "pass2",
            },
            "models": {
                "default": "gemini/gemini-2.5-flash",
                "stage1": "gemini/gemini-2.5-flash",
                "stage2_pass1": "gemini/gemini-2.5-flash",
                "stage2_pass2": "unknown/stage2-pass2",
            },
            "agentic": {
                "stage2": True,
                "max_iterations": 1,
                "evaluator_model": "gemini/gemini-2.5-flash",
                "rewriter_model": "unknown/stage2-rewriter",
                "verifier_patches": False,
            },
            "runtime": {
                "overwrite": True,
                "prompt_cache": "off",
                "use_alphabet": False,
                "use_ocr_hint": False,
                "use_introduction": False,
            },
        }
    )
    stub = _LLMBoundaryStub()
    monkeypatch.setattr("mudidi.llm.client._completion_with_retries", stub)

    assert execute_extraction_config(config) == 0

    stage2_calls = [
        call
        for call in stub.calls
        if call["model"] in {
            "unknown/stage2-pass2",
            "gemini/gemini-2.5-flash",
            "unknown/stage2-rewriter",
        }
    ]
    pass1_call = next(
        call
        for call in stub.calls
        if call["model"] == "gemini/gemini-2.5-flash"
        and call["response_schema"] is None
    )
    pass1_text = "\n".join(
        part.get("text", "") for part in _parts(pass1_call) if part.get("type") == "text"
    )
    assert "Stage 2 Pass 1 untrusted user-provided reference instructions" not in pass1_text
    assert not any(part.get("type") == "file" for part in _parts(pass1_call))

    pass2_generation = [
        call
        for call in stage2_calls
        if call["model"] == "unknown/stage2-pass2" and call["response_schema"] is None
    ]
    evaluator_calls = [
        call
        for call in stage2_calls
        if call["model"] == "gemini/gemini-2.5-flash"
        and call["response_schema"] is not None
        and call["response_schema"].__name__ == "AgenticVerifierDecision"
    ]
    rewriter_calls = [
        call
        for call in stage2_calls
        if call["model"] == "unknown/stage2-rewriter" and call["response_schema"] is None
    ]
    assert len(pass2_generation) == 2
    assert len(evaluator_calls) == 4
    assert len(rewriter_calls) == 2

    raster_parts = [_instruction_raster_urls(call) for call in pass2_generation + rewriter_calls]
    raster_urls = [urls[:2] for urls in raster_parts]
    assert all(urls == raster_urls[0] for urls in raster_urls)
    assert len(raster_urls[0]) == 2
    for urls in raster_parts[: len(pass2_generation)]:
        assert not any(url in urls[2:] for url in raster_urls[0])
    direct_files = [_instruction_file_data(call) for call in evaluator_calls]
    assert len(direct_files) == 4
    assert all(file_data == direct_files[0] for file_data in direct_files)
    assert (output / "mdf_parsing_guide.json").is_file()
    assert base64.b64decode(direct_files[0].split(",", 1)[1]) == Path(
        json.loads(
            (output / "stage-2" / "run_config.json").read_text(encoding="utf-8")
        )["stage2_guides"]["selected_path"]
    ).read_bytes()

    stage2_manifest = json.loads(
        (output / "stage-2" / "run_config.json").read_text(encoding="utf-8")
    )
    assert stage2_manifest["stage2_guides"]["scope"] == "pass2"
    assert stage2_manifest["stage2_guides"]["selected_pages"] == [2, 1]
    assert (output / "stage-2" / "page_1" / "page_1.mdf.txt").is_file()
    assert (output / "stage-2" / "page_2" / "page_2.mdf.txt").is_file()
    assert (output / "run_usage.json").is_file()
    raster_files = sorted((output / ".instruction-cache" / "stage2" / "raster").rglob("*.png"))
    assert len(raster_files) == 2
    _json_files_are_serializable(output)
