# Task 5 Report: Anthropic Claude Research-Only Subscription Adapter

## Scope

Implemented the opt-in direct Claude.ai subscription adapter and its focused fake-transport coverage:

- `src/mudidi/llm/subscriptions/claude_research.py`
- `tests/llm/test_subscription_claude_research.py`
- `docs/subscription-routing-research.md`
- `src/mudidi/llm/subscriptions/oauth.py` (minimal bounded JSON token-request helper required by the researched Claude token endpoint)
- `tests/llm/test_subscription_oauth.py` (JSON token-request regression coverage)

The adapter is isolated from the normal API-key/LiteLLM path. It does not read or set provider API-key environment variables, access provider keychains/browser cookies/CLI credential stores, invoke a gateway or hosted relay, or fall back to another provider or API-key route.

## Implementation

### Explicit opt-in and policy boundary

- `ClaudeResearchBackend` requires either `research_opt_in=True` (or its explicit test seam alias) or the exact environment value `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1`.
- Other environment values, including `true`, remain disabled; an explicit false flag overrides the environment.
- `status()` is safe and unauthenticated while disabled, includes `research_only`, opt-in state, and the mandatory policy warning, and does not load credentials.
- Login, refresh, logout, completion, and structured completion fail closed with typed policy errors until opt-in is enabled.
- Provider policy responses (HTTP 403 or policy error payloads) become typed `SubscriptionPolicyError` values with the warning in metadata.

### Researched Claude OAuth flow

- Module-private researched values are the Claude.ai authorization endpoint, Platform token endpoint, Anthropic Messages endpoint, public OAuth client id, full Claude Code scope set, and `/callback` redirect path.
- Uses the shared PKCE challenge, ephemeral `127.0.0.1` loopback receiver, callback state validation, and encrypted subscription store.
- Authorization-code and refresh-token requests use the shared bounded JSON OAuth helper. The exchange body carries `grant_type`, `client_id`, code/refresh token, redirect URI, code verifier, and callback state as applicable; token responses retain refresh-token rotation and expiry metadata.
- OAuth access tokens are sent to Messages as `Authorization: Bearer ...`, never as `x-api-key`. The Messages request includes the researched Claude Code headers and prepends the Claude Code identity system block.
- The shared `OAuthHttpClient` keeps its existing form-urlencoded methods unchanged and adds `request_json_token`, reusing HTTPS allowlists, no-redirect validation, response-size limits, JSON parsing, and typed secret-safe token validation.

### Messages-compatible boundary

- Translates normalized system/developer, user, and text-only assistant history to Anthropic Messages content.
- Supports validated base64/HTTPS image blocks, researched extended-thinking budgets, temperature/max-token controls, and structured JSON-schema output configuration.
- Rejects unsupported message/content/cache/schema requests before transport.
- Normalizes visible text, model, stop reason, and input/output/total/cached/reasoning usage.
- Rejects malformed, unsupported, thought-only, invalid structured, and schema-mismatched responses.
- Performs at most one refresh after a 401 and retries once with the rotated credential; a second 401 becomes typed expired-session failure. No fallback is attempted.
- Status, errors, representations, and metadata redact access/refresh token material and secret-bearing identity fields.

## Test coverage

The Claude-focused tests cover:

- Exact research opt-in semantics and policy warning status/error behavior.
- Claude.ai authorization URL values, PKCE S256 parameters, loopback redirect, callback state rejection, and token exchange/refresh endpoint/body behavior.
- JSON token exchange and refresh through the default shared OAuth transport, including refresh-token rotation.
- Messages URL, Bearer auth, researched headers, identity system block, text/system/user/assistant/image translation, thinking, temperature, max tokens, and usage normalization.
- Structured JSON-schema request/response translation and validation.
- Exactly-one refresh retry, second-401 expiry, typed policy/auth/transport/unsupported errors, and secret-safe outputs.
- API-key environment values are not surfaced or used by the adapter.

The shared OAuth regression test covers bounded JSON token POST encoding, content negotiation, timeout propagation, response validation, and token parsing while the pre-existing form exchange tests remain in place.

