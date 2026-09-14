# Google and Claude Subscription Login Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Google and Claude subscription login failures actionable in the local dashboard and verify configured login initiation without exposing OAuth secrets.

**Architecture:** Preserve the existing provider-specific OAuth adapters and shared local-only lifecycle routes. The web layer will translate only known, safe configuration/policy reasons into actionable messages, while the browser client will display the server's safe JSON message instead of replacing it with a generic failure. Provider credentials, authorization codes, PKCE state, and full authorization URLs remain server-held; the browser receives only a relative `launch_url` and follows a same-origin, no-store local redirect to the provider.

**Tech Stack:** Python 3.11, FastAPI, vanilla browser JavaScript, pytest, Node.js test harnesses, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md`

## Global Constraints

- Keep subscription routing local-only and separate from API-key billing.
- Google OAuth registration remains deployment-local through `MUDIDI_GOOGLE_OAUTH_CLIENT_ID`; never hardcode a client registration or read Google CLI/browser credential files.
- Claude remains explicitly research-only and requires `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1`.
- Lifecycle responses may contain only safe status, category, actionable configuration text, and the relative local `launch_url`/`expires_at` fields; never include tokens, authorization codes, PKCE values, or full authorization URLs.
- Preserve the existing popup gesture for successful login flows; close the temporary popup when login initiation fails.
- No changes to provider adapters, OAuth transport, extraction behavior, or API-key routing unless a focused test demonstrates a necessary contract adjustment.
- Authenticated live OAuth completion is not part of the default test suite; configured login initiation is sufficient for local verification.

---

### Task 1: Expose safe actionable lifecycle guidance

**Files:**
- Modify: `src/mudidi/web/app.py:594-623` (`subscription_error_message`)
- Test: `tests/web/test_subscription_routes.py` near the existing lifecycle error tests

**Interfaces:**
- Consumes: `SubscriptionError.metadata["reason"]` and `SubscriptionProvider`.
- Produces: The existing `message` field in `POST /subscriptions/{provider}/login` error JSON; successful login JSON returns only safe fields plus relative `launch_url` and `expires_at`.

- [ ] **Step 1: Write the failing route tests**

Add a fake backend whose `begin_login()` raises each provider error while retaining a safe metadata reason:

```python
class _LoginErrorBackend(_FakeBackend):
    def __init__(self, provider: SubscriptionProvider, error: SubscriptionError) -> None:
        super().__init__(provider)
        self.error = error

    def begin_login(self, redirect_uri: str) -> SubscriptionLoginTransaction:
        del redirect_uri
        raise self.error
```

Add tests that assert the route preserves the safe HTTP category and gives setup-specific guidance:

```python
def test_google_login_reports_missing_oauth_client_configuration(tmp_path: Path) -> None:
    backend = _LoginErrorBackend(
        SubscriptionProvider.GOOGLE,
        SubscriptionAuthError(
            "Google OAuth client configuration is unavailable",
            provider=SubscriptionProvider.GOOGLE,
            metadata={"reason": "oauth_client_configuration_missing"},
        ),
    )
    response = TestClient(
        _app(tmp_path, backends={SubscriptionProvider.GOOGLE: backend})
    ).post("/subscriptions/google/login")

    assert response.status_code == 409
    assert response.json()["category"] == "authentication"
    assert "MUDIDI_GOOGLE_OAUTH_CLIENT_ID" in response.json()["message"]
    assert "authorization_url" not in response.json()


def test_claude_login_reports_required_research_opt_in(tmp_path: Path) -> None:
    backend = _LoginErrorBackend(
        SubscriptionProvider.CLAUDE,
        SubscriptionPolicyError(
            "Claude subscription routing requires "
            "MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1",
            provider=SubscriptionProvider.CLAUDE,
            metadata={"reason": "research_opt_in_required"},
        ),
    )
    response = TestClient(
        _app(tmp_path, backends={SubscriptionProvider.CLAUDE: backend})
    ).post("/subscriptions/claude/login")

    assert response.status_code == 403
    assert response.json()["category"] == "policy"
    assert "MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1" in response.json()["message"]
    assert "warning" not in response.json()
