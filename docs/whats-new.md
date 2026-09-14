# What's New

## 0.1.1 — Google subscription workflow

MUDIDI 0.1.1 makes Google subscription login and model selection work entirely
from the local dashboard.

### Google authentication and models

- Log in and out through Google Antigravity's browser OAuth without requiring
  an Antigravity CLI login.
- Store the resulting access and refresh tokens only in MUDIDI's encrypted
  local subscription store.
- Refresh live subscription model catalogs on demand and invalidate cached
  results when login or logout changes the active account.
- Put Gemini Pro models before Flash models, preserving newest-first provider
  order within each family.
- Show the account as **Google subscription** in the dashboard.

### Compatibility

- Migrate legacy saved presets containing `models.temperature` before strict
  validation.
- Preserve API-key billing as an independent authentication mode.

## 0.1.0 — Subscription inference

MUDIDI can now run production dictionary digitization through authenticated OpenAI, Google, or Claude subscriptions. API-key inference remains available as a separate billing mode.

### Subscription accounts

- Sign in and sign out from the local dashboard or `mudidi auth`.
- Keep provider credentials in a dedicated encrypted local store.
- Use independently authenticated providers without exposing access tokens in configuration, logs, subprocess arguments, or browser storage.
- Refresh expired sessions once and fail with actionable authentication guidance when reauthentication is required.
- Make Subscription billing the first and default billing choice for new dashboard runs; API-key billing remains available below it.

### Live model selection

- Load stage-compatible models from the authenticated account rather than a fixed OpenAI, Gemini, or Claude list.
- Sort Google subscription models with Pro before Flash while preserving newest-first order within each family and for other providers.
- Show only the reasoning levels supported by the selected model, including adaptive Claude and effort-qualified Gemini models.
- Configure Stage 1, Stage 2 Pass 1, Stage 2 Pass 2, evaluator, and rewriter models independently where the workflow permits it.
- Preserve entitlement-only split-model selections in saved presets while the live catalog refreshes.

### Production workflow

- Run subscription-backed inference from both the local web dashboard and `mudidi run`.
- Review and approve the generated MDF parsing guide before Stage 2 conversion.
- Resume compatible CLI output safely; changed instruction attachments require an explicit overwrite.
- Cancel queued or active web runs without leaving page output or usage attributed to work that never completed.
- Pass `--alphabet PATH` to enable the supplied character inventory automatically.
- Migrate older saved dashboard presets that contain retired configuration
  fields instead of failing during dashboard startup.

### What changed

- Dashboard preset storage now runs schema migration 5 for existing local
  databases.
- Legacy `models.temperature` values are removed before strict configuration
  validation, so old presets no longer crash dashboard startup.
- The migration preserves current preset settings and keeps new YAML and API
  configurations strict; `temperature` remains rejected for newly submitted
  configurations.

### Provider adapters

- **OpenAI:** Codex account authentication, account-scoped model discovery, structured output, image input, reasoning controls, and usage normalization.
- **Google:** direct Google Antigravity OAuth and Cloud Code routing, canonical Gemini model aliases, effort routing, failed-run retry, and project-aware requests.
  The official Antigravity installed-app registration is bundled; the
  `MUDIDI_GOOGLE_OAUTH_CLIENT_ID` and `MUDIDI_GOOGLE_OAUTH_CLIENT_SECRET`
  variables are optional overrides for compatible Antigravity registrations.
- **Claude:** OAuth-backed Messages requests, paginated model discovery, image input, structured output, adaptive or extended thinking, and typed failure handling.

!!! note
    Subscription adapters use provider-specific consumer authentication and locally observed interfaces. Claude subscription routing is documented as researched behavior rather than a public Anthropic OAuth contract. Review provider terms before relying on these paths in a production environment.

### Verified release boundary

The release was smoke-tested with page 34 of the *Carolinian-English Dictionary* through all three authenticated providers and both supported surfaces. Coverage included complete Gemini extraction, OpenAI and Claude transcription, CLI split-model execution, agentic verification, cancellation, guide approval, editing, artifacts, history, usage, presets, and resume behavior.

Three defects found during that smoke were repaired before release:

1. saved subscription presets losing entitlement-only split models during catalog startup;
2. complete overwrite runs issuing a duplicate Stage 1 request for the Pass 1 sample page;
3. explicit CLI alphabet files remaining disabled in the resolved runtime.

The release candidate passed 1,383 repository tests with 10 intentional skips,
the strict MkDocs build, generated-reference verification, Ruff, and live
post-repair web and CLI checks. Current main passes 1,385 tests, including the
legacy-preset migration regression.