## TDD evidence

### Initial RED

After writing the Claude-focused tests and before creating the production adapter, the required command failed during collection:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py
```

Observed result:

```text
ImportError while importing test module ...
ModuleNotFoundError: No module named 'mudidi.llm.subscriptions.claude_research'
1 error in 0.20s
```

### JSON transport RED

After adding the researched JSON token-request regression, before adding the shared helper, the focused test failed as expected:

```text
uv run pytest -q tests/llm/test_subscription_oauth.py -k json_token_requests
```

Observed result:

```text
AttributeError: 'OAuthHttpClient' object has no attribute 'request_json_token'
1 failed, 16 deselected in 0.12s
```

After changing the Claude transport assertion to JSON, before dispatching the adapter through the helper, the Claude regression failed as expected with `json.decoder.JSONDecodeError` while the implementation still sent form data.

### GREEN

After implementing the adapter and shared JSON helper:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py
```

Observed result:

```text
..................                                                       [100%]
18 passed in 0.11s
```

The narrow shared OAuth suite also passed:

```text
uv run pytest -q tests/llm/test_subscription_oauth.py
```

Observed result:

```text
.................                                                        [100%]
17 passed in 0.19s
```

No formatter, linter, build, or project-wide test suite was run.

## Concerns and policy caveats

- This is a research implementation only. It is not Anthropic-approved, carries no compatibility warranty, and is not a claim that Claude subscription routing is permitted under any plan or provider terms.
- The authorization/token endpoints, scopes, Claude Code headers, identity prompt, Messages schema, and response behavior are provider-owned and may change or be disabled without notice.
- The shared loopback flow intentionally uses an ephemeral `127.0.0.1` callback and in-memory PKCE material; callers remain responsible for local machine security, current provider terms, account permission, and safe encrypted-store handling.
- Tests use fake OAuth, browser, callback, and HTTP seams only; no live provider account or network request was used.
- Removing this experiment means disabling provider selection and the opt-in flag, deleting the adapter/focused tests/documentation, and deleting the Claude record through the separate subscription store logout path; the API-key path does not require migration.

## Review follow-up

The review pass tightened the adapter boundary and response contract:

- Shared OAuth/store/receiver failures are re-homed as Claude-owned, warning-bearing secret-safe errors; token-endpoint 403 responses become policy errors.
- Login and refresh require the shared JSON token seam and no longer fall back to form requests. Thinking budgets remain fixed by reasoning level, require visible output room, and use a larger default output limit.
- Rotated credentials discard prior token-bearing identity fields, and status-after-rotation coverage confirms old secrets do not reappear.
- Structured schemas require `additionalProperties: false` recursively for objects and compare JSON enum/const values without Python boolean/integer coercion.
- Messages success envelopes require the expected type, assistant role, bounded model, and stop reason before content parsing. Nested thinking usage, strict image MIME allowlisting, HTTPS image translation, developer-to-system translation, thought-only rejection, and API-key environment lookup behavior are covered.

The review regression suite initially reported:

```text
19 failed, 23 passed in 0.52s
```

After the fixes, the focused suites passed:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py
45 passed in 0.15s

uv run pytest -q tests/llm/test_subscription_oauth.py
17 passed in 0.24s

uv run pytest -q tests/llm/test_subscription_claude_research.py tests/llm/test_subscription_oauth.py
62 passed in 0.29s
```

For the first review pass, the exact RED command and result were:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py
19 failed, 23 passed in 0.52s
```

The subsequent regression additions were also run RED before their
implementation fixes:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py -k 'json_container_values or mime_cannot or environment_values or explicit_false'
5 failed, 2 passed, 44 deselected in 0.33s
```

After the container/MIME/environment fixes, the pre-commit GREEN run was:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py tests/llm/test_subscription_oauth.py
68 passed in 0.33s
```

After commit `921ee0a`, the explicit post-commit focused runs were:

```text
uv run pytest -q tests/llm/test_subscription_claude_research.py
51 passed in 0.13s

uv run pytest -q tests/llm/test_subscription_oauth.py
17 passed in 0.27s
```
