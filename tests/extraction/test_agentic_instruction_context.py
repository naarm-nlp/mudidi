from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import inspect
import pymupdf

from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.schemas.ocr_result import OCRPageResult


def test_stage1_verifier_retains_page_context_and_dispatches(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
        )
    )
    strategy = TwoStageLLMExtraction()
    captured = {}

    def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"decision": "accept"}, None, {"input_tokens": 1}

    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured",
        fake_complete_structured,
    )
    assert "page_context" in inspect.signature(strategy._verify_stage1_output).parameters
    strategy._verify_stage1_output(
        "output",
        image_path=str(image),
        ocr_result=OCRPageResult(source_image=str(image), backend="test", raw_text="ocr"),
        page_context=None,
        attempt=1,
    )
    assert captured["messages"][1]["role"] == "user"
    assert captured["response_schema"].__name__ == "AgenticVerifierDecision"


def test_legacy_stage2_guides_are_routed_by_scope() -> None:
    for scope, pass1_expected, pass2_expected in (
        ("pass1", "legacy", ""),
        ("pass2", "", "legacy"),
        ("both", "legacy", "legacy"),
    ):
        strategy = TwoStageLLMExtraction(stage2_guides="legacy", stage2_guides_scope=scope)
        pass1_text, _ = strategy._stage2_guide_values(
            strategy._stage2_context_for_pass1(), pass_name="pass1"
        )
        pass2_text, _ = strategy._stage2_guide_values(
            strategy._stage2_context_for_pass2(), pass_name="pass2"
        )
        assert pass1_text == pass1_expected
        assert pass2_text == pass2_expected


def test_pass1_single_and_multi_include_shared_context_before_samples(
    tmp_path: Path, monkeypatch
) -> None:
    from mudidi.instructions import prepare_instruction_context
    from mudidi.llm.pass_1 import discover_field_cheatsheet, discover_field_cheatsheet_multi

    guide = tmp_path / "guide.txt"
    guide.write_text("Keep reference only.", encoding="utf-8")
    context = prepare_instruction_context(
        guide, page_spec=None, cache_dir=tmp_path / "cache", models=[]
    )
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
    )
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(png)
    second.write_bytes(png)

    calls = []

    def fake_complete(*, messages, **kwargs):
        calls.append(messages)
        return (
            '{"markers": [{"marker": "lx", "description": "headword"}], '
            '"rules": [], "abbreviations": {}}',
            {"total_tokens": 1},
        )

    monkeypatch.setattr("mudidi.llm.pass_1.complete_with_usage", fake_complete)
    discover_field_cheatsheet(
        transcription="one",
        sample_image=first,
        intro_images=[],
        model="gemini/gemini-3-flash-preview",
        instruction_context=context,
    )
    discover_field_cheatsheet_multi(
        samples=[("one", "one", first), ("two", "two", second)],
        intro_images=[],
        model="gemini/gemini-3-flash-preview",
        instruction_context=context,
    )
    for messages in calls:
        parts = messages[1]["content"]
        assert "Keep reference only." in parts[0]["text"]
        assert parts[-1]["type"] == "image_url"