```

- [ ] **Step 2: Run the route tests and verify they fail**

Run:

```bash
uv run pytest -q tests/web/test_subscription_routes.py::test_google_login_reports_missing_oauth_client_configuration tests/web/test_subscription_routes.py::test_claude_login_reports_required_research_opt_in
```

Expected: both tests fail because the current web mapping returns generic authentication text instead of provider-specific configuration guidance.

- [ ] **Step 3: Implement the minimal safe reason mapping**

In `subscription_error_message`, read the already-redacted `exc.metadata` reason and handle only these exact cases before the existing category mapping:

```python
reason = str(exc.metadata.get("reason", ""))
if provider is SubscriptionProvider.GOOGLE and reason == "oauth_client_configuration_missing":
    return (
        "Google OAuth is not configured. Set "
        "MUDIDI_GOOGLE_OAUTH_CLIENT_ID and restart MUDIDI."
    )
if provider is SubscriptionProvider.CLAUDE and reason == "research_opt_in_required":
    return (
        "Claude subscription routing requires "
        "MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1; restart MUDIDI."
    )
```

Keep all other provider errors on the existing generic safe messages. Do not return `str(exc)` for arbitrary exceptions.

- [ ] **Step 4: Run the route tests and verify they pass**

Run the same two pytest nodeids. Expected: both pass with status, category, and actionable message assertions intact.

- [ ] **Step 5: Commit the server-message change**

```bash
git add src/mudidi/web/app.py tests/web/test_subscription_routes.py
git commit -m "Improve subscription login configuration guidance"
```

---

### Task 2: Display safe login-initiation errors in the browser

**Files:**
- Modify: `src/mudidi/web/static/app.js:1552-1605` (subscription login click handler)
- Test: `tests/web/test_subscription_routes.py` with the existing Node-based JavaScript harness style

**Interfaces:**
- Consumes: JSON responses from `POST /subscriptions/{provider}/login` with a relative same-origin `launch_url` and optional safe `message` text.
- Produces: The existing `[data-subscription-action-status]` live region text and popup lifecycle behavior.

 - [ ] **Step 1: Write the failing browser behavior test**

Add a small pure helper beside the subscription-card handler so the browser
uses the server's safe message consistently:

```javascript
const subscriptionLoginErrorMessage = (
  payload,
  fallback = "Subscription login failed.",
) => {
  const message = payload?.message;
  return typeof message === "string" && message.trim() ? message : fallback;
};
```

Add a Node harness that extracts this helper from `app.js`, evaluates it, and
asserts both the server-message and fallback behavior:

```javascript
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("const subscriptionLoginErrorMessage");
const end = source.indexOf("\n};", start) + 3;
const functionSource = source
  .slice(start, end)
  .replace(
    "const subscriptionLoginErrorMessage",
    "globalThis.subscriptionLoginErrorMessage",
  );
