# Subscription OAuth Callback Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local web and CLI subscription login flows emit provider-registered callback URIs and complete fixed-port callbacks without an OMP/Pi process.

**Architecture:** Keep MUDIDI's dashboard local-only and retain provider-specific callback contracts inside the adapters. OpenAI Codex and Claude use fixed loopback callback authorities required by their provider clients; the web app starts a MUDIDI-owned, loopback-only callback receiver for those pending logins and completes the server-held transaction in the receiver thread. Google keeps a dashboard-hosted loopback callback, but uses the provider's exact root `/oauth2callback` path. The shared receiver gains an explicit fixed-port/localhost mode used only by these provider contracts; its default remains an ephemeral 127.0.0.1 listener.

**Tech Stack:** Python 3.11, FastAPI, stdlib `http.server`/threading, existing PKCE and OAuth helpers, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md`

## Global Constraints

- Local-only; no OMP gateway, broker, Pi process, hosted relay, browser-cookie import, or provider credential-store reuse.
- Every callback validates state; non-container listeners bind to loopback, while Compose's fixed callback bridge is reachable only through host-loopback-published ports and accepts only local/private container-network sources with exact Host/path checks.
- OpenAI and Claude must use their provider-registered callback contracts; Google must use `/oauth2callback`.
- Access/refresh tokens remain in the encrypted MUDIDI subscription store and never enter web responses, callback HTML, worker messages, or logs.
- Existing API-key behavior and default ephemeral OAuth receiver behavior remain unchanged.
- Claude remains explicit research-only and opt-in; Google still fails closed when its deployment-local client ID is absent.
- Use stdlib HTTP primitives; do not add runtime dependencies.
- Compose container mode publishes the fixed provider callback ports on host loopback only, so the in-container listeners remain reachable without exposing them beyond the local machine.

---

### Task 1: Align provider callback contracts and web bridge

**Files:**
- Modify: `compose.yaml`
- Modify: `src/mudidi/llm/subscriptions/oauth.py`
- Modify: `src/mudidi/llm/subscriptions/openai_codex.py`
- Modify: `src/mudidi/llm/subscriptions/google_gemini_cli.py`
- Modify: `src/mudidi/llm/subscriptions/claude_research.py`
- Modify: `src/mudidi/web/app.py`
- Test: `tests/llm/test_subscription_oauth.py`
- Test: `tests/llm/test_subscription_openai_codex.py`
- Test: `tests/llm/test_subscription_google_gemini_cli.py`
- Test: `tests/llm/test_subscription_claude_research.py`
- Test: `tests/web/test_subscription_routes.py`
- Test: `tests/web/test_docker_deployment.py`

**Interfaces:**
- Existing adapters continue to expose `begin_login(redirect_uri)` and `complete_login(code, state, transaction)`.
- Add an optional adapter property named `login_redirect_uri`: OpenAI returns `http://localhost:1455/auth/callback`, Claude returns `http://localhost:54545/callback`, and Google has no fixed property so the web app supplies a loopback URI using the dashboard's bound port and the exact `/oauth2callback` path.
- Extend `LoopbackOAuthReceiver` with an explicit opt-in for a registered fixed port and redirect hostname while keeping `port=0` and `127.0.0.1` as the default. Reject fixed ports unless the opt-in is present.
- The web app stores only pending transaction/receiver handles in process memory. A MUDIDI-owned receiver thread validates the callback, consumes the matching transaction, invokes `complete_login`, and returns static success/failure text; it never serializes credential values.

- [ ] **Step 1: Write failing tests**

Add tests that demonstrate the regression and desired contract:

```python
def _free_loopback_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_fixed_receiver_emits_localhost_registered_uri() -> None:
    port = _free_loopback_port()
    receiver = LoopbackOAuthReceiver(
        expected_state="state-value",
        host="127.0.0.1",
        port=port,
        path="/auth/callback",
        redirect_host="localhost",
        allow_fixed_port=True,
    )
    assert receiver.redirect_uri == f"http://localhost:{port}/auth/callback"
    receiver.close()
```

```python
def test_openai_and_claude_expose_registered_login_redirects() -> None:
    assert OpenAICodexBackend.login_redirect_uri == "http://localhost:1455/auth/callback"
    assert ClaudeResearchBackend.login_redirect_uri == "http://localhost:54545/callback"
```

```python
def test_google_web_login_uses_root_oauth2callback_path(tmp_path: Path) -> None:
    backend = _FakeBackend(SubscriptionProvider.GOOGLE)
    client = TestClient(_app(tmp_path, backends={SubscriptionProvider.GOOGLE: backend}))
    pending = client.post("/subscriptions/google/login")
    query = parse_qs(urlsplit(pending.json()["authorization_url"]).query)
    state = query["state"][0]
    callback = client.get(f"/oauth2callback?code=authorization-code&state={state}")
    assert query["redirect_uri"][0].endswith("/oauth2callback")
    assert callback.json()["status"] == "authenticated"
```

