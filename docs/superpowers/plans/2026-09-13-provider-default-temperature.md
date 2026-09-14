# Provider-Default Temperature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove temperature from every MUDIDI configuration and inference request so each model provider always selects its own default.

**Architecture:** Perform one clean interface cutover from the provider request boundary back through the extraction call graph, then remove the user-facing and persisted configuration surfaces. Provider-default behavior is represented only by omission: no compatibility field, ignored argument, clamp, model table, or replacement value remains in the final tree.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI/Jinja2, argparse, LiteLLM, provider-specific subscription adapters, pytest, Chromium browser verification.

**Spec:** `docs/superpowers/specs/2026-09-13-provider-default-temperature-design.md`

## Global Constraints

- MUDIDI MUST omit `temperature` from LiteLLM parameters and every direct provider request body.
- `models.temperature`, `--temperature`, and the dashboard Temperature field MUST be removed rather than accepted and ignored.
- Existing strict YAML containing `models.temperature` MUST fail validation until the field is deleted.
- Existing CLI commands containing `--temperature` MUST fail parsing until the option is deleted.
- Reasoning effort, extended-thinking budgets, token limits, retry behavior, model selection, `top_p`, frequency penalties, and presence penalties remain unchanged.
- Remove GPT-5 clamping, Gemini temperature exceptions, structured-retry temperature escalation, and their log/help text.
- Migrate every repository caller in the same implementation; do not add aliases, deprecations, `**kwargs` swallowing, or compatibility shims.
- Tests MUST be written and observed failing before production changes.
- Do not run the project-wide suite inside Tasks 1 or 2; run it once in Task 3.
- Every task ends with a verified commit, as required by `AGENTS.md`.

---

### Task 1: Remove temperature from the inference request graph

**Files:**
- Modify: `src/mudidi/llm/client.py`
- Modify: `src/mudidi/llm/pass_1.py`
- Modify: `src/mudidi/llm/pass_2.py`
- Modify: `src/mudidi/extraction/llm_two_stage.py`
- Modify: `src/mudidi/cli/extract.py` (`_build_strategy` only in this task)
- Modify: `src/mudidi/llm/subscriptions/types.py`
- Modify: `src/mudidi/llm/subscriptions/claude_research.py`
- Modify: `src/mudidi/llm/subscriptions/google_gemini_cli.py`
- Test: `tests/llm/test_client_reasoning.py`
- Test: `tests/llm/test_subscription_types.py`
- Test: `tests/llm/test_subscription_dispatch.py`
- Test: `tests/llm/test_subscription_claude_research.py`
- Test: `tests/llm/test_subscription_google_gemini_cli.py`
- Test: `tests/llm/test_subscription_openai_codex.py`
- Test: affected tests under `tests/extraction/`

**Interfaces:**
- Produces: `_build_params(model, messages, max_tokens, reasoning_effort, *, top_p=None, prompt_cache_key=None, prompt_cache_retention=None) -> dict[str, Any]` with no temperature input or output key.
- Produces: `complete`, `complete_with_usage`, and `complete_structured` without a `temperature` parameter.
- Produces: `CompletionRequest` without a `temperature` field; extra input named `temperature` is rejected by its existing `extra="forbid"` contract.
- Produces: stage helper and `TwoStageLLMExtraction` signatures without temperature.
- Consumes: existing reasoning, token-limit, schema, cache, backend, and retry interfaces unchanged.

- [ ] **Step 1: Enumerate symbol-aware callers before changing exported interfaces**

Use LSP references for `complete`, `complete_with_usage`, `complete_structured`, `CompletionRequest`, `discover_dictionary_markers`, `discover_dictionary_markers_from_pages`, `extract_mdf`, and `TwoStageLLMExtraction.__init__`. If the Python language server still reports `textDocument/references` as unsupported, record that limitation and use targeted repository searches for these exact symbols plus `temperature=`; do not infer that the current known list is exhaustive.

- [ ] **Step 2: Write failing generic-client and request-contract tests**

Replace the GPT-5 clamp and GPT-4 temperature-preservation tests in `tests/llm/test_client_reasoning.py` with an omission contract covering ordinary OpenAI, GPT-5, Gemini 2.5, Gemini 3, Anthropic, and OpenRouter models:

```python
@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-4o",
        "openai/gpt-5.5",
        "gemini/gemini-2.5-pro",
        "gemini/gemini-3.1-pro-preview",
        "anthropic/claude-sonnet-4-6",
        "openrouter/anthropic/claude-sonnet-5",
    ],
)
def test_build_params_uses_provider_default_temperature(model: str) -> None:
    params = _build_params(
        model,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1024,
        reasoning_effort=None,
    )

    assert "temperature" not in params
```

Update every remaining `_build_params` call in that file to the final signature. Remove imports and tests for `_requires_temperature_one` and `_effective_temperature`.

In `tests/llm/test_subscription_types.py`, replace the temperature-carrying assertion with strict rejection:

```python
def test_completion_request_rejects_removed_temperature_field() -> None:
    with pytest.raises(ValidationError, match="temperature"):
        CompletionRequest.model_validate(
            {
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.1,
            }
        )
```

In `tests/llm/test_subscription_dispatch.py`, assert captured `CompletionRequest` objects expose no `temperature` field:

```python
assert "temperature" not in backend.requests[-1][1].model_dump()
```

- [ ] **Step 3: Write failing direct-provider omission tests**

Update the Claude extended-thinking test to omit temperature at construction and assert both thinking and provider-default omission:

```python
request = CompletionRequest(
    model="claude-sonnet-research",
    messages=[{"role": "user", "content": "Think carefully."}],
    reasoning="high",
    max_tokens=10000,
)
result = backend.complete(request)
body = json.loads(captured_request.data)
assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192}
assert "temperature" not in body
assert result.text == "hello"
```

Delete the obsolete test expecting `thinking_temperature_conflict` because callers can no longer construct that state.

Update the Google request test to construct `CompletionRequest` without temperature and assert:

```python
generation = body["request"]["generationConfig"]
assert "temperature" not in generation
assert generation["maxOutputTokens"] == 123
assert generation["thinkingConfig"] == {"thinkingBudget": 8192}
```

Update the OpenAI Codex request test to remove the now-invalid constructor field while retaining its existing `assert "temperature" not in captured["body"]` contract.

- [ ] **Step 4: Run the new tests and verify contract failures**

Run:

```bash
uv run pytest -q \
  tests/llm/test_client_reasoning.py \
  tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_dispatch.py \
  tests/llm/test_subscription_claude_research.py \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_openai_codex.py
```

Expected: failures show the old `_build_params` signature, `CompletionRequest.temperature`, Claude/Google body fields, or the obsolete Claude conflict. Fix test syntax or setup errors until failures are caused only by the old production contract.

- [ ] **Step 5: Remove temperature from the generic completion API**

In `src/mudidi/llm/client.py`:

1. Delete `_requires_temperature_one`, `_effective_temperature`, and `_set_temperature_param`.
2. Remove `temperature` from `_build_params`, `_subscription_request`, `complete`, `complete_with_usage`, and `complete_structured` signatures and invocations.
3. Do not add `temperature` to the `params` dict for any model family.
4. Keep Gemini reasoning behavior, but change the Gemini 3 log to omit the false temperature statement:

```python
print(f"  [Gemini 3] reasoning_effort={effort}")
```

5. Structured retries rebuild the same provider-default parameter set. Delete `attempt_temperature` and change the retry banner to:

```python
print(
    f"Calling LLM API (structured) with model: {model} → "
    f"{response_schema.__name__} (attempt {attempt + 1}/{max_attempts})",
    flush=True,
)
```

Keep the existing per-retry frequency penalty, presence penalty, and OpenRouter fallback behavior unchanged.

- [ ] **Step 6: Remove temperature from the provider-neutral and direct-provider contracts**

Delete `temperature` from `CompletionRequest` in `src/mudidi/llm/subscriptions/types.py`.

In `ClaudeResearchBackend._build_request_body`, delete temperature range validation, `body["temperature"]`, and the extended-thinking conflict branch. Preserve the reasoning budget selection and `max_tokens > budget_tokens` validation.

In `GoogleGeminiCliBackend._build_request_body`, delete the conditional assignment to `generation["temperature"]`. OpenAI Codex source requires no temperature-body change because it already omits the field.

- [ ] **Step 7: Migrate extraction and stage helper callsites**

In `src/mudidi/llm/pass_1.py`, remove temperature from both dictionary-marker discovery signatures and from each `completion_kwargs` mapping.

