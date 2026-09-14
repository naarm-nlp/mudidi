# Claude Policy-Warning Contract Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the blanket Claude policy-warning contract and every rendered or serialized copy while preserving explicit research opt-in and real provider-policy errors.

**Architecture:** Delete `SubscriptionStatus.policy_warning` at the shared contract boundary, then migrate runtime, Claude, CLI, API, form, template, browser, test, and documentation consumers to explicit state. Filter the legacy metadata key at the safe serialization boundary so existing encrypted credentials cannot re-expose it. Keep `research_only`, `opt_in_enabled`, typed `SubscriptionPolicyError`, and the opt-in environment variable.

**Tech Stack:** Python 3.11, Pydantic, FastAPI, Jinja2, vanilla JavaScript/CSS, pytest, Ruff, Chromium browser verification.

**Spec:** `docs/superpowers/specs/2026-09-13-claude-warning-contract-removal-design.md`

## Global Constraints

- `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1` remains required.
- Provider HTTP 403 and provider-reported policy failures remain typed `SubscriptionPolicyError` failures.
- Remove the blanket warning from backend metadata, public models, API payloads, CLI, web pages, tests, and documentation.
- Preserve the substantial concurrent OAuth/transport edits in `claude_research.py` and its focused test file; never stage unrelated hunks.
- Remove obsolete warning constants, properties, fields, branches, DOM nodes, CSS, and documentation mandates; do not leave compatibility aliases.
- Every behavior change follows red-green TDD and each commit stages only files or hunks owned by this plan.

---

### Task 1: Remove the shared warning status contract

**Files:**
- Modify: `src/mudidi/llm/subscriptions/types.py:339-385,604-617`
- Modify: `src/mudidi/llm/client.py:380-440`
- Modify: `src/mudidi/cli/main.py:385-480`
- Test: `tests/llm/test_subscription_types.py:160-200`
- Test: `tests/llm/test_subscription_contract.py:797-819`
- Test: `tests/cli/test_subscription_auth_cli.py:83-120`

**Interfaces:**
- Consumes: `SubscriptionStatus.metadata`, `research_only`, `opt_in_enabled`, `SubscriptionPolicyError`.
- Produces: warning-free `SubscriptionStatus` and explicit opt-in routing with metadata reason `research_opt_in_required`.

- [ ] **Step 1: Write failing shared-contract tests**

Add a status test that proves the removed field is rejected and legacy metadata is filtered:

```python
def test_subscription_status_rejects_removed_warning_and_filters_legacy_metadata() -> None:
    with pytest.raises(ValidationError):
        SubscriptionStatus(
            provider=SubscriptionProvider.CLAUDE,
            policy_warning="legacy warning",
        )

    status = SubscriptionStatus(
        provider=SubscriptionProvider.CLAUDE,
        metadata={"policy_warning": "legacy warning", "opt_in_enabled": False},
    )
    assert "policy_warning" not in status.metadata
    assert "policy_warning" not in status.redacted_metadata
```

Update `test_claude_opt_in_is_enforced_during_runtime_resolution` to assert:

```python
assert raised.value.metadata["reason"] == "research_opt_in_required"
assert status.metadata["research_only"] is True
assert status.metadata["opt_in_enabled"] is False
assert "policy_warning" not in status.model_dump()
assert "policy_warning" not in status.redacted_metadata
```

Extend the CLI status/login test fixture with explicit research metadata and assert `"Policy warning:" not in output`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest -q \
  tests/llm/test_subscription_types.py::test_subscription_status_rejects_removed_warning_and_filters_legacy_metadata \
  tests/llm/test_subscription_contract.py::test_claude_opt_in_is_enforced_during_runtime_resolution \
  tests/cli/test_subscription_auth_cli.py
```

Expected: failures because `policy_warning` remains a model field, metadata preserves the legacy key, runtime reason is `policy_warning`, or CLI still prints it.

- [ ] **Step 3: Implement the shared cutover**

In `types.py`, remove the field and its `redacted_metadata` branch. Filter the exact legacy key case-insensitively before recursively redacting metadata:

```python
if key.casefold() == "policy_warning" or _SECRET_KEY_RE.search(key):
    continue