```python
def test_fixed_callback_receiver_completes_web_transaction(tmp_path: Path) -> None:
    port = _free_loopback_port()
    backend = _FakeBackend(
        SubscriptionProvider.OPENAI,
        login_redirect_uri=f"http://localhost:{port}/auth/callback",
    )
    app = _app(tmp_path, backends={SubscriptionProvider.OPENAI: backend})
    client = TestClient(app)
    pending = client.post("/subscriptions/openai/login")
    state = parse_qs(urlsplit(pending.json()["authorization_url"]).query)["state"][0]
    callback_url = (
        f"http://localhost:{port}/auth/callback"
        f"?code=authorization-code&state={state}"
    )
    with urlopen(callback_url, timeout=2) as callback:
        assert callback.status == 200
    assert backend.callback_calls == [("authorization-code", state)]
    assert client.get("/subscriptions/openai/status").json()["authenticated"] is True
```

Keep existing tests proving ordinary receivers require ephemeral ports and fake dashboard-prefixed aliases remain compatible where no fixed adapter URI is advertised.

- [ ] **Step 2: Run focused tests and verify they fail for the callback-contract reason**

Run:

```bash
uv run pytest -q \
  tests/llm/test_subscription_oauth.py \
  tests/llm/test_subscription_openai_codex.py \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_claude_research.py \
  tests/web/test_subscription_routes.py
```

Expected: failures show the current dynamic OpenAI/Claude URI, prefixed Google URI, and absent fixed web callback completion.

- [ ] **Step 3: Implement the shared fixed callback mode**

In `oauth.py`, allow `validate_loopback_redirect_uri` to accept `localhost` as a loopback hostname, add explicit `redirect_host` and `allow_fixed_port` receiver arguments, construct the advertised URI from `redirect_host`, and keep the existing rejection for nonzero ports without `allow_fixed_port=True`. Preserve exact Host/path/state/origin checks and static callback responses.

- [ ] **Step 4: Update provider adapters to use provider contracts**

In OpenAI and Claude, define their fixed registered redirect URI constants, expose `login_redirect_uri`, and pass the fixed port plus `redirect_host="localhost"` and `allow_fixed_port=True` to the default `LoopbackOAuthReceiver`. Keep injected receiver factories untouched for tests. In Google, restrict provider-generated login redirect paths to `/oauth2callback`; retain deployment-local client-ID fail-closed behavior.

- [ ] **Step 5: Add the web callback receiver bridge**

In `app.py`, select an adapter's optional `login_redirect_uri` when present; otherwise build the Google dashboard URI at the exact root `/oauth2callback`. For fixed URIs, create a MUDIDI-owned receiver using the adapter URI, start a daemon waiter before returning the authorization response, and complete the pending transaction through the existing safe lifecycle path. In explicit `container_mode`, permit only the container bridge to bind its container interface and accept private container-network sources; keep the regular route gate local-only with the same explicit container-mode private-source rule. Add root `/oauth2callback` and fixed callback aliases only where needed, preserve exact local-origin/path/state validation, close receivers after completion/timeout/startup failure, and ensure callback responses contain no code, state, or credential values. Update `compose.yaml` to publish only `127.0.0.1:1455:1455` and `127.0.0.1:54545:54545` in addition to the existing dashboard port so a containerized local browser can reach the in-container fixed listeners. Add a container-mode request test that exercises a private gateway client and a rejection test for a public client.

- [ ] **Step 6: Run focused tests and inspect the resulting URL/callback behavior**

Run the focused command from Step 2. Also run a local fake-provider integration test that exercises one actual HTTP callback request and inspect only sanitized URL fields (`provider`, `redirect_uri`, path, and whether state/challenge exist).

- [ ] **Step 7: Run the full test suite and commit**

Run:

```bash
uv run pytest -q
```

Then commit the verified implementation:

```bash
git add src/mudidi/llm/subscriptions/oauth.py \
  src/mudidi/llm/subscriptions/openai_codex.py \
  src/mudidi/llm/subscriptions/google_gemini_cli.py \
  src/mudidi/llm/subscriptions/claude_research.py \
  src/mudidi/web/app.py \
  tests/llm/test_subscription_oauth.py \
  tests/llm/test_subscription_openai_codex.py \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_claude_research.py \
  tests/web/test_subscription_routes.py
 git commit -m "fix: align subscription OAuth callbacks"
```

Acceptance: provider URLs match their registered callback contracts; OpenAI and Claude fixed-port callback requests complete web transactions locally and in the loopback-only Compose deployment; Google uses `/oauth2callback`; container-mode dashboard lifecycle requests accept only private gateway clients while non-container requests remain loopback-only; invalid host/path/state callbacks fail closed; fixed-port collisions are typed transport failures and all receiver/transaction cleanup paths are covered; existing API-key and ephemeral receiver tests remain green; no secret material appears in HTTP responses or logs.
