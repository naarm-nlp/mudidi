# Direct Subscription Routing Design

> **Status:** Approved research design. This feature is local-only and opt-in; it is not a hosted credential service.

## Goal

Allow each local MUDIDI user to authenticate with an OpenAI, Google, or Claude subscription through a provider-specific PKCE OAuth flow and run MUDIDI inference without entering an API key into MUDIDI.

## Scope

- Add a provider-neutral subscription backend contract above the existing LiteLLM/API-key path.
- Implement local PKCE authentication, encrypted credential storage, refresh, logout, and non-secret status.
- Implement direct research adapters for OpenAI Codex, Google subscription routing, and Claude subscription routing.
- Wire subscription selection into the CLI, loopback web dashboard, and inference worker.
- Preserve the existing API-key path unchanged and require explicit selection before using it.
- Keep the work behind an explicit research opt-in until provider behavior and terms are reviewed.

## Non-goals

- No OMP auth gateway, OMP broker, Pi process, or provider credential-store reuse.
- No hosted or multi-user credential service.
- No reading `~/.pi`, `~/.omp`, `~/.codex`, browser cookies, or provider keychains directly.
- No silent fallback from subscription routing to API-key billing.
- No automatic account sharing, quota pooling, or server-side subscription proxy.
- No changes to unrelated OCR, PDF, extraction, or dashboard styling behavior.

## Constraints

- MUDIDI binds its web dashboard to loopback and runs workers as the local user.
- Subscription mode is separate from API-key mode in configuration, UI, persisted run metadata, and worker messages.
- Provider-specific OAuth and request transport remain isolated behind adapter interfaces.
- PKCE state and verifier validation is mandatory for every browser callback.
- Refresh tokens are encrypted at rest, never logged, never included in worker event payloads, and never exposed through web responses.
- OAuth callback listeners bind to loopback only and use a per-login state value.
- Provider credentials are loaded only inside the subscription backend process that needs them.
- Research adapters must fail closed when provider capability or authentication state is unavailable.
- Authenticated live smokes are opt-in and never part of the default test suite.
- The default Google OAuth registration seam reads only the deployment-local
  public client ID from `MUDIDI_GOOGLE_OAUTH_CLIENT_ID`; subscription workers
  never inherit OAuth tokens, authorization codes, client secrets, or provider
  API-key variables.
- Claude subscription support is explicitly research-only and must fail closed unless the explicit opt-in is enabled.

## Architecture

### Backend boundary

`src/mudidi/llm/client.py` currently builds LiteLLM parameters and resolves API keys. Subscription routing must not be added as more model-name conditionals. Introduce a backend selection layer with a stable internal request/result contract:

- `CompletionRequest`: model id, normalized messages, image references, max output tokens, reasoning effort, temperature policy, schema, timeout, and cancellation signal.
- `CompletionResult`: text, provider/model identity, finish state, normalized usage, billing mode, and raw-provider diagnostics excluded from user-facing logs.
- `BackendCapabilities`: image input, structured output, streaming, cancellation, usage, reasoning, and model discovery.
- `SubscriptionBackend`: login, status, logout, capabilities, unstructured completion, and structured completion.

The existing LiteLLM functions remain a backend implementation. Existing callers should continue to use the same public extraction behavior while selecting the backend from resolved configuration.

### Local credential lifecycle

Add a MUDIDI-owned subscription credential store separate from API-key persistence. The store uses an encrypted local file/SQLite record with restrictive permissions and a separate encryption key file. Records contain provider id, access token, refresh token, expiry, account metadata safe for display, and provider-specific non-secret metadata. The store exposes only:

- `save(provider, credential)`
- `load(provider)`
- `delete(provider)`
- `status(provider)`
- serialized refresh/update for concurrent local workers

The web application can request status and launch login, but never receives access or refresh token values. Workers receive a backend/provider selector and resolve credentials from the local store under the same OS account.

### OAuth flow

The shared OAuth utility owns PKCE generation, loopback callback binding, state verification, timeout, cancellation, and safe callback responses. Each provider adapter supplies authorization URL, token exchange, refresh, scopes, client registration values, and provider-specific account parsing. Provider modules must not share assumptions about token response fields or refresh semantics.

### Worker boundary

API-key workers retain the existing one-shot credential message. Subscription workers receive a non-secret message such as:

```json
{"auth_mode":"subscription","provider":"openai"}
```