```

In `client.py`, replace warning-string checks with explicit opt-in state:

```python
research_opt_in_missing = (
    status_metadata.get("research_only") is True
    and status_metadata.get("opt_in_enabled") is False
)
if research_opt_in_missing:
    raise SubscriptionPolicyError(
        "Claude subscription routing requires MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1",
        provider=provider,
        metadata={"reason": "research_opt_in_required"},
    )
```

Apply the same logic after refresh. In `cli/main.py`, remove `policy_warning` from category inference and delete both policy-warning print branches; retain typed category/error rendering.

- [ ] **Step 4: Run tests and lint to verify GREEN**

Run the Step 2 command, then:

```bash
uv run ruff check \
  src/mudidi/llm/subscriptions/types.py src/mudidi/llm/client.py \
  src/mudidi/cli/main.py tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_contract.py tests/cli/test_subscription_auth_cli.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 5: Commit the shared contract cutover**

```bash
git add src/mudidi/llm/subscriptions/types.py src/mudidi/llm/client.py \
  src/mudidi/cli/main.py tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_contract.py tests/cli/test_subscription_auth_cli.py
git commit -m "refactor: remove subscription policy warning contract"
```

### Task 2: Remove Claude warning production and metadata

**Files:**
- Modify: `src/mudidi/llm/subscriptions/claude_research.py:80-120,460-525,1110-1150,1625-1650,1940-1985`
- Test: `tests/llm/test_subscription_claude_research.py:300-325,470-550`

**Interfaces:**
- Consumes: warning-free `SubscriptionStatus` from Task 1.
- Produces: Claude status/errors/credentials with `research_only` and `opt_in_enabled`, no `policy_warning`, and actionable opt-in failure reason `research_opt_in_required`.

- [ ] **Step 1: Rewrite focused tests to specify warning-free behavior**

Rename the opt-in test and assert:

```python
status = backend.status()
assert status.authenticated is False
assert status.metadata["research_only"] is True
assert status.metadata["opt_in_enabled"] is False
assert "policy_warning" not in status.model_dump()

with pytest.raises(SubscriptionPolicyError) as login_error:
    backend.login()
assert "MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1" in str(login_error.value)
assert login_error.value.metadata["reason"] == "research_opt_in_required"
assert "policy_warning" not in login_error.value.metadata
```

For receiver, refresh, transport, and provider-policy normalization tests, replace equality assertions against `backend.policy_warning` with `"policy_warning" not in raised.value.metadata`. Keep provider, category, status, and reason assertions.

- [ ] **Step 2: Run focused Claude tests to verify RED**

Run:

```bash
uv run pytest -q tests/llm/test_subscription_claude_research.py
```

Expected: warning-contract assertions fail until the backend producer is removed. If unrelated concurrent OAuth/transport assertions fail, record them separately and rerun the exact warning-contract nodes:

```bash
uv run pytest -q \
  tests/llm/test_subscription_claude_research.py::test_status_warns_and_login_run_require_explicit_research_opt_in \
  tests/llm/test_subscription_claude_research.py::test_login_rejects_oauth_client_without_json_token_method \
  tests/llm/test_subscription_claude_research.py::test_refresh_rejects_oauth_client_without_json_token_method \
  tests/llm/test_subscription_claude_research.py::test_login_normalizes_shared_receiver_errors_to_claude_policy_metadata \
  tests/llm/test_subscription_claude_research.py::test_refresh_maps_token_endpoint_403_to_claude_policy_error
```

- [ ] **Step 3: Remove warning production surgically**

Delete `_POLICY_WARNING` and `ClaudeResearchBackend.policy_warning`. Change `_require_opt_in()` to:

```python
raise _error(
    SubscriptionPolicyError,
    "Claude subscription routing requires MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1",
    reason="research_opt_in_required",
)
```

Remove `policy_warning` writes from `_error`, `_normalize_shared_error`, `_status_metadata`, credential metadata, and `SubscriptionStatus` construction. Preserve `research_only`, `opt_in_enabled`, credential redaction, and actual provider-policy rejection errors. Explicitly discard a legacy incoming `policy_warning` key when merging shared error or credential metadata.

- [ ] **Step 4: Run focused tests and lint to verify GREEN**

Run the warning-focused Claude node IDs, then the full Claude test file if its concurrent transport work is coherent:

```bash
uv run pytest -q tests/llm/test_subscription_claude_research.py
uv run ruff check src/mudidi/llm/subscriptions/claude_research.py \
  tests/llm/test_subscription_claude_research.py
```