In `src/mudidi/llm/pass_2.py`, remove temperature from `extract_mdf` and its `completion_kwargs` mapping.

In `src/mudidi/extraction/llm_two_stage.py`:

- Remove the constructor parameter and `self.temperature` assignment.
- Remove `temperature=` from both Stage 1 structured calls, Stage 2 pass calls, and every agentic evaluator/rewriter call.
- Preserve reasoning effort, token limits, schemas, backend selection, and prompt caching exactly.

In `src/mudidi/cli/extract.py::_build_strategy`, stop reading `args.temperature` and stop passing it to `TwoStageLLMExtraction`. User-facing parser/manifest/banner removal belongs to Task 2.

Migrate every remaining source or test caller found in Step 1. Do not retain an optional parameter or catch unexpected `temperature` in `**kwargs`.

- [ ] **Step 8: Run focused inference tests and verify the original exception is covered**

Run:

```bash
uv run pytest -q \
  tests/llm/test_client_reasoning.py \
  tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_dispatch.py \
  tests/llm/test_subscription_claude_research.py \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_openai_codex.py \
  tests/extraction
```

Expected: all selected tests pass; the Claude extended-thinking request contains `thinking` and no `temperature`; no test expects clamping or a temperature conflict.

- [ ] **Step 9: Commit the request-graph cutover**

```bash
git add \
  src/mudidi/llm/client.py \
  src/mudidi/llm/pass_1.py \
  src/mudidi/llm/pass_2.py \
  src/mudidi/extraction/llm_two_stage.py \
  src/mudidi/cli/extract.py \
  src/mudidi/llm/subscriptions/types.py \
  src/mudidi/llm/subscriptions/claude_research.py \
  src/mudidi/llm/subscriptions/google_gemini_cli.py \
  tests/llm/test_client_reasoning.py \
  tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_dispatch.py \
  tests/llm/test_subscription_claude_research.py \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_openai_codex.py \
  tests/extraction
git commit -m "refactor: use provider temperature defaults"
```

Acceptance: no inference API, request model, extraction strategy, stage helper, or provider body accepts or emits temperature; focused inference tests pass.

---

### Task 2: Remove temperature from configuration and user surfaces

