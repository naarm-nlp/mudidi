from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import inspect

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
def test_stage1_target_label_is_immediately_before_final_image(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
        )
    )
    context = SimpleNamespace(
        text="guide",
        metadata=SimpleNamespace(original_filename="guide.pdf"),
        content_parts=lambda model, stage_label: [
            {"type": "text", "text": "REFERENCE PDF"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ref"}},
        ],
    )
    strategy = TwoStageLLMExtraction(stage1_mode="flat", stage1_instruction_context=context)
    captured = {}

    def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(header=[], lines=[], footer=[]), "{}", {}

    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured",
        fake_complete_structured,
    )
    strategy._stage1_transcribe(
        OCRPageResult(source_image=str(image), backend="test", raw_text=""),
        str(image),
        page_context=None,
    )
    content = captured["messages"][1]["content"]
    assert content[-1]["type"] == "image_url"
    assert "TRANSCRIPTION TARGET" in content[-2]["text"]


def test_stage1_agentic_evaluator_and_rewriter_use_selected_context(
    tmp_path: Path, monkeypatch
) -> None:
    from mudidi.agentic.verifier_loop import AgenticVerifierDecision

    image = tmp_path / "page.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
        )
    )
    context = SimpleNamespace(
        text="selected guide",
        metadata=SimpleNamespace(original_filename="selected.pdf"),
        content_parts=lambda model, stage_label: [
            {"type": "text", "text": f"{stage_label} PDF reference"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ref"}},
        ],
    )
    strategy = TwoStageLLMExtraction(
        stage1_mode="flat",
        stage1_instruction_context=context,
        agentic_evaluator_model="provider/evaluator",
        agentic_rewriter_model="provider/rewriter",
    )
    structured_calls = []
    def fake_structured(**kwargs):
        structured_calls.append(kwargs)
        if kwargs["response_schema"].__name__ == "AgenticVerifierDecision":
            result = AgenticVerifierDecision(decision="retry", confidence=1.0)
        else:
            result = SimpleNamespace(header=[], lines=[], footer=[])
        return result, "{}", {}


    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured", fake_structured
    )
    ocr = OCRPageResult(source_image=str(image), backend="test", raw_text="ocr")
    strategy._verify_stage1_output(
        "output",
        image_path=str(image),
        ocr_result=ocr,
        page_context=None,
        attempt=1,
    )
    strategy._rewrite_stage1_output(
        "output",
        decision=AgenticVerifierDecision(decision="retry", confidence=1.0),
        image_path=str(image),
        ocr_result=ocr,
        page_context=None,
        attempt=1,
    )
    assert structured_calls[0]["model"] == "provider/evaluator"
    assert structured_calls[1]["model"] == "provider/rewriter"
    assert any(
        "selected guide" in part["text"]
        for part in structured_calls[0]["messages"][1]["content"]
        if part["type"] == "text"
    )
