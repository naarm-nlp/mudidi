# Mixed-provider authentication and model-control design

Date: 2026-09-13
Status: Approved in chat
Base: `research/subscription-routing`

## Problem

The dashboard exposes Stage 1 and Stage 2 provider controls, but synchronizes them through one hidden `provider` value. Subscription mode adds a second global provider lock. The persisted `AuthConfig`, worker descriptor, and subscription runtime also carry only one provider. The UI therefore cannot safely execute the mixed-provider configuration it appears to offer.

Credential and subscription cards are filtered to the selected provider. Model controls remain populated before authentication, which can submit a stale model from a previous provider. The subscription catalog contains only one curated model per provider, so Claude currently exposes only Claude Sonnet 4.6.

Google login requires deployment-local OAuth metadata, while Claude login is hidden behind `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1`. Both gates conflict with the requested always-available local login flow.

## Goals

- Keep billing mode global: one run uses either API-key billing or subscription billing.
- Make Stage 1, Stage 2, evaluator, and rewriter providers independent.
- Show every credential card belonging to the active billing mode.
- Gate each model and reasoning control on authentication for that control's provider.
- Resolve and hand off every credential required by active model selections without persisting secrets.
- Route subscription requests to the backend matching each model's provider prefix.
- Always enable Google and Claude subscription login.
- Show a complete provider-supported catalog after authentication, including multiple Claude choices.

## Non-goals

- Mixing API-key and subscription billing within one run.
- Anonymous calls to credential-required provider model APIs.
- Adding a hosted MUDIDI catalog proxy.
- Scraping provider documentation at runtime.
- Allowing separate providers for Stage 2 Pass 1 and Pass 2; the existing Stage 2 provider remains shared.

## Provider model

The browser submits these independent provider fields:

- `stage1_provider`
- `stage2_provider`
- `evaluator_provider`
- `rewriter_provider`

`stage2_provider` applies to both Stage 2 passes. Evaluator and rewriter fields remain optional when agentic processing is disabled.

Provider model IDs remain qualified using the existing LiteLLM-facing prefixes:

- `openai/...`
- `anthropic/...`
- `gemini/...`
- `openrouter/...`

Subscription providers map to model prefixes through one canonical mapping:

- OpenAI subscription → `openai`
- Google subscription → `gemini`
- Claude subscription → `anthropic`

The single hidden `provider` field and global subscription-provider selector are removed. Run metadata retains a primary display provider derived from the first active stage, but it is not an authentication or routing authority.

## Persisted authentication configuration

`AuthConfig` retains the global `mode` and replaces the single subscription `provider` with a deterministic tuple of required subscription providers:

```yaml
auth:
  mode: subscription
  providers:
    - google
    - claude
```

The provider tuple is derived from active stage and agentic model prefixes, deduplicated, and sorted. API-key mode stores an empty provider tuple. Tokens and API keys never enter the configuration.

Configuration validation enforces:

- Subscription mode requires at least one supported subscription provider.
- API-key mode forbids subscription providers.
- Every active model prefix has a corresponding provider in subscription mode.
- OpenRouter is rejected in subscription mode.
- Inactive stage and agentic controls do not contribute credential requirements.

This is a clean schema cutover: repository call sites, tests, CLI namespace construction, web forms, and worker validation move to `auth.providers` together.

## Browser behavior

### Credential visibility

API-key billing shows all supported API-key cards simultaneously:

- Anthropic
- OpenAI
- Google Gemini
- OpenRouter

Subscription billing shows all subscription account cards simultaneously:

- OpenAI
- Google
- Claude

Switching billing mode hides and disables only the cards from the other mode. Selecting a stage provider never hides credential cards.

### Per-provider control gating

Provider selectors remain enabled. For each active stage or agentic role, the model and reasoning controls are enabled only when the selected provider is authenticated in the active billing mode.

- API-key mode accepts persistent, temporary, or environment credentials.
- Subscription mode requires an authenticated account for the mapped subscription provider.
- OpenRouter is omitted or disabled in subscription provider selectors.

When a selected provider is unauthenticated:

- Clear the model value and custom-model value for that control.
- Disable model, custom-model, and reasoning inputs.
- Do not request `/models/{provider}`.
- Display a provider-specific, non-secret instruction such as `Add an Anthropic API key to choose a model.` or `Log in to Claude to choose a model.`
- Prevent preview or run submission from accepting a stale hidden model value.

Saving a key or completing login refreshes authentication status, requests that provider's catalog, and enables only controls currently selecting that provider. Deleting a key or logging out clears and disables only controls using that provider. Other provider selections remain unchanged.

### Stage independence

Changing Stage 1 provider affects only Stage 1 model/reasoning state. Changing Stage 2 provider affects both Stage 2 passes, their split/shared cache, and their reasoning controls. Agentic provider changes affect only their own group.

The request sequence and `AbortController` guards remain. Catalog requests continue to run through `Promise.all`, deduplicated by provider, stage, and authentication mode.

## Model catalogs

OpenAI, Anthropic, and Gemini official model-list APIs require credentials. MUDIDI therefore does not attempt anonymous model discovery.

Before authentication, model controls remain disabled with no selectable model. The existing bundled catalog becomes an authenticated fallback rather than an anonymous selectable list.

