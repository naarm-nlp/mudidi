# Subscription Login Retry Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a closed or abandoned provider OAuth tab safe to retry immediately from the dashboard or another browser.

**Architecture:** The web app owns pending subscription transactions and fixed callback receivers. A new login attempt for a provider supersedes that provider's older pending transactions, closes their receivers, and then binds the new callback listener. The existing frontend may continue closing an empty popup when the login endpoint rejects; successful retries now receive a usable authorization URL instead of a stale-port failure.

**Tech Stack:** Python 3.11, FastAPI, stdlib `LoopbackOAuthReceiver`, pytest, existing Node-based static JavaScript harnesses.

**Spec:** User report: closing the first provider OAuth tab must not make later provider login attempts fail or auto-close; the next login attempt must be retryable immediately without exposing credentials or callback state.

## Global Constraints

- Superseding a transaction is limited to the same subscription provider.
- Close removed receivers outside the transaction lock; never hold the lock while shutting down a socket.
- Preserve state/PKCE validation, local-only route gates, fixed provider callback URIs, and existing timeout cleanup.
- Do not return authorization codes, OAuth state, tokens, or raw exception text in responses.
- Existing dynamic/fake backend lifecycle behavior and API-key authentication remain unchanged.
- Use only the standard library and existing project dependencies.

---

### Task 1: Add a failing retry regression

**Files:**
- Modify: `tests/web/test_subscription_routes.py` near the existing fixed callback lifecycle tests.

**Interfaces:**
- Consumes: `create_app`, `_FakeBackend`, `_free_loopback_port`, `app.state.subscription_transactions`, and `app.state.subscription_receivers`.
- Produces: A deterministic test proving a second login for one provider can replace the first fixed callback receiver and bind the same port.

- [ ] **Step 1: Write the failing test**

Add `test_fixed_callback_login_retry_replaces_abandoned_transaction`:

1. Allocate one free loopback port.
2. Build an OpenAI `_FakeBackend` with `login_redirect_uri=f"http://localhost:{port}/auth/callback"`.
3. Create the app/client with that backend.
4. POST `/subscriptions/openai/login`; assert HTTP 200 and capture its transaction handle and receiver.
5. POST the same login route again without completing the first callback; assert HTTP 200, capture the second handle/receiver, and assert the first handle/receiver was removed and closed while exactly one current transaction/receiver remains.
6. In a `finally` block close any remaining receivers.

The test must use a real fixed `LoopbackOAuthReceiver` so successful replacement proves the socket was released, not just that a map entry changed.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest -q tests/web/test_subscription_routes.py::test_fixed_callback_login_retry_replaces_abandoned_transaction
```

Expected: FAIL because the second login returns the existing fixed-port transport error instead of replacing the abandoned receiver.

- [ ] **Step 3: Commit the regression test**

```bash
git add tests/web/test_subscription_routes.py
git commit -m "test: cover retry after abandoned subscription login"
```

### Task 2: Supersede stale provider transactions

**Files:**
- Modify: `src/mudidi/web/app.py` around `register_subscription_receiver`, `remove_subscription_receiver`, and `subscription_login`.
- Test: `tests/web/test_subscription_routes.py` from Task 1.

**Interfaces:**
- Consumes: The existing `app.state.subscription_transaction_lock`, provider/transaction maps, and receiver `close()` seam.
- Produces: A helper that removes all pending transactions for one provider and closes their receivers, plus a login route that invokes it after creating a valid replacement transaction and before binding the replacement receiver.

- [ ] **Step 1: Implement the minimal cancellation helper**

Add a helper with this behavior:

```python
def supersede_subscription_transactions(provider: SubscriptionProvider) -> None:
    receivers: list[object] = []
    with app.state.subscription_transaction_lock:
        for handle, (pending_provider, _transaction, _expires_at) in list(
            app.state.subscription_transactions.items()
        ):
            if pending_provider is not provider:
                continue
            del app.state.subscription_transactions[handle]
            receiver = app.state.subscription_receivers.pop(handle, None)
            if receiver is not None:
                receivers.append(receiver)
    for receiver in receivers:
        close_receiver = getattr(receiver, "close", None)
        if callable(close_receiver):
            try:
                close_receiver()
            except Exception:
                pass