**Files:**
- Modify: `src/mudidi/config/yaml_config.py`
- Modify: `src/mudidi/cli/run.py`
- Modify: `src/mudidi/cli/extract.py`
- Modify: `src/mudidi/web/forms.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `tests/config/test_yaml_config.py`
- Modify: `tests/cli/test_config_execution.py`
- Modify: `tests/llm/test_client_reasoning.py` if CLI help text is asserted there
- Modify: `tests/web/test_app.py`
- Modify: `tests/web/test_run_form.py`
- Modify: affected manifest/resolved-config tests under `tests/extraction/`
- Regenerate: `docs/reference/cli.md`
- Regenerate: `docs/reference/config.md`
- Test: `tests/config/test_docs_reference.py`

**Interfaces:**
- Consumes: Task 1's inference graph, which no longer accepts temperature.
- Produces: `ModelsConfig` and `NewRunForm` without temperature; CLI and dashboard expose no temperature input; manifests and preset form state contain no temperature.

- [ ] **Step 1: Write failing strict-config and CLI tests**

In `tests/config/test_yaml_config.py`, add a strict legacy-field rejection:

```python
def test_yaml_config_rejects_removed_temperature(tmp_path: Path) -> None:
    path = tmp_path / "removed-temperature.yaml"
    path.write_text(
        """
version: 1
kind: inference
input:
  pages: dictionary.pdf
output:
  directory: output
models:
  temperature: 0.1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="temperature"):
        load_yaml_config(path)
```

In `tests/cli/test_config_execution.py`, add:

```python
def test_run_parser_rejects_removed_temperature_option() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--temperature", "0.1"])
```

Assert the exit code follows the existing argparse-error test convention if that file already checks it.

- [ ] **Step 2: Write failing web and persistence tests**

Update `tests/web/test_app.py` so the home page asserts:

```python
assert 'name="temperature"' not in response.text
assert 'aria-label="About temperature"' not in response.text
```

Delete the negative-temperature form-validation test because the field no longer exists. Remove `temperature` from otherwise valid multipart form payloads.

In `tests/web/test_run_form.py`, remove the constructor value and old `config.models.temperature` assertion, then assert the serialized configuration lacks the field:

```python
assert "temperature" not in config.models.model_dump()
```

Add or update a preset-state assertion so `_preset_form_state(...)` has no `temperature` key. Update manifest/resolved-config tests to assert both stage manifests and the normalized run configuration omit temperature.

- [ ] **Step 3: Run surface tests and verify they fail for the old field**

Run:

```bash
uv run pytest -q \
  tests/config/test_yaml_config.py \
  tests/cli/test_config_execution.py \
  tests/web/test_app.py \
  tests/web/test_run_form.py \
  tests/extraction
```

Expected: failures identify the existing schema field, CLI option, dashboard control, preset state, or manifest entry. Do not proceed on fixture or syntax errors.

- [ ] **Step 4: Remove the typed configuration and forwarding field**

Delete `temperature` from `ModelsConfig` in `src/mudidi/config/yaml_config.py` and from `NewRunForm` plus `NewRunForm.to_config()` in `src/mudidi/web/forms.py`.

Delete `temperature=models.temperature` from the namespace built in `src/mudidi/cli/run.py`.

Because both Pydantic models retain `extra="forbid"`, legacy YAML and submitted form data fail explicitly without a custom migration branch.

- [ ] **Step 5: Remove CLI, manifest, and terminal output surfaces**

In `src/mudidi/cli/extract.py`:

- Delete the `--temperature` argument registration.
- Delete `temperature` from `_build_stage1_manifest` and `_build_stage2_manifest`.
- Change the run banner to retain strategy, models, stage, and overwrite only:

```python
print(
    f"Strategy: {args.strategy} | Models: {args.stage_models.summary()} | "
    f"Stage: {args.stage} | Overwrite: {args.overwrite}"
)
```

Search this file for `args.temperature` and confirm no occurrence remains.

- [ ] **Step 6: Remove dashboard control and preset restoration**

Delete the complete Temperature `<label>` block from `src/mudidi/web/templates/home.html`. Do not replace it with a hidden input.

Delete the `temperature` entry from `_preset_form_state` in `src/mudidi/web/app.py`. Remove any error-label mapping or submitted-value restoration dedicated to temperature if present. Keep neighboring batch-size and reasoning controls unchanged.

- [ ] **Step 7: Regenerate references and update assertions**

Run:

```bash
uv run python scripts/generate_docs_reference.py
```

Confirm `docs/reference/cli.md` contains no `--temperature` option and `docs/reference/config.md` contains no `models.temperature` field in inference, benchmark, or sweep templates.

Run:

```bash
uv run python scripts/generate_docs_reference.py --check
uv run pytest -q tests/config/test_docs_reference.py
```

Expected: generated references are current and their tests pass.

- [ ] **Step 8: Run focused configuration, CLI, web, and extraction tests**

```bash
uv run pytest -q \
  tests/config \
  tests/cli \
  tests/web/test_app.py \
  tests/web/test_run_form.py \
  tests/extraction
```

Expected: all selected tests pass. Review every remaining `temperature` occurrence in product code, tests, generated references, and examples; only historical design/plan explanation may retain the word.

- [ ] **Step 9: Commit the surface cutover**

```bash
git add \
  src/mudidi/config/yaml_config.py \
  src/mudidi/cli/run.py \
  src/mudidi/cli/extract.py \
  src/mudidi/web/forms.py \
  src/mudidi/web/app.py \
  src/mudidi/web/templates/home.html \
  tests/config/test_yaml_config.py \
  tests/cli/test_config_execution.py \
  tests/web/test_app.py \
  tests/web/test_run_form.py \
  tests/extraction \
  docs/reference/cli.md \
  docs/reference/config.md
git commit -m "refactor: remove temperature configuration"
```

Acceptance: no production configuration, CLI, browser, preset, manifest, or generated reference accepts, persists, displays, or restores temperature; focused surface tests pass.

---

### Task 3: Verify the clean cutover and reproduce the original run

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-provider-default-temperature-design.md` (status only after verification)
- Verify: all files changed in Tasks 1 and 2
- Runtime input: `/private/tmp/mudidi-subscription-try/runs/run-f411d6cc6481/inputs/pages/Carolinian-English-Dictionary.pdf`

**Interfaces:**
- Consumes: Tasks 1 and 2 complete.
- Produces: generated-reference proof, full-suite proof, browser-surface proof, and an authenticated one-page Claude regression run.

- [ ] **Step 1: Run exhaustive source and interface checks**

Use repository search for `temperature`, `temperature=`, `args.temperature`, `models.temperature`, `request.temperature`, and `name="temperature"` across `src`, `tests`, `examples`, and `docs/reference`. Inspect every result. Runtime/config/interface occurrences are defects; historical statements in the approved spec and implementation plan are expected.

Run LSP diagnostics on every changed Python file. If the server remains unavailable, run the configured Ruff lint check instead:

```bash
uv run ruff check \
  src/mudidi/llm/client.py \
  src/mudidi/llm/pass_1.py \
  src/mudidi/llm/pass_2.py \
  src/mudidi/extraction/llm_two_stage.py \
  src/mudidi/cli/extract.py \
  src/mudidi/cli/run.py \
  src/mudidi/config/yaml_config.py \
  src/mudidi/web/forms.py \
  src/mudidi/web/app.py \
  src/mudidi/llm/subscriptions/types.py \
  src/mudidi/llm/subscriptions/claude_research.py \
  src/mudidi/llm/subscriptions/google_gemini_cli.py
```

Do not run `ruff format` across legacy files; it produces unrelated churn in this branch. Apply only targeted formatting required by diagnostics.

- [ ] **Step 2: Run generated-reference and full-suite verification**

```bash
uv run python scripts/generate_docs_reference.py --check
uv run pytest -q
```

Expected: reference check exits zero and the complete suite reports zero failures. Read the complete output and record pass/skip/warning counts.

- [ ] **Step 3: Verify the dashboard surface in Chromium**

Restart the managed dashboard `mudidi-subscription-dashboard` and browser-drive `http://127.0.0.1:56781/`. Confirm the Model step contains reasoning and batch-size controls but no Temperature label, help button, input, or hidden field. Inspect submitted form data or the prepared run configuration and confirm no temperature key is serialized.

- [ ] **Step 4: Exercise the original authenticated Claude regression**

Through the dashboard, submit a fresh overwrite run with:

- input PDF: `/private/tmp/mudidi-subscription-try/runs/run-f411d6cc6481/inputs/pages/Carolinian-English-Dictionary.pdf`
- dictionary pages: `34`
- output directory: `/Users/davidsamuel/Documents/MUDIDI-runs`
- authentication: subscription / Claude
- pipeline: complete two-stage run
- model for all stages: `anthropic/claude-sonnet-4-6`
- reasoning: low for Stage 1 and both Stage 2 passes
- agentic verification: disabled
- batch size: `1`
- output policy: overwrite

Observe the actual worker log. Acceptance for the reported regression:

- The run reaches the provider request instead of raising `Claude extended thinking requires temperature 1`.
- The run banner and resolved configuration contain no temperature value.
- Stage 1 finishes successfully; if a later provider/content error occurs, report it separately and do not misstate the temperature regression as unresolved.
- No credential, authorization code, state, PKCE verifier, or token appears in browser output, worker logs, or the report.

- [ ] **Step 5: Request final code review and address findings**

Dispatch a read-only reviewer over the complete implementation range from `43dfe97` through the current HEAD and working tree. Require review of clean-cutover completeness, missed callsites, strict legacy rejection, provider request omission, Claude-thinking regression coverage, web/CLI removal, generated docs, and secret safety. Fix every Critical or Important finding and rerun the affected checks.

- [ ] **Step 6: Mark the spec implemented and commit final evidence metadata**

After Steps 1-5 pass, change the design header to:

```markdown
> **Status:** Implemented and verified.
```

Run:

```bash
uv run pytest -q tests/config/test_docs_reference.py
```

Then commit:

```bash
git add docs/superpowers/specs/2026-09-13-provider-default-temperature-design.md
git commit -m "docs: mark provider-default temperature implemented"
```

- [ ] **Step 7: Confirm clean repository state**

```bash
git status --short
```

Expected: no output. Report the focused test results, full-suite counts, generated-reference check, browser verification, original one-page run result, review verdict, and all implementation commit SHAs.

Acceptance: the clean cutover is complete end to end; the original authenticated Claude page-34 run no longer fails because MUDIDI supplied temperature `0.1`; all verification is fresh and the branch is clean.