Expected: no warning-contract failures. Report unrelated concurrent failures without modifying their behavior.

- [ ] **Step 5: Stage only warning-removal hunks and commit**

Because both files contain pre-existing uncommitted user work, use interactive staging and inspect the staged diff:

```bash
git add -p src/mudidi/llm/subscriptions/claude_research.py \
  tests/llm/test_subscription_claude_research.py
git diff --cached -- \
  src/mudidi/llm/subscriptions/claude_research.py \
  tests/llm/test_subscription_claude_research.py
git commit -m "refactor: remove Claude warning metadata"
```

Select only hunks that delete or replace warning-contract code and tests. Do not stage OAuth endpoints, callback ports, SSE decoding, compression, billing headers, token refresh, or identity bootstrap changes.

### Task 3: Remove warning payloads and dashboard presentation

**Files:**
- Modify: `src/mudidi/web/app.py:90-105,461-604,670-689,1020-1125,2120-2140,2880-2900`
- Modify: `src/mudidi/web/forms.py:50-65,510-538`
- Modify: `src/mudidi/web/static/app.js:510-525,645-662,1485-1505`
- Modify: `src/mudidi/web/static/app.css:205-218`
- Modify: `src/mudidi/web/templates/home.html:315-365`
- Modify: `src/mudidi/web/templates/review.html:18-34`
- Modify: `src/mudidi/web/templates/run_detail.html:15-24`
- Test: `tests/web/test_run_form.py:590-615`
- Test: `tests/web/test_subscription_routes.py:300-345,770-870,970-1030`
- Test: `tests/web/test_subscription_integration.py:420-455`
- Test: `tests/web/test_run_routes.py`

**Interfaces:**
- Consumes: warning-free status/backend interfaces from Tasks 1-2.
- Produces: subscription JSON, prepared-run summaries, and rendered pages with no disclaimer or warning scaffolding.

- [ ] **Step 1: Write failing web contract tests**

Update the run-form test:

```python
assert "policy_warning" not in summary
assert summary["billing_mode"] == "subscription"
```

Extend subscription route tests:

```python
assert "warning" not in status.json()
assert "data-auth-policy-warning" not in home.text
assert "data-subscription-warning" not in home.text
assert "subscription-policy-warning" not in home.text
```

For login/callback failures, assert the safe `message`, `category`, and status code remain, but `"warning" not in response.json()`. Add Review and run-detail assertions that `policy_warning` and the removed disclaimer are absent.

- [ ] **Step 2: Run web tests to verify RED**

Run:

```bash
uv run pytest -q \
  tests/web/test_run_form.py \
  tests/web/test_subscription_routes.py \
  tests/web/test_subscription_integration.py \
  tests/web/test_run_routes.py
```

Expected: failures from current summary keys, JSON warning keys, and rendered warning elements.

- [ ] **Step 3: Remove web warning flow**

Delete `_CLAUDE_SUBSCRIPTION_WARNING` and `subscription_warning()`. Build policy category from explicit metadata:

```python
if (
    provider is SubscriptionProvider.CLAUDE
    and status.metadata.get("research_only") is True
    and status.metadata.get("opt_in_enabled") is False
    and not status.authenticated
):
    return "policy"
```

Remove `warning` from status, lifecycle error, login, and callback payloads. Remove `policy_warning` from form/app review summaries and run views. Remove the warning keys from `review.html`, the warning paragraphs from `home.html` and `run_detail.html`, the JavaScript constant/query/show-hide logic and card warning update, and `.subscription-policy-warning` CSS if no remaining template uses it.

Keep generic login error messages and the `Policy opt-in required` state label because they represent actionable disabled configuration, not the removed disclaimer.

- [ ] **Step 4: Run web tests and lint to verify GREEN**

Run the Step 2 command, then:

```bash
uv run ruff check src/mudidi/web/app.py src/mudidi/web/forms.py \
  tests/web/test_run_form.py tests/web/test_subscription_routes.py \
  tests/web/test_subscription_integration.py tests/web/test_run_routes.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 5: Commit web removal**

```bash
git add src/mudidi/web/app.py src/mudidi/web/forms.py \
  src/mudidi/web/static/app.js src/mudidi/web/static/app.css \
  src/mudidi/web/templates/home.html src/mudidi/web/templates/review.html \
  src/mudidi/web/templates/run_detail.html tests/web/test_run_form.py \
  tests/web/test_subscription_routes.py tests/web/test_subscription_integration.py \
  tests/web/test_run_routes.py