```

Use the project’s existing cleanup naming/style. Call it in `subscription_login` only after `backend.begin_login(redirect_uri)` returns a valid `SubscriptionLoginTransaction`, and before `save_subscription_transaction`/`start_subscription_receiver`. This makes the explicit new login request the replacement boundary while leaving failed initial transaction creation untouched.

- [ ] **Step 2: Run the regression test to verify it passes**

Run:

```bash
uv run pytest -q tests/web/test_subscription_routes.py::test_fixed_callback_login_retry_replaces_abandoned_transaction
```

Expected: PASS; the second request returns a pending authorization URL and the old fixed listener is closed.

- [ ] **Step 3: Commit the implementation**

```bash
git add src/mudidi/web/app.py tests/web/test_subscription_routes.py
git commit -m "fix: allow subscription login retries after abandoned tabs"
```

### Task 3: Verify live retry behavior

**Files:**
- Modify: none unless verification finds a regression.

- [ ] **Step 1: Run focused subscription tests**

```bash
uv run pytest -q tests/web/test_subscription_routes.py tests/llm/test_subscription_oauth.py
```

Expected: all tests pass.

- [ ] **Step 2: Run the full suite**

```bash
uv run pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Smoke-test two consecutive live OpenAI login requests**

Start the dashboard on `127.0.0.1:56781`, POST `/subscriptions/openai/login` twice without following either provider URL, and verify both responses are `status=pending`; do not print authorization URLs, state, or codes. Stop/restart the dashboard after the smoke test so no abandoned listener remains.

- [ ] **Step 4: Commit the plan and record verification**

```bash
git add docs/superpowers/plans/2026-09-09-subscription-login-retry.md
git commit -m "docs: plan subscription login retry cleanup"
```

### Task 4: Align current OpenAI Codex authorization metadata

**Files:**
- Modify: `src/mudidi/llm/subscriptions/openai_codex.py` constants used by `build_authorization_url`.
- Test: `tests/llm/test_subscription_openai_codex.py` authorization-parameter assertions.

**Interfaces:**
- Consumes: The existing fixed `http://localhost:1455/auth/callback`, PKCE transaction, and OpenAI authorization endpoint.
- Produces: Current Codex authorization metadata matching the official OpenAI CLI flow: scopes `openid profile email offline_access api.connectors.read api.connectors.invoke` and originator `codex_cli_rs`.

- [ ] **Step 1: Write the failing contract assertion**

Change the existing authorization URL test to require the exact current scope string and `originator == "codex_cli_rs"`, while retaining assertions for client ID, exact callback URI, PKCE S256, state, and no verifier in the authorization request.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest -q tests/llm/test_subscription_openai_codex.py::test_login_builds_pi_codex_authorization_url_and_exchanges_pkce_code
```

Expected: FAIL because the adapter currently sends the legacy four-scope string and `originator=pi`.

- [ ] **Step 3: Implement the metadata cutover**

Update only the provider-private `_SCOPE` and `_ORIGINATOR` constants. Keep token exchange fields unchanged: `grant_type`, the same authorization code, exact fixed redirect URI, client ID, and original PKCE verifier.

- [ ] **Step 4: Run the focused OpenAI tests**

```bash
uv run pytest -q tests/llm/test_subscription_openai_codex.py
```

Expected: all OpenAI adapter tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/mudidi/llm/subscriptions/openai_codex.py tests/llm/test_subscription_openai_codex.py
git commit -m "fix: align OpenAI Codex OAuth metadata"
```

### Task 5: Resolve final lifecycle and request-contract findings

**Files:**
- Modify: `src/mudidi/web/app.py` for in-flight receiver ownership and logout cancellation.
- Modify: `src/mudidi/llm/subscriptions/claude_research.py` for tool/function-call rejection.
- Test: `tests/web/test_subscription_routes.py`, `tests/llm/test_subscription_claude_research.py`.

**Interfaces:**
- Consumes: Existing transaction lock, receiver map, callback completion, logout route, and provider-neutral message validation.
- Produces: Provider-scoped supersession that also handles a receiver already completing a callback; logout that invalidates pending provider callbacks before deleting credentials; Claude translation that rejects assistant/tool-call shapes rather than silently discarding them.

- [ ] **Step 1: Add failing lifecycle/request tests**

Add deterministic tests for:

1. a receiver retained after transaction consumption being closed by a same-provider replacement;
2. logout invalidating a pending fixed callback so a later callback cannot re-authenticate;
3. Claude rejecting an assistant message containing text plus `tool_calls`, `tool_call`, or `function_call`.

- [ ] **Step 2: Run the new tests to verify red**

```bash
uv run pytest -q tests/web/test_subscription_routes.py::test_supersession_closes_inflight_receiver tests/web/test_subscription_routes.py::test_subscription_logout_cancels_pending_callback tests/llm/test_subscription_claude_research.py::test_rejects_mixed_text_and_tool_call_message
```

Expected: FAIL because active receiver ownership is currently tied only to pending transaction entries, logout leaves pending callbacks live, and Claude drops tool fields.

- [ ] **Step 3: Implement minimal lifecycle/request fixes**

Track provider ownership for active receivers independently from transaction entries, close all same-provider receivers outside the transaction lock during supersession, cancel provider transactions/receivers before logout, and reject any tool/function-call key in Claude message history with the existing typed unsupported-request error.

- [ ] **Step 4: Run focused lifecycle tests**

```bash
uv run pytest -q tests/web/test_subscription_routes.py tests/llm/test_subscription_claude_research.py
```

Expected: all focused lifecycle and Claude tests pass without changing API-key behavior.

- [ ] **Step 5: Commit**

```bash
git add src/mudidi/web/app.py src/mudidi/llm/subscriptions/claude_research.py tests/web/test_subscription_routes.py tests/llm/test_subscription_claude_research.py
git commit -m "fix: close stale subscription callback flows"

```
### Task 6: Harden cross-process persistence and receiver startup

**Files:**
- Modify: `src/mudidi/llm/subscriptions/openai_codex.py`, `src/mudidi/llm/subscriptions/google_gemini_cli.py`, and `src/mudidi/llm/subscriptions/claude_research.py`.
- Modify: `src/mudidi/llm/subscriptions/oauth.py`.
- Test: provider adapter tests, `tests/llm/test_subscription_oauth.py`, and focused lifecycle tests.

**Interfaces:**
- Consumes: Each adapter's existing `_provider_operation()` context, two-step `complete_login`, direct `login`, `refresh`, and `logout` paths; the one-shot `LoopbackOAuthReceiver`.
- Produces: Cross-process serialization of login exchange/persistence with refresh/logout, and a receiver whose close is terminal even when it occurs before `receive()` starts its listener.

- [ ] **Step 1: Add failing concurrency tests**

Add deterministic tests that:

1. block a provider refresh/login operation on the store's provider lock and prove the competing login cannot persist until that lock is released, covering OpenAI, Google, and Claude two-step/direct paths through the existing adapter test seams;
2. close a `LoopbackOAuthReceiver` before invoking `receive()` and prove `receive()` returns the existing typed `callback_closed` error without starting a listener or clearing the close state.

- [ ] **Step 2: Run the tests to verify red**

```bash
uv run pytest -q tests/llm/test_subscription_oauth.py::test_receiver_close_before_receive_is_terminal tests/llm/test_subscription_openai_codex.py::test_login_persistence_waits_for_provider_lock tests/llm/test_subscription_google_gemini_cli.py::test_login_persistence_waits_for_provider_lock tests/llm/test_subscription_claude_research.py::test_login_persistence_waits_for_provider_lock
```

Expected: FAIL because adapter login completion/persistence currently bypasses `_provider_operation()` and `receive()` resets the closed receiver state before starting its listener.

- [ ] **Step 3: Implement minimal locking and terminal-close behavior**

Wrap each provider's complete-login exchange/enrichment/save and direct login exchange/save path in its existing `_provider_operation()` context without nesting a second provider lock. Make `LoopbackOAuthReceiver.receive()` check terminal closure before clearing event/error state and synchronize the close/start boundary so a close cannot be erased or followed by listener startup.

- [ ] **Step 4: Run focused concurrency tests**

```bash
uv run pytest -q tests/llm/test_subscription_oauth.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py
```

Expected: all focused OAuth adapter and receiver tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/mudidi/llm/subscriptions/oauth.py src/mudidi/llm/subscriptions/openai_codex.py src/mudidi/llm/subscriptions/google_gemini_cli.py src/mudidi/llm/subscriptions/claude_research.py tests/llm/test_subscription_oauth.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py
git commit -m "fix: serialize subscription OAuth persistence"
```