const context = vm.createContext({});
vm.runInContext(functionSource, context);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const providerMessage = (
  "Google OAuth is not configured. Set "
  + "MUDIDI_GOOGLE_OAUTH_CLIENT_ID and restart MUDIDI."
);
assert(
  context.subscriptionLoginErrorMessage({message: providerMessage}) === providerMessage,
  "server login guidance should be displayed",
);
assert(
  context.subscriptionLoginErrorMessage({message: "   "}) === "Subscription login failed.",
  "blank server messages should use the safe fallback",
);
```

The test must fail against the current source because the handler still expects
the provider authorization URL instead of the local launch URL. The handler test
setup must also assert that the existing catch path continues to close a popup
after a failed initiation.

 - [ ] **Step 2: Run the browser behavior test and verify it fails**

Run:

```bash
uv run pytest -q tests/web/test_subscription_routes.py::test_subscription_login_displays_safe_server_message_and_closes_popup
```

Expected: Node exits non-zero because the handler cannot yet navigate the
relative local `launch_url`.

 - [ ] **Step 3: Parse safe JSON errors and preserve popup cleanup**

Add the helper shown above before the subscription-card loop. Replace the login
handler's response/error section with this complete behavior:

```javascript
const payload = await response.json().catch(() => null);
if (!response.ok) {
  throw new Error(subscriptionLoginErrorMessage(payload));
}
if (typeof payload?.launch_url !== "string" || !payload.launch_url) {
  throw new Error("Login URL unavailable.");
}
const launchUrl = new URL(
  payload.launch_url,
  window.location.origin,
);
if (!["http:", "https:"].includes(launchUrl.protocol)) {
  throw new Error("Login URL is not a web URL");
}
if (launchUrl.origin !== window.location.origin) {
  throw new Error("Login URL must stay on this server");
}
popup.location.href = launchUrl.href;
if (actionStatus) actionStatus.textContent = "Complete login in the browser window…";
await waitForSubscriptionLogin(card, provider);
```

Change the catch binding to `catch (error)` and set the live status from the
safe caught error:

```javascript
if (actionStatus) {
  actionStatus.textContent = error instanceof Error && error.message
    ? error.message
    : "Subscription login failed.";
}
```

Keep the existing `popup.close()` call before that status update. Do not put
the authorization URL, transaction handle, callback code, or PKCE values into
the status element.

 - [ ] **Step 4: Run the browser behavior test and verify it passes**

Run the same focused pytest nodeid. Expected: PASS, including server-message
display, safe fallback, and popup cleanup assertions.

 - [ ] **Step 5: Commit the browser change**

```bash
git add src/mudidi/web/static/app.js tests/web/test_subscription_routes.py
git commit -m "Show actionable subscription login errors"
```


---

### Task 3: Verify configured Google and Claude login initiation

**Files:**
- Modify: none
- Test: existing route and provider lifecycle tests

**Interfaces:**
- Consumes: `MUDIDI_GOOGLE_OAUTH_CLIENT_ID` and `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH` in the dashboard process environment.
- Produces: A pending login JSON response with only safe fields and a relative `launch_url`; the browser follows that local route, which returns the provider authorization URL only in a no-store redirect. Verification output records only status and whether `launch_url` was present.

- [ ] **Step 1: Run provider lifecycle regression tests**

Run:

```bash
uv run pytest -q tests/web/test_subscription_routes.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Start an isolated configured dashboard**

Start the current worktree dashboard on a separate loopback port and data directory with:

: "${MUDIDI_GOOGLE_OAUTH_CLIENT_ID:?MUDIDI_GOOGLE_OAUTH_CLIENT_ID must be set to the deployment public client ID}"
MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1 \
uv run python -m mudidi.cli.main web \
  --host 127.0.0.1 \
  --port 56782 \
  --data-dir /private/tmp/mudidi-subscription-configured
```

Use the managed process tool, wait for readiness, and do not print or persist tokens.

- [ ] **Step 3: Probe both login endpoints without following external authorization**

For each provider, POST `/subscriptions/{provider}/login`, then record only:

```text
provider, HTTP status, payload status, category, has_launch_url
```

Expected for both configured providers: HTTP 200, `status=pending`, `has_launch_url=true`, and no authorization URL or transaction handle in JSON. Immediately call the matching logout endpoint or stop the isolated process so pending transactions and callback listeners do not remain active.

- [ ] **Step 4: Run the complete test suite**

```bash
uv run pytest -q
```

Expected: zero failures. Existing warnings may remain if unchanged.

- [ ] **Step 5: Stop the isolated verification dashboard and confirm the main dashboard is ready**

Stop only the temporary configured process. Confirm the existing dashboard process is ready; do not stop unrelated managed processes.

- [ ] **Step 6: Commit any final test-only or documentation adjustment**

```bash
git status --short
git diff --check
```

If the working tree contains only the planned changes and all verification is green, commit them with a focused message. Do not commit credentials, output artifacts, or temporary diagnostic files.
