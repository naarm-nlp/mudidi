# Direct Subscription Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, local-only subscription authentication path for OpenAI Codex, Google Gemini CLI/Cloud Code Assist, and a clearly labelled Anthropic research adapter, so MUDIDI can call provider subscription endpoints without accepting or persisting provider API keys.

**Architecture:** Keep the existing LiteLLM/API-key path unchanged for `auth.mode: api_key`. Add a provider-neutral subscription backend contract with separate PKCE login, encrypted credential storage, refresh, request translation, response normalization, and redacted status. The extraction call surface selects a backend from typed configuration; the web subprocess receives only a provider/mode descriptor and reads the encrypted local subscription record itself. All subscription endpoints are HTTPS allowlisted constants, loopback OAuth callbacks use state plus PKCE, and no access/refresh token enters YAML, process arguments, JSON events, logs, templates, or API responses.

**Tech Stack:** Python 3.11+, Pydantic v2, stdlib `urllib`/`http.server`/`secrets`/`hashlib`, existing `cryptography` Fernet storage dependency from the web extra, FastAPI/HTML web UI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md`

## Global Constraints

- Subscription mode is opt-in and disabled by default. Existing API-key behavior and provider model discovery remain compatible.
- Do not import, inspect, or depend on `~/.pi`, `~/.omp`, `~/.codex`, browser cookies, provider keychains, an OMP gateway, or any other external credential store.
- Do not add a hosted relay, background broker, provider proxy, shell command, or silent API-key fallback.
- Store subscription records separately from `CredentialVault`; Fernet ciphertext is the only persisted secret representation. File and database permissions are restrictive.
- Never log, serialize, template-render, return over the web API, or include in subprocess argv/environment any access token, refresh token, authorization code, PKCE verifier, or client secret.
- Use fake HTTP servers/callables for deterministic tests. Live provider calls are opt-in smoke tests only and are not required for the normal suite.
- Claude support is explicitly research-only. The adapter must fail closed when the research opt-in is absent and expose the disabled state through typed errors and explicit metadata.
- Follow TDD: add a focused failing test before each production change, run the smallest relevant test file, then implement the minimum code.

## Task 1: Add Provider-Neutral Subscription Contracts

**Files:**
- Create `src/mudidi/llm/subscriptions/__init__.py`.
- Create `src/mudidi/llm/subscriptions/types.py`.
- Create `tests/llm/test_subscription_types.py`.

- [ ] Write tests for `SubscriptionProvider` (`openai`, `google`, `claude`), `AuthMode` (`api_key`, `subscription`), immutable `CompletionRequest`, `CompletionResult`, `BackendCapabilities`, and `SubscriptionCredential` validation. Assert token fields are excluded from `repr`, model dump, and redacted metadata.
- [ ] Define the contract: `SubscriptionBackend.login()`, `refresh()`, `logout()`, `status()`, `complete()`, and `complete_structured()`; `CompletionRequest` carries model/messages/temperature/max_tokens/reasoning/schema/cache key; `CompletionResult` carries visible text, optional structured JSON, normalized usage, finish reason, and `billing_mode="subscription"`.
- [ ] Define normalized exceptions (`SubscriptionAuthError`, `SubscriptionTokenExpired`, `SubscriptionTransportError`, `SubscriptionPolicyError`, `SubscriptionUnsupportedRequest`) with provider/category/status metadata and secret-safe stringification.
- [ ] Export only provider-neutral types and error classes from the package; keep provider-specific constants private to adapters.
- [ ] Run `uv run pytest -q tests/llm/test_subscription_types.py`; leave the test red before implementation and green afterward.

## Task 2: Implement PKCE, Loopback Callback, and Separate Encrypted Store

**Files:**
- Create `src/mudidi/llm/subscriptions/pkce.py`.
- Create `src/mudidi/llm/subscriptions/storage.py`.
- Create `src/mudidi/llm/subscriptions/oauth.py`.
- Create `tests/llm/test_subscription_pkce.py`.
- Create `tests/llm/test_subscription_storage.py`.
- Create `tests/llm/test_subscription_oauth.py`.

- [ ] Add tests proving PKCE verifier/challenge use RFC 7636 S256, state is unpredictable and distinct, callback rejects wrong state/missing code, and callback binds to `127.0.0.1` on an ephemeral port. Test callback success without opening a real browser by injecting the callback listener and opener.
- [ ] Add tests proving `SubscriptionStore` creates a separate SQLite table/file, encrypts JSON records with Fernet, uses mode `0600` for its key, survives restart, deletes records, and never exposes plaintext token bytes in database/key files or status/repr. Corrupt ciphertext must raise a typed storage/auth error rather than returning partial data.
- [ ] Implement `PkceChallenge`, `LoopbackOAuthReceiver`, and `OAuthHttpClient` using only HTTPS provider URLs plus loopback callback URLs. Validate redirect URI, state, and token response fields; bound callback/token timeouts; cap response body size; redact response details before raising errors.
- [ ] Implement storage records with provider, account label, access token, refresh token, expiry, optional provider project/account id, schema version, and updated timestamp. Keep serialization private and return a token-bearing object only inside backend code.
- [ ] Run the three focused test files.

## Task 3: Add OpenAI Codex Subscription Adapter

**Files:**
- Create `src/mudidi/llm/subscriptions/openai_codex.py`.
- Create `tests/llm/test_subscription_openai_codex.py`.

- [ ] Write fake-server tests for authorization URL construction (`auth.openai.com/oauth/authorize`), state/PKCE validation, authorization-code exchange, refresh-token exchange, malformed/expired token responses, logout, and status without secret leakage.
- [ ] Write request/response tests for the Codex Responses subscription transport: bearer access token is sent only in the HTTP `Authorization` header; model/messages/reasoning/structured schema translate to the Codex request body; response text, finish reason, and usage normalize into `CompletionResult`; 401 triggers one refresh-and-retry, while 403/429/5xx map to typed errors without API fallback.
- [ ] Implement `OpenAICodexBackend` with the Pi-researched client id/scope/loopback flow and provider-owned endpoint constants, but no Pi credential-store or process dependency. Decode only non-secret account metadata from the access-token JWT when available; never persist the raw JWT outside encrypted storage.
- [ ] Support the MUDIDI contract’s structured output by requesting the provider’s JSON/schema mode and validating the returned JSON in `complete_structured`; reject unsupported image/tool shapes with `SubscriptionUnsupportedRequest` rather than silently changing semantics.
- [ ] Run `uv run pytest -q tests/llm/test_subscription_openai_codex.py`.

## Task 4: Add Google Gemini CLI / Cloud Code Assist Subscription Adapter

**Files:**
- Create `src/mudidi/llm/subscriptions/google_gemini_cli.py`.
- Create `tests/llm/test_subscription_google_gemini_cli.py`.

- [ ] Write fake-server tests for Google OAuth authorization-code and refresh flows, loopback state/PKCE handling, required project/account metadata, Cloud Code Assist request envelope, bearer authorization, SSE/JSON response parsing, usage normalization, and secret-safe errors.
- [ ] Implement `GoogleGeminiCliBackend` against the direct Cloud Code Assist subscription endpoint (`cloudcode-pa.googleapis.com` with the documented `v1internal:streamGenerateContent?alt=sse` operation), using the locally acquired Google account token and project id. Keep production/sandbox endpoint choices explicit and HTTPS allowlisted; do not read gcloud ADC, browser state, or Antigravity files.
- [ ] Translate MUDIDI text/image message parts, reasoning level, temperature, max tokens, and structured schema to the Cloud Code Assist request envelope. Parse SSE `response` chunks until completion and return normalized visible output/usage; reject malformed or thought-only responses where the contract requires visible content.
- [ ] Implement refresh-on-401 exactly once, bounded retry for explicitly transient statuses, and typed quota/policy/auth errors. Never route the request through Gemini API-key endpoints or OpenRouter.
- [ ] Run `uv run pytest -q tests/llm/test_subscription_google_gemini_cli.py`.

## Task 5: Add Anthropic Research-Only Subscription Adapter

**Files:**
- Create `src/mudidi/llm/subscriptions/claude_research.py`.
- Create `tests/llm/test_subscription_claude_research.py`.
- Create `docs/subscription-routing-research.md`.

- [ ] Write tests that require an explicit research opt-in, build the Claude.ai OAuth PKCE request, validate callback state, exchange/refresh tokens through the researched provider endpoints, and reject login/run when opt-in is false.
- [ ] Write fake transport tests for Anthropic Messages-compatible request translation, system/user content, image blocks, extended-thinking controls, structured JSON request/response handling, usage normalization, one refresh retry, and typed policy/auth/unsupported errors. Assert API-key environment variables are neither read nor set by this backend.
- [ ] Implement `ClaudeResearchBackend` with all research-only strings and endpoint constants isolated in the module. Require `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1` (or the explicit constructor flag used by tests), expose explicit opt-in status metadata, and fail closed on missing opt-in or policy-restricted responses.
- [ ] Document the explicit opt-in flag, token storage boundary, and removal path.
- [ ] Run `uv run pytest -q tests/llm/test_subscription_claude_research.py`.

## Task 6: Add Typed Auth Configuration and Runtime Backend Selection

**Files:**
- Modify `src/mudidi/config/yaml_config.py` (`_ExtractionConfig`, model/config validators, redaction).
- Modify `src/mudidi/cli/run.py` (`execution_namespace_from_config`, extraction execution context).
- Modify `src/mudidi/cli/extract.py` (`_build_strategy` backend context).
- Modify `src/mudidi/extraction/llm_two_stage.py` (constructor and every LLM call).
- Modify `src/mudidi/llm/pass_1.py` (`discover_field_cheatsheet*`).
- Modify `src/mudidi/llm/pass_2.py` (`extract_direct_mdf`).
- Modify `src/mudidi/llm/client.py` (backend dispatch while preserving API-key path).
- Create `tests/config/test_subscription_config.py`.
- Create `tests/llm/test_subscription_dispatch.py`.

- [ ] Add failing config tests for `auth.mode/provider`, defaulting to `api_key`, requiring a supported subscription provider when mode is `subscription`, rejecting subscription mode for benchmark configurations, and redacting auth metadata without tokens.
- [ ] Add failing dispatch tests proving API-key mode calls the existing LiteLLM functions unchanged, subscription mode never calls LiteLLM or reads `*_API_KEY`, and all Stage 1/Pass 1/Pass 2/agentic calls receive the selected backend context.
- [ ] Add an `AuthConfig`/`SubscriptionRuntime` typed boundary and map it through YAML, CLI namespace, extraction strategy, pass helpers, and agentic verifier/rewriter calls. Preserve fake-callable test seams by keeping existing default function signatures compatible where possible and adding only keyword-only backend parameters.
- [ ] Implement `client.complete`, `complete_with_usage`, and `complete_structured` dispatch to the subscription backend when passed a runtime backend; otherwise retain current `_build_params`/LiteLLM behavior byte-for-byte at the call contract. Normalize subscription usage to existing fields and set `billing_mode` only for subscription results.
- [ ] Ensure `execute_extraction_config` resolves the backend in the current process without serializing tokens and that missing credentials fail before page work begins. Do not change benchmark/API-key semantics.
- [ ] Run `uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/extraction tests/cli/test_config_execution.py`.

## Task 7: Secure Web Worker Handoff and Subscription Lifecycle API

**Files:**
- Modify `src/mudidi/web/inference_worker.py` (`apply_credential_message` replacement/extension and worker setup).
- Modify `src/mudidi/web/production_worker.py` (descriptor-only stdin protocol and secret-safe error redaction).
- Modify `src/mudidi/web/jobs.py` (subscription descriptor handoff and status/error mapping).
- Modify `src/mudidi/web/app.py` (local auth routes/status/login callback/logout).
- Modify `src/mudidi/web/credentials.py` only for shared path/config helpers; keep API-key vault schema separate.
- Create `tests/web/test_subscription_worker.py`.
- Create `tests/web/test_subscription_routes.py`.

- [ ] Write tests proving worker stdin accepts only `{"auth_mode":"subscription","provider":"..."}` for subscription runs, rejects token-bearing/unknown descriptors, and never puts token material in argv, environment, event payloads, or redacted logs. Preserve existing API-key credential-message tests.
- [ ] Write route tests for non-secret status, login initiation, callback completion, logout, provider availability, Claude opt-in state, and local-only route behavior. Assert responses contain account label/expiry/category only, never access/refresh token text.
- [ ] Implement a separate per-user subscription store path under the existing web data directory, derive provider backend from the descriptor, and let the dedicated worker load encrypted local credentials. Login callback handling must remain loopback-only and use the same state/verifier transaction.
- [ ] Map typed subscription failures to existing run/job failure events with stable user-safe messages; preserve secret redaction for both API-key and subscription modes.
- [ ] Run `uv run pytest -q tests/web/test_subscription_worker.py tests/web/test_subscription_routes.py tests/web/test_production_jobs.py tests/web/test_inference_worker.py`.

## Task 8: Add CLI and Web Selection UX

**Files:**
- Modify `src/mudidi/cli/main.py` (auth command group and run flags/config help).
- Modify `src/mudidi/cli/run.py` (CLI auth override resolution).
- Modify `src/mudidi/web/forms.py` (auth mode/provider fields and validation).
- Modify `src/mudidi/web/templates/home.html` (subscription cards and status).
- Modify `src/mudidi/web/templates/*.html` only where existing run/status surfaces require the same metadata.
- Modify `tests/cli/test_cli.py`, `tests/cli/test_config_execution.py`, `tests/web/test_run_form.py`, and add `tests/cli/test_subscription_auth_cli.py`.

- [ ] Write CLI parser tests for `mudidi auth status|login|logout --provider ...`, `run --auth-mode subscription --provider ...`, config-file equivalents, invalid combinations, and secret-free output.
- [ ] Write form tests for provider-specific model normalization, subscription defaults, missing-login validation, Claude opt-in/error text, and API-key regression behavior.
- [ ] Implement auth lifecycle commands that call the local backend/store only; `auth status` prints availability/provider/account/expiry/policy category, never tokens. `auth login` opens the provider URL through the existing browser opener seam and waits for the loopback callback; `auth logout` deletes only the selected provider record.
- [ ] Add explicit “subscription billing” metadata to run previews/status and provider-specific login cards. Keep API-key model discovery controls visible and unchanged.
- [ ] Run the focused CLI/web test files.

## Task 9: Contract, Security, and Opt-In Smoke Verification

**Files:**
- Modify `tests/llm/test_subscription_contract.py` (new).
- Modify `tests/web/test_subscription_integration.py` (new).
- Modify `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md` only if implementation evidence requires a precise correction.

- [ ] Add a provider contract parametrized test that runs the same fake request/refresh/error/security assertions for OpenAI, Google, and Claude; include structured-output and image-input boundaries used by MUDIDI.
- [ ] Add a local end-to-end fake-provider smoke: login callback → encrypted store → config resolution → worker descriptor → backend request → normalized extraction result. Assert no secret crosses the descriptor/log/event boundaries.
- [ ] Run targeted tests plus the existing web/CLI extraction tests that cover changed contracts. Run `uv run pytest -q` once after all code is integrated; run the actual local CLI/web smoke command and observe successful fake-provider behavior.
- [ ] Perform a final security review of URL allowlists, callback binding/state/PKCE, file permissions, token redaction, API-key isolation, no fallback, Claude opt-in enforcement, and malformed provider responses. Remove dead compatibility code/comments and update the research document with observed limitations.

## Task 10: Commit Verified Implementation

- [ ] Inspect the final diff for unrelated changes and confirm no credentials or generated artifacts are tracked.
- [ ] Commit the verified implementation with a message such as `feat: add direct subscription routing research adapters`.
- [ ] Record the commit hash and exact focused/full verification commands in the delivery response, including that live provider authentication was not claimed unless an opt-in authenticated smoke actually ran.