def test_stage1_generation_evaluator_and_rewriter_use_real_pdf_context(
    tmp_path: Path, monkeypatch
) -> None:
    from mudidi.agentic.verifier_loop import AgenticVerifierDecision
    from mudidi.instructions import prepare_instruction_context
    import mudidi.instructions as instructions

    image = tmp_path / "page.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
        )
    )
    guide = tmp_path / "guide.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Reference page one")
    document.new_page().insert_text((72, 72), "Reference page two")
    document.save(str(guide))
    document.close()

    original_file_content_part = instructions.file_content_part
    original_image_data_url = instructions.image_data_url
    original_render_pdf_pages = instructions.render_pdf_pages
    file_calls = []
    image_calls = []
    render_calls = []

    def spy_file_content_part(*args, **kwargs):
        file_calls.append((args, kwargs))
        return original_file_content_part(*args, **kwargs)

    def spy_image_data_url(*args, **kwargs):
        image_calls.append((args, kwargs))
        return original_image_data_url(*args, **kwargs)

    def spy_render_pdf_pages(*args, **kwargs):
        render_calls.append((args, kwargs))
        return original_render_pdf_pages(*args, **kwargs)

    monkeypatch.setattr(instructions, "file_content_part", spy_file_content_part)
    monkeypatch.setattr(instructions, "image_data_url", spy_image_data_url)
    monkeypatch.setattr(instructions, "render_pdf_pages", spy_render_pdf_pages)
    context = prepare_instruction_context(
        guide,
        page_spec="1-2",
        cache_dir=tmp_path / "cache",
        models=("gemini/gemini-2.5-flash", "unknown/model"),
    )
    assert context.metadata.selected_pages == (1, 2)
    assert context.pdf_data_url is not None
    assert len(context.raster_data_urls) == 2
    preparation_counts = (len(file_calls), len(image_calls), len(render_calls))
    assert preparation_counts == (1, 2, 1)

    strategy = TwoStageLLMExtraction(
        transcribe_model="gemini/gemini-2.5-flash",
        stage1_mode="flat",
        stage1_instruction_context=context,
        agentic_evaluator_model="unknown/model",
        agentic_rewriter_model="unknown/model",
        prompt_mode="inference",
    )
    structured_calls = []

    def fake_structured(**kwargs):
        structured_calls.append(kwargs)
        if kwargs["response_schema"].__name__ == "AgenticVerifierDecision":
            result = AgenticVerifierDecision(decision="accept", confidence=1.0)
        else:
            result = SimpleNamespace(header=[], lines=["rewritten"], footer=[])
        return result, "{}", {}

    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured",
        fake_structured,
    )
    ocr = OCRPageResult(source_image=str(image), backend="test", raw_text="ocr")
    transcribed, _raw, _usage, _messages = strategy._stage1_transcribe(
        ocr, str(image), page_context=None
    )
    strategy._verify_stage1_output(
        transcribed,
        image_path=str(image),
        ocr_result=ocr,
        page_context=None,
        attempt=0,
    )
    strategy._rewrite_stage1_output(
        transcribed,
        decision=AgenticVerifierDecision(decision="retry", confidence=1.0),
        image_path=str(image),
        ocr_result=ocr,
        page_context=None,
        attempt=1,
    )

    assert [call["model"] for call in structured_calls] == [
        "gemini/gemini-2.5-flash",
        "unknown/model",
        "unknown/model",
    ]
    assert (len(file_calls), len(image_calls), len(render_calls)) == preparation_counts
    assert len(structured_calls) == 3

    for call, stage_label in zip(
        structured_calls,
        ("Stage 1", "Stage 1 evaluator", "Stage 1 rewriter"),
    ):
        content = call["messages"][1]["content"]
        reference_text = [
            part["text"]
            for part in content
            if part["type"] == "text" and "reference instructions" in part["text"]
        ]
        assert len(reference_text) == 1
        assert reference_text[0].startswith(stage_label)
        assert content[-2]["type"] == "text"
        assert "TRANSCRIPTION TARGET" in content[-2]["text"]
        assert content[-1]["type"] == "image_url"

    direct_content = structured_calls[0]["messages"][1]["content"]
    file_parts = [part for part in direct_content if part["type"] == "file"]
    assert len(file_parts) == 1
    assert file_parts[0]["file"]["file_data"] == context.pdf_data_url

    raster_payloads = []
    for call in structured_calls[1:]:
        content = call["messages"][1]["content"]
        assert not any(part["type"] == "file" for part in content)
        raster_payloads.append(
            [
                part["image_url"]["url"]
                for part in content
                if part["type"] == "image_url"
                and part["image_url"]["url"] in context.raster_data_urls
            ]
        )
    assert raster_payloads == [list(context.raster_data_urls)] * 2

def test_stage2_agentic_scope_routes_context_for_evaluator_and_rewriter(
    monkeypatch,
) -> None:
    from mudidi.agentic.verifier_loop import AgenticVerifierDecision

    def context_parts(model, stage_label):
        del model
        return [
            {"type": "text", "text": f"{stage_label} PDF reference"},
            {"type": "file", "file": {"file_data": "data:application/pdf;base64,ref"}},
        ]

    context = SimpleNamespace(
        text="stage2 selected guide",
        metadata=SimpleNamespace(original_filename="stage2.pdf"),
        content_parts=context_parts,
    )
    field_map = SimpleNamespace(format_prompt_block=lambda: "\\lx headword")
    verifier_calls = []
    rewriter_calls = []

    def fake_structured(**kwargs):
        verifier_calls.append(kwargs)
        return AgenticVerifierDecision(decision="retry", confidence=1.0), "{}", {}

    def fake_with_usage(**kwargs):
        rewriter_calls.append(kwargs)
        return "rewritten", {}

    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured", fake_structured
    )
    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_with_usage", fake_with_usage
    )
    for scope in ("pass1", "pass2", "both"):
        verifier_calls.clear()
        rewriter_calls.clear()
        strategy = TwoStageLLMExtraction(
            stage2_instruction_context=context,
            stage2_guides_scope=scope,
            agentic_evaluator_model="provider/evaluator",
            agentic_rewriter_model="provider/rewriter",
        )
        decision = strategy._verify_stage2_output(
            "\\lx foo\n\\gn bar",
            transcribed_text="foo bar",
            field_map=field_map,
            attempt=1,
        )
        strategy._rewrite_stage2_output(
            "\\lx foo\n\\gn bar",
            transcribed_text="foo bar",
            field_map=field_map,
            decision=decision[0],
            attempt=1,
        )
        for call in (verifier_calls[0], rewriter_calls[0]):
            content = call["messages"][1]["content"]
            has_context = any(
                "stage2 selected guide" in part.get("text", "")
                or part.get("type") == "file"
                for part in content
            )
            assert has_context is (scope in {"pass2", "both"})
