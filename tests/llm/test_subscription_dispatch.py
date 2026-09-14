from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from mudidi.agentic.verifier_loop import AgenticVerifierDecision
from mudidi.extraction.llm_two_stage import TwoStageLLMExtraction
from mudidi.llm import client
from mudidi.llm.pass_1 import discover_field_cheatsheet, discover_field_cheatsheet_multi
from mudidi.llm.pass_2 import extract_direct_mdf
from mudidi.llm.subscriptions import (
    BackendCapabilities,
    CompletionRequest,
    CompletionResult,
    SubscriptionRuntime,
    SubscriptionRuntimeRouter,
    SubscriptionProvider,
)
from mudidi.schemas.transcription import (
    FlatTranscriptionResponsePlain,
    TranscriptionResponsePlain,
)
from mudidi.schemas.ocr_result import OCRPageResult


class _Answer(BaseModel):
    answer: str


class _UnionResponse(BaseModel):
    value: int | str
    optional_index: int | None = None


class _MappingResponse(BaseModel):
    values: dict[str, int]


class _FieldMap:
    def format_prompt_block(self) -> str:
        return "Markers: \\lx and \\gn"


class _FakeBackend:
    provider = SubscriptionProvider.OPENAI
    capabilities = BackendCapabilities(
        structured_output=True,
        usage=True,
        reasoning=True,
    )

    def __init__(self) -> None:
        self.requests: list[tuple[str, CompletionRequest]] = []

    def login(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def refresh(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def logout(self) -> None:
        raise NotImplementedError

    def status(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(("complete", request))
        return CompletionResult(
            text="subscription answer",
            usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            model=request.model,
        )

    def complete_structured(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(("structured", request))
        return CompletionResult(
            text='{"answer":"structured"}',
            structured_json={"answer": "structured"},
            usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            model=request.model,
        )


def _page(tmp_path: Path, name: str = "page.png") -> Path:
    path = tmp_path / name
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
        b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return path


def test_api_key_completion_keeps_existing_litellm_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    response = SimpleNamespace(
        model="gpt-4o",
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content="api answer")
            )
        ],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )

    monkeypatch.setattr(client, "api_key_for_model", lambda _model: "api-key")
    monkeypatch.setattr(
        client,
        "_completion_with_retries",
        lambda params: (captured.__setitem__("params", params), response)[1],
    )

    assert (
        client.complete("gpt-4o", [{"role": "user", "content": "hi"}]) == "api answer"
    )
    assert captured["params"]["api_key"] == "api-key"
    assert "backend" not in captured["params"]


def test_subscription_complete_never_enters_litellm_or_api_key_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(
        client.litellm, "completion", lambda **_kwargs: pytest.fail("LiteLLM called")
    )
    monkeypatch.setattr(
        client, "api_key_for_model", lambda _model: pytest.fail("API key read")
    )

    assert (
        client.complete(
            "gpt-5.1-codex",
            [{"role": "user", "content": "hi"}],
            backend=backend,
        )
        == "subscription answer"
    )
    assert backend.requests[0][0] == "complete"
    assert backend.requests[0][1].model == "gpt-5.1-codex"
    assert "temperature" not in backend.requests[0][1].model_dump()


@pytest.mark.parametrize(
    ("provider", "qualified", "native"),
    [
        (SubscriptionProvider.OPENAI, "openai/gpt-subscription", "gpt-subscription"),
        (
            SubscriptionProvider.GOOGLE,
            "gemini/gemini-subscription",
            "gemini-subscription",
        ),
        (
            SubscriptionProvider.CLAUDE,
            "anthropic/claude-subscription",
            "claude-subscription",
        ),
    ],
)
def test_subscription_completion_strips_selected_provider_prefix(
    provider: SubscriptionProvider,
    qualified: str,
    native: str,
) -> None:
    backend = _FakeBackend()
    backend.provider = provider

    assert (
        client.complete(
            qualified,
            [{"role": "user", "content": "hi"}],
            backend=backend,
        )
        == "subscription answer"
    )
    assert backend.requests[0][1].model == native


def test_subscription_router_dispatches_each_model_to_its_authenticated_provider() -> (
    None
):
    openai = _FakeBackend()
    google = _FakeBackend()
    google.provider = SubscriptionProvider.GOOGLE
    router = SubscriptionRuntimeRouter(
        (
            SubscriptionRuntime(SubscriptionProvider.OPENAI, openai),
            SubscriptionRuntime(SubscriptionProvider.GOOGLE, google),
        )
    )

    assert (
        client.complete(
            "openai/gpt-subscription",
            [{"role": "user", "content": "openai"}],
            backend=router,
        )
        == "subscription answer"
    )
    assert (
        client.complete(
            "gemini/gemini-subscription",
            [{"role": "user", "content": "google"}],
            backend=router,
        )
        == "subscription answer"
    )
    assert openai.requests[0][1].model == "gpt-subscription"
    assert google.requests[0][1].model == "gemini-subscription"


def test_subscription_router_rejects_model_without_authenticated_provider() -> None:
    router = SubscriptionRuntimeRouter(
        (SubscriptionRuntime(SubscriptionProvider.OPENAI, _FakeBackend()),)
    )

    with pytest.raises(ValueError, match="no authenticated google subscription"):
        client.complete(
            "gemini/gemini-subscription",
            [{"role": "user", "content": "hi"}],
            backend=router,
        )


def test_subscription_completion_rejects_a_different_provider_prefix() -> None:
    backend = _FakeBackend()

    with pytest.raises(ValueError, match="incompatible"):
        client.complete(
            "gemini/gemini-subscription",
            [{"role": "user", "content": "hi"}],
            backend=backend,
        )
    assert backend.requests == []


def test_subscription_usage_and_structured_results_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(
        client.litellm, "completion", lambda **_kwargs: pytest.fail("LiteLLM called")
    )
    monkeypatch.setattr(
        client, "api_key_for_model", lambda _model: pytest.fail("API key read")
    )

    text, usage = client.complete_with_usage(
        "gemini-3-flash",
        [{"role": "user", "content": "hi"}],
        backend=backend,
    )
    assert text == "subscription answer"
    assert usage["prompt_tokens"] == 3
    assert usage["completion_tokens"] == 4
    assert usage["total_tokens"] == 7
    assert usage["billing_mode"] == "subscription"
    assert usage["cost_usd"] is None

    parsed, raw, structured_usage = client.complete_structured(
        "gemini-3-flash",
        [{"role": "user", "content": "return JSON"}],
        _Answer,
        backend=backend,
    )
    assert parsed.answer == "structured"
    assert raw == '{"answer":"structured"}'
    assert backend.requests[-1][1].schema["type"] == "object"
    assert backend.requests[-1][1].schema["properties"]["answer"]["type"] == "string"
    assert "temperature" not in backend.requests[-1][1].model_dump()


def test_all_extraction_call_paths_receive_subscription_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend = _FakeBackend()
    page = _page(tmp_path)
    ocr = OCRPageResult(source_image=str(page), backend="test", raw_text="ocr text")
    strategy = TwoStageLLMExtraction(
        transcribe_model="stage1-model",
        stage2_pass1_model="pass1-model",
        stage2_pass2_model="pass2-model",
        stage1_mode="flat",
        backend=backend,
    )
    structured_calls: list[dict[str, Any]] = []
    usage_calls: list[dict[str, Any]] = []

    def fake_structured(**kwargs: Any):
        structured_calls.append(kwargs)
        if kwargs["response_schema"] is AgenticVerifierDecision:
            return (
                AgenticVerifierDecision(decision="accept", confidence=1.0),
                "{}",
                {"total_tokens": 1},
            )
        return (
            SimpleNamespace(header=[], lines=["line"], footer=[]),
            "{}",
            {"total_tokens": 1},
        )

    def fake_usage(**kwargs: Any):
        usage_calls.append(kwargs)
        if kwargs["model"] == "pass1-model":
            return (
                '{"markers": [{"marker": "lx", "description": "headword"}], "rules": [], "abbreviations": {}}',
                {"total_tokens": 1},
            )
        return "\\lx foo\n\\gn bar", {"total_tokens": 1}

    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_structured", fake_structured
    )
    monkeypatch.setattr(
        "mudidi.extraction.llm_two_stage.llm.complete_with_usage", fake_usage
    )
    monkeypatch.setattr("mudidi.llm.pass_1.complete_with_usage", fake_usage)
    monkeypatch.setattr("mudidi.llm.pass_2.llm.complete_with_usage", fake_usage)

    strategy._stage1_transcribe(ocr, str(page))
    discover_field_cheatsheet(
        transcription="one",
        sample_image=page,
        intro_images=[],
        model="pass1-model",
        backend=backend,
    )
    discover_field_cheatsheet_multi(
        samples=[("one", "one", page), ("two", "two", page)],
        intro_images=[],
        model="pass1-model",
        backend=backend,
    )
    extract_direct_mdf(
        transcription="one",
        image_path=str(page),
        field_map=_FieldMap(),
        model="pass2-model",
        reasoning_effort="low",
        backend=backend,
    )
    strategy._verify_stage1_output(
        "line",
        image_path=str(page),
        ocr_result=ocr,
        page_context=None,
        attempt=0,
    )
    strategy._rewrite_stage1_output(
        "line",
        decision=AgenticVerifierDecision(decision="retry", confidence=1.0),
        image_path=str(page),
        ocr_result=ocr,
        page_context=None,
        attempt=1,
    )
    strategy._verify_stage2_output(
        "\\lx foo",
        transcribed_text="foo",
        field_map=_FieldMap(),
        attempt=0,
    )
    strategy._rewrite_stage2_output(
        "\\lx foo",
        transcribed_text="foo",
        field_map=_FieldMap(),
        decision=AgenticVerifierDecision(decision="retry", confidence=1.0),
        attempt=1,
    )

    assert structured_calls
    assert usage_calls
    assert all(call["backend"] is backend for call in structured_calls)
    assert all(call["backend"] is backend for call in usage_calls)