git commit -m "fix: remove Claude warnings from dashboard"
```

### Task 4: Remove documentation mandates and stale tests

**Files:**
- Modify: `docs/subscription-routing-research.md:1-25`
- Modify: `docs/superpowers/plans/2026-09-09-direct-subscription-routing.md:15-90`
- Modify: `docs/superpowers/plans/2026-09-09-subscription-oauth-callback-fix.md:15-25`
- Modify: `docs/superpowers/plans/2026-09-12-google-claude-login-diagnostics.md:10-25,65-80`
- Modify: `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md:35-48`
- Modify: `tests/config/test_subscription_config.py:215-235`

**Interfaces:**
- Consumes: completed runtime and UI cutover.
- Produces: documentation and fixtures that describe opt-in without mandating or carrying the removed disclaimer.

- [ ] **Step 1: Remove stale documentation and fixture fields**

Delete the blanket warning block from `subscription-routing-research.md`. Rewrite historical mandates narrowly, for example:

```markdown
Claude subscription routing remains behind the explicit
`MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1` opt-in.
```

Remove `policy_warning=None` from test status constructors and delete warning-only assertions. Preserve tests for opt-in, typed policy rejection, secret safety, and local-only routing.

- [ ] **Step 2: Verify the repository warning sweep**

Use the harness Grep tool over `src`, `tests`, and `docs` with this case-insensitive regex:

```text
policy_warning|_POLICY_WARNING|_CLAUDE_SUBSCRIPTION_WARNING|data-auth-policy-warning|data-subscription-warning|subscription-policy-warning|not Anthropic-approved|compatibility warranty
```

Expected: no production/test occurrence. The approved design and implementation plan may mention removed symbol names only as migration documentation; no file repeats the disclaimer as a warning.

- [ ] **Step 3: Run documentation-adjacent and config tests**

```bash
uv run pytest -q tests/config tests/cli/test_subscription_auth_cli.py
```

Expected: pass.

- [ ] **Step 4: Commit documentation and fixture cleanup**

```bash
git add docs/subscription-routing-research.md docs/superpowers \
  tests/config tests/cli/test_subscription_auth_cli.py
git commit -m "docs: remove Claude policy warning mandate"
```

### Task 5: Verify the end-to-end clean cutover

**Files:**
- Verify only; modify tests solely if they expose a missed warning-removal contract.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: browser, test, lint, review, and dashboard-runtime evidence.

- [ ] **Step 1: Run focused regression suites**

```bash
uv run pytest -q \
  tests/llm/test_subscription_types.py \
  tests/llm/test_subscription_contract.py \
  tests/llm/test_subscription_claude_research.py \
  tests/cli/test_subscription_auth_cli.py \
  tests/web/test_run_form.py \
  tests/web/test_subscription_routes.py \
  tests/web/test_subscription_integration.py \
  tests/web/test_run_routes.py
```

Expected: pass, except any separately recorded concurrent Claude transport failures that are outside the warning contract.

- [ ] **Step 2: Run complete lint and tests**

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: pass. If concurrent Claude work remains inconsistent, run and report the complete suite with only its exact affected tests excluded; never conceal the full-suite failures.

- [ ] **Step 3: Browser-verify every dashboard surface**

Restart the persistent dashboard, then use Chromium to inspect:

1. New Run Model step with subscription mode and Claude selected.
2. Claude subscription credential card.
3. Review page for a Claude subscription run.
4. Run-detail page for that run.

Assert the disclaimer text and all three warning selectors are absent. Confirm provider selection, login/logout buttons, review continuation, and run metadata still render.

- [ ] **Step 4: Request independent review**

Ask a reviewer to inspect the final diff for warning leakage, stale metadata, weakened opt-in enforcement, policy-error regression, concurrent-work loss, and dead warning scaffolding. Resolve every evidence-backed finding and rerun affected checks.

- [ ] **Step 5: Final commit and dashboard reload**

If review fixes remain, stage only those hunks and commit:

```bash
git commit -m "fix: complete Claude warning cutover"
```

Restart `mudidi-subscription-dashboard`, verify readiness at `http://localhost:56781/`, and report all remaining unrelated worktree changes without staging them.