The worker starts the selected backend, reads the local encrypted credential store, and emits only redacted status/errors. Subprocess and worker logs must pass through existing redaction before persistence. For Google, the parent and production worker carry only `MUDIDI_GOOGLE_OAUTH_CLIENT_ID` through the explicit subscription environment allowlist so the resolver can construct the same registered backend; all other secret-looking variables remain excluded.

## Provider adapters

### OpenAI Codex

Implement an opt-in Codex subscription adapter using the provider-specific PKCE flow and Codex request envelope required by the ChatGPT/Codex subscription surface. Account/workspace identity must be retained only as non-secret metadata. The adapter must normalize Codex responses into MUDIDI text/structured results and expose account/authentication/quota failures distinctly.

The adapter must not assume that an ordinary OpenAI API-key model id is valid for Codex subscription transport; provider model mapping is explicit.

### Google subscription

Implement a provider-specific Google subscription adapter behind the same interface. Separate account OAuth, project/account provisioning, model selection, and request transport from the existing Gemini API-key adapter. The adapter must not send a subscription credential to the public Gemini API-key endpoint. Headless structured output and image capability must be tested independently because Google’s consumer/agent product surfaces and quotas can change.

Deployment setup supplies the public installed-app OAuth client registration
through `MUDIDI_GOOGLE_OAUTH_CLIENT_ID` before starting the CLI or web process.
The default CLI factory, web app factory, runtime resolver, and subscription
worker use this same explicit variable. A missing registration is not replaced
with a Google CLI/gcloud/browser credential or an API key: login and refresh
fail closed with the typed `oauth_client_configuration_missing` reason before
any token exchange.

### Claude subscription

Implement Claude subscription routing only as a clearly marked research adapter. It must use the local PKCE flow and provider-specific request shaping isolated from the ordinary Anthropic API-key adapter. It must not read Claude Code credential files, scrape browser sessions, or expose a generic remote gateway. The UI and CLI must expose the disabled opt-in state through safe category and configuration guidance. If provider behavior or terms validation fails, the adapter returns an explicit unsupported result; it must not silently use an API key.

## Configuration and user experience

Add an explicit authentication mode to typed inference configuration:

```yaml
auth:
  mode: subscription # api_key | subscription
  provider: openai
```

The exact nesting must follow existing YAML conventions after implementation mapping. Model/provider selection remains separate from authentication mode. Subscription mode requires a provider adapter and rejects API-key-only custom providers.

CLI requirements:

- `mudidi auth status`
- `mudidi auth login <provider>`
- `mudidi auth logout <provider>`
- explicit run/config selection for subscription mode
- safe status output with account label and expiry/quota state, never token values

Web requirements:

- provider subscription cards separate from API-key credential cards
- login/logout/status actions
- explicit disabled-opt-in state and configuration guidance for Claude
- no token reveal endpoint
- clear authentication, quota, unsupported-capability, and API-billing states
- no silent fallback

## Error and usage semantics

Normalize provider failures into authentication, expired-session, quota/rate-limit, unsupported-capability, transport, schema, cancellation, and provider-error categories. Subscription usage has `billing_mode="subscription"` and may have unknown monetary cost; do not report zero-dollar API cost. API-key fallback is a user-selected mode change, not an automatic retry.

## Testing strategy

- Unit tests for PKCE verifier/challenge generation and callback state rejection.
- Fake OAuth server tests for authorization-code exchange, refresh rotation, expiry, cancellation, and malformed responses.
- Credential-store tests for encryption, permissions, serialized refresh, redaction, and missing/expired records.
- Adapter contract tests for text, image, structured schema, usage, cancellation, and normalized errors.
- Worker tests proving subscription messages contain no token material and API-key behavior remains unchanged.
- CLI/web tests for login/status/logout and explicit auth-mode validation.
- Authenticated live smoke tests are opt-in, provider-specific, and run manually after installation/login.

## Observed local verification limitation

The OpenAI Codex adapter currently accepts a `2xx` response with
`status: "completed"` when the response has an `output` field but none of its
items contains visible text (for example, an unknown output item). It returns
an empty `CompletionResult` instead of raising the `missing_visible_text`
transport error. A response with no `output`/`output_text` still fails closed.
Until the provider envelope is tightened, callers must not treat an empty
normalized text result as successful extraction.


## Rollout

1. Land the backend contract and local auth store behind the research feature flag.
2. Add one provider adapter and its fake-server tests at a time.
3. Validate a complete MUDIDI Stage 1/Stage 2 run per provider with a local authenticated account.
4. Keep subscription mode disabled by default until all required adapters pass their smoke tests and the provider-policy review is recorded.
5. Preserve API-key mode as the stable default and do not change its billing or credential semantics.