@pytest.mark.parametrize(
    "response_schema",
    [
        FlatTranscriptionResponsePlain,
        TranscriptionResponsePlain,
        AgenticVerifierDecision,
    ],
)
def test_subscription_schema_boundary_normalizes_real_extraction_models(
    response_schema: type[BaseModel],
) -> None:
    from mudidi.llm.client import _normalize_subscription_schema
    from mudidi.llm.subscriptions.claude_research import (
        _require_supported_schema as require_claude_schema,
    )
    from mudidi.llm.subscriptions.openai_codex import (
        _require_supported_schema as require_openai_schema,
    )

    normalized = _normalize_subscription_schema(
        response_schema.model_json_schema(),
        provider=SubscriptionProvider.OPENAI,
    )

    def assert_supported(node: Any) -> None:
        assert isinstance(node, dict)
        assert not {"$defs", "$ref", "oneOf", "minimum", "maximum"} & set(node)
        if "anyOf" in node:
            assert isinstance(node["anyOf"], list)
            for child in node["anyOf"]:
                assert_supported(child)
        if node.get("type") == "object" or "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for child in node.get("properties", {}).values():
            assert_supported(child)
        if isinstance(node.get("items"), dict):
            assert_supported(node["items"])

    assert_supported(normalized)

    require_openai_schema(normalized)
    require_claude_schema(normalized)