After authentication:

- API-key mode fetches the official provider model-list API and applies existing pagination, allowlisting, filtering, caching, and stale fallback behavior.
- Subscription mode uses authenticated backend discovery when the backend advertises `model_discovery=True`.
- A backend without discovery uses a comprehensive provider-supported subscription manifest.

The subscription manifest includes every model supported by each MUDIDI adapter rather than one default entry. Claude must expose multiple supported Claude models. Known stage recommendations remain grouped above other available models.

A provider failure after a successful fetch uses the stale cached list. A first failure uses the authenticated bundled manifest. Neither fallback enables a provider that is unauthenticated.

## API-key worker boundary

The controller derives required API-key providers from active model prefixes. It resolves all required credentials from the existing vault.

The private stdin descriptor changes from one credential object to a fixed-schema provider map, for example:

```json
{
  "auth_mode": "api_key",
  "credentials": {
    "ANTHROPIC_API_KEY": "...",
    "OPENAI_API_KEY": "..."
  }
}
```

Only fixed allowlisted environment names are accepted. Duplicate keys, nested values, unsupported names, non-string values, and extra fields fail closed. Secret values are installed into the child environment only after validation and are removed from errors, events, snapshots, and logs.

Environment credentials already inherited by API-key workers need not be copied into the descriptor. Persistent and temporary credentials required by selected models are included. Missing providers move the run to the existing credential-required state before spawning work.

## Subscription worker boundary

The private subscription descriptor contains provider names only:

```json
{
  "auth_mode": "subscription",
  "providers": ["google", "claude"]
}
```

The worker validates that the descriptor provider set exactly matches `config.auth.providers`. It loads each credential from the encrypted subscription store and constructs a `SubscriptionRuntimeRouter`.

The router implements the backend interface used by the extraction strategy. For every completion request it:

1. Parses the qualified model prefix.
2. Maps the prefix to the configured subscription provider.
3. Rejects unconfigured, custom, or OpenRouter prefixes.
4. Strips or normalizes the model through the selected provider's existing resolver.
5. Dispatches to that provider's authenticated backend.

This keeps one worker and the existing pipeline lifecycle. Stage 1, Stage 2, evaluator, and rewriter calls can use different providers without splitting the run into multiple processes.

Status, refresh, and missing-login checks run for every required backend before page processing begins. One missing or expired provider prevents the run from starting; no partial page processing occurs.

## Always-enabled subscription login

### Google

MUDIDI bundles the installed-application OAuth client ID and client secret published by Google's official Gemini CLI. Google's source explicitly documents that this installed-app registration is intended to be embedded. Existing MUDIDI environment values remain optional overrides for deployments using their own registration.

The default backend, CLI factory, runtime factory, worker allowlist, login errors, and tests use one shared registration resolver. Normal users no longer need `MUDIDI_GOOGLE_OAUTH_CLIENT_ID`.

### Claude

Remove the `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH` lookup and the opt-in policy gate. Claude login and execution are enabled by default.

Remove the policy-only account status, environment propagation, setup guidance, and error branches. The encrypted credential boundary, OAuth state validation, fixed endpoints, callback security, and safe error normalization remain unchanged.

The internal adapter name remains unchanged to avoid a weightless rename; user-facing copy says `Claude subscription` and contains no research opt-in instruction.

## Validation and error behavior

- Form validation names the exact unauthenticated provider required by each active control.
- Preview and start re-check provider availability server-side; disabled browser controls are not trusted.
- A run requiring several providers reports all missing providers in one safe response.
- Provider API and OAuth errors remain fixed-message and secret-free.
- Worker descriptor/config provider-set mismatches fail before backend creation.
- A model/backend prefix mismatch fails before a network request.

## Migration and compatibility

All repository-generated configurations move to `auth.providers`. Tests and internal fixtures using `auth.provider` are migrated in the same cutover. No deprecated alias or dual-schema path is retained.

Existing managed run snapshots using the former single-provider subscription schema are historical artifacts. They remain viewable, but resuming an uncompleted pre-cutover subscription run requires recreating its run configuration through the dashboard.

## Verification

Automated contracts cover:

- Independent Stage 1 and Stage 2 provider parsing and persistence.
- Stage 1 OpenAI plus Stage 2 Anthropic with separate API keys.
- Stage 1 Google plus Stage 2 Claude with separate subscription backends.
- Evaluator and rewriter routing independent of stage providers.
- Exact provider-set validation in configuration and worker descriptors.
- Multiple-key handoff allowlisting, redaction, and missing-provider failures.
- Subscription router dispatch, model normalization, and prefix rejection.
- All active-mode credential cards visible.
- Provider-scoped model/reasoning gating, clearing, login enablement, logout disablement, and stale-value submission defense.
- No model endpoint request before authentication.
- Multiple Claude models after authentication.
- Google default registration and optional override.
- Claude login and execution without an opt-in environment variable.

Final verification runs targeted tests, the complete suite, JavaScript syntax checking, and browser-driven scenarios against the actual dashboard at desktop and narrow widths. The verified code is committed, fast-forwarded into the active dashboard worktree, the service is restarted, and the served asset is checked in-browser.
