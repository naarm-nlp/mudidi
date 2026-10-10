# What's New

## Unreleased — Faster, cheaper Stage 2

- Stage 2 now parses each page locally. A page's MDF holds exactly the lines
  printed on that page; an entry that crosses a page break is split between
  the two page files instead of being completed from the next page.
- New `dictionary.mdf.txt` joins the page files in order, and
  `dictionary.mdf.report.json` lists the pages that start mid-entry.
- Stage 2 Pass 2 sends one page image instead of three. The previous page is
  supplied as the last 1,500 characters of its transcript; the next page is no
  longer sent.
- Pages run eight at a time by default (`--batch-size 8`).
- Oversized page images are compressed once and reused across stages.

## Unreleased — Agentic loop: Evaluator and Editor

- The loop is now an evaluator-optimizer. The **Evaluator** chooses an explicit
  action (`accept`, `targeted_edits`, `full_redo`, or `reject`) and
  lists each edit as a line, the exact current text, and the replacement.
- The **Editor** (previously "rewriter") verifies every proposed edit against
  the source and approves or refuses it with a reason. Approved edits are
  applied as exact replacements; the Editor no longer re-writes the page.
- The Editor's verdicts are fed back to the Evaluator on the next round.
- The loop stops early when it detects no progress: a refused edit proposed
  again, two rounds that change nothing, or a page returning to an earlier
  version.
- **Maximum Iterations** replaces "Maximum correction iterations" and defaults
  to 3.
- **Deterministic patches** and **Concrete retry evidence** are removed from
  the dashboard. The matching YAML keys and CLI flags are still accepted but
  ignored.
- Loop records use new fields (`action`, `edits`, `redo_reason`, per-round
  Editor verdicts) and stop reasons (`invalid_decision`, `no_progress`,
  `oscillation`).
- Subscription logins wait and retry when the provider reports that it is
  overloaded or rate limiting, instead of failing the page.
- `dictionary.mdf.txt` leaves out a page whose file holds no MDF (for example
  a model refusal) and lists it under `pages_without_mdf`.

## Unreleased — Parallel runs

- The dashboard no longer limits you to one working run. Start as many as you
  like.
- **Active run** is now **Active runs**: a list of every run in progress with
  its stage and progress, including runs waiting for parsing-guide review.
- When a worker process crashes, the run page now shows the worker's last
  error line instead of a bare "failed" status.
- A run is refused only when another in-progress run writes to the same output
  folder. The Review page names that run.
- The Review page and the run page warn when another working run uses the same
  provider login, because those runs share one rate limit.

## Unreleased — Dashboard redesign

The local dashboard has a new look and a reorganized **New run** flow. Routes,
form fields, saved presets, and run data are unchanged.

- **New run** is now a six-step wizard: Input, Pipeline, Context, Model,
  Agentic, and Review. The new **Context** step holds the Dictionary Profile,
  MDF parsing-guide inputs, and an **Advanced: stage instructions** toggle.
- The interface uses the Modernist Ochre theme with light and dark modes and
  self-hosted fonts.
- Every run page shows the same workspace tab bar; views that are not available
  yet are greyed out instead of hidden.
- Native installs add **Choose folder…** beside the output directory, which
  opens the operating system's folder dialog.
- Agentic verification is renamed **Agentic loop**, with an explanation of
  what it does and what it costs.
- Dashboard runs always add the bundled SIL MDF manual text to Stage 2 Pass 1;
  the manual menu is removed.
- Request and upload size limits are off by default and opt-in through
  `--max-request-bytes` and `--max-upload-bytes`.

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