def test_subscription_schema_normalizer_preserves_union_and_nullability() -> None:
    normalized = client._normalize_subscription_schema(
        _UnionResponse.model_json_schema(),
        provider=SubscriptionProvider.OPENAI,
    )
    properties = normalized["properties"]
    assert properties["value"]["type"] == ["integer", "string"]
    assert properties["optional_index"]["type"] == ["integer", "null"]


def test_subscription_schema_normalizer_rejects_schema_valued_additional_properties() -> (
    None
):
    generic = client._normalize_subscription_schema(
        _MappingResponse.model_json_schema()
    )
    assert isinstance(generic["properties"]["values"]["additionalProperties"], dict)
    with pytest.raises(
        client.SubscriptionUnsupportedRequest,
        match="additionalProperties",
    ):
        client._normalize_subscription_schema(
            _MappingResponse.model_json_schema(),
            provider=SubscriptionProvider.OPENAI,
        )


def test_subscription_client_import_has_no_litellm_or_dotenv_side_effects() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "from mudidi.llm.client import complete\n"
                "from mudidi.llm.subscriptions import CompletionResult\n"
                "class Backend:\n"
                "    def complete(self, request):\n"
                "        return CompletionResult(text='ok')\n"
                "assert complete('subscription-model', "
                "[{'role': 'user', 'content': 'hello'}], backend=Backend()) == 'ok'\n"
                "assert 'litellm' not in sys.modules\n"
                "assert not any(name == 'dotenv' or name.startswith('dotenv.') "
                "for name in sys.modules)\n"
            ),
        ],
        check=True,
    )


@pytest.mark.parametrize(
    "response_schema",
    [
        FlatTranscriptionResponsePlain,
        TranscriptionResponsePlain,
        AgenticVerifierDecision,
    ],
)
def test_real_subscription_request_builders_accept_normalized_extraction_schemas(
    response_schema: type[BaseModel],
) -> None:
    from mudidi.llm.client import _normalize_subscription_schema
    from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend
    from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend

    class _Store:
        def load(self, _provider: SubscriptionProvider):
            return None

    normalized = _normalize_subscription_schema(
        response_schema.model_json_schema(),
        provider=SubscriptionProvider.OPENAI,
    )
    request = CompletionRequest(
        model="subscription-model",
        messages=[{"role": "user", "content": "return the requested value"}],
        schema=normalized,
    )

    openai_body = OpenAICodexBackend(store=_Store())._build_request_body(
        request,
        structured=True,
    )
    claude_body = ClaudeResearchBackend(store=_Store())._build_request_body(
        request,
        structured=True,
    )

    assert openai_body["text"]["format"]["schema"] == normalized
    assert claude_body["output_config"]["format"]["schema"] == normalized
