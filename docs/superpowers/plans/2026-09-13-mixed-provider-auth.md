# Mixed-provider authentication implementation plan

Date: 2026-09-13
Design: `docs/superpowers/specs/2026-09-13-mixed-provider-auth-design.md`
Branch: `research/subscription-routing`

## Assumptions and invariants

- Billing mode remains global per run.
- Stage 2 Pass 1 and Pass 2 share one provider.
- Provider-qualified model IDs are the routing authority.
- No secret enters persisted configuration, events, logs, status payloads, URLs, or exceptions.
- Browser-disabled controls are not trusted; server validation enforces authentication and provider/model consistency.
- Existing single-provider config and form fields are removed in one cutover.

## Task 1: Cut configuration and form model to independent providers

Files:

- Modify `src/mudidi/config/yaml_config.py`
- Modify `src/mudidi/web/forms.py`
- Modify `src/mudidi/cli/run.py`
- Modify `src/mudidi/cli/main.py`
- Modify `tests/config/test_subscription_config.py`
- Modify `tests/web/test_run_form.py`
- Modify `tests/cli/test_cli.py`

Steps:

1. Use LSP references for `AuthConfig.provider`, `NewRunForm.provider`, `NewRunForm.auth_provider`, `_model_provider`, and `_resolve_model` before editing.
2. Add failing configuration tests for `AuthConfig(mode="subscription", providers=(GOOGLE, CLAUDE))`, deterministic deduplication, API-key rejection of subscription providers, and missing subscription providers.
3. Add failing form tests proving Stage 1 OpenAI and Stage 2 Anthropic resolve independently in API-key mode and Stage 1 Google plus Stage 2 Claude resolve independently in subscription mode.
4. Replace `AuthConfig.provider` with `providers: tuple[SubscriptionProvider, ...]`; validate sorted unique providers against mode.
5. Replace global form `provider`/`auth_provider` with `stage1_provider` and `stage2_provider`; keep evaluator and rewriter providers.
6. Make each model resolver accept its explicit provider. Derive required subscription providers from active model prefixes and map `gemini→google`, `anthropic→claude`, `openai→openai`.
7. Update CLI construction and summaries to emit/read `auth.providers`; a CLI-selected subscription provider remains a convenience that applies to all stage models unless explicit qualified models require additional providers.
8. Run focused config/form/CLI tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/config/test_subscription_config.py \
  tests/web/test_run_form.py \
  tests/cli/test_cli.py -q
```

## Task 2: Add provider-routed subscription runtime

Files:

- Modify `src/mudidi/llm/subscriptions/types.py`
- Modify `src/mudidi/llm/subscriptions/__init__.py`
- Modify `src/mudidi/llm/client.py`
- Modify `src/mudidi/cli/run.py`
- Modify `tests/llm/test_subscription_contract.py`
- Modify `tests/config/test_subscription_config.py`

Steps:

1. Use LSP references for `SubscriptionRuntime`, `resolve_subscription_runtime`, `_subscription_backend`, `_subscription_provider`, and `_subscription_request_model`.
2. Add failing tests with Google and Claude fake backends proving qualified models dispatch to the matching backend, normalize to native IDs, and reject unconfigured/OpenRouter/custom prefixes before network calls.
3. Add a `SubscriptionRuntimeRouter` containing an immutable provider-to-backend mapping and provider-qualified dispatch methods for plain, usage, and structured completion paths.
4. Change runtime resolution to validate and authenticate every provider in `auth.providers`, constructing one backend per provider from the existing encrypted store.
5. Preserve the existing single strategy backend parameter by passing the router as that backend; select the concrete backend inside client completion helpers from the request model prefix.
6. Update CLI execution to construct the router.
7. Run focused subscription client/contract tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/llm/test_subscription_contract.py \
  tests/config/test_subscription_config.py -q
```

## Task 3: Hand off multiple API keys and subscription providers safely

Files:

- Modify `src/mudidi/web/jobs.py`
- Modify `src/mudidi/web/inference_worker.py`
- Modify `src/mudidi/web/production_worker.py`
- Modify `tests/web/test_inference_worker.py`
- Modify `tests/web/test_subscription_worker.py`
- Modify `tests/web/test_production_jobs.py`

Steps:

1. Use LSP references for `JobController.start_inference`, `start_pass2`, `resume_inference`, `_credential_handoff`, `_worker_environment`, and `apply_credential_message`.
2. Add failing tests for a two-key fixed-schema descriptor, duplicate/unknown/nested rejection, secret-free errors, and exact multi-provider subscription descriptor validation.
3. Change job-controller entry points to receive an immutable provider-to-credential mapping rather than one credential.
4. Serialize only required non-environment API credentials under fixed environment names; subscription descriptors contain provider names only.
5. Validate the descriptor in the worker before mutating its environment. Apply all API keys atomically after complete validation.
6. Validate subscription descriptor providers exactly equal `config.auth.providers` before constructing runtimes.
7. Ensure worker process environments still omit ambient API keys in subscription mode and include no obsolete Claude opt-in variable.
8. Run focused worker/job tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_inference_worker.py \
  tests/web/test_subscription_worker.py \
  tests/web/test_production_jobs.py -q
```

## Task 4: Validate every required provider in web preview/start

Files:

- Modify `src/mudidi/web/app.py`
- Modify `src/mudidi/web/jobs.py`
- Modify `src/mudidi/web/templates/credential_required.html`
- Modify `tests/web/test_app.py`
- Modify `tests/web/test_production_routes.py`
- Modify `tests/web/test_subscription_routes.py`

Steps:

1. Add failing route tests for mixed API providers, mixed subscription providers, all missing-provider messages, and secret-free responses.
2. Add one helper that derives active model providers from `InferenceConfig`, including enabled agentic models.
3. During preview, validate every required subscription backend using the same refresh/auth sequence as the worker; aggregate safe provider-specific field errors.
4. During start/resume/pass2, resolve every required API credential and pass the mapping to `JobController`.
5. Render all missing API providers in the credential-required response instead of resolving `run.provider` as the execution authority.
6. Persist the first active stage provider only as run-list display metadata.
7. Update run details, review summaries, and preset form reconstruction for `auth.providers` and independent stage providers.
8. Run focused web route tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_app.py \
  tests/web/test_production_routes.py \
  tests/web/test_subscription_routes.py -q
```

## Task 5: Remove OAuth setup gates

Files:

- Modify `src/mudidi/llm/subscriptions/google_gemini_cli.py`
- Modify `src/mudidi/llm/subscriptions/claude_research.py`
- Modify `src/mudidi/llm/client.py`
- Modify `src/mudidi/cli/main.py`
- Modify `src/mudidi/web/app.py`
- Modify `src/mudidi/web/jobs.py`
- Modify `src/mudidi/web/production_worker.py`
- Modify relevant subscription tests under `tests/llm`, `tests/cli`, and `tests/web`

Steps:

1. Add failing tests proving Google login builds without environment registration and environment values still override the bundled registration.
2. Add failing tests proving Claude status/login/completion no longer read or require `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH`.
3. Define the official Gemini CLI installed-app client ID/secret once in the Google adapter, with the existing MUDIDI environment names as optional overrides.
4. Make every Google backend factory use the shared registration resolver.
5. Remove Claude opt-in lookup, constructor flag, policy errors/status metadata, worker allowlisting, and user-facing setup guidance.
6. Keep Claude's research-only metadata only if it describes transport provenance without controlling availability; remove it if no remaining consumer requires it.
7. Run focused OAuth and subscription-route tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/llm/test_subscription_google_gemini_cli.py \
  tests/llm/test_subscription_claude_research.py \
  tests/cli/test_subscription_auth_cli.py \
  tests/web/test_subscription_routes.py -q
```

## Task 6: Gate catalogs on authentication and expand supported manifests

Files:

- Modify `src/mudidi/web/models.py`
- Modify `src/mudidi/web/app.py`
- Modify `tests/web/test_model_catalog_service.py`
- Modify `tests/web/test_model_catalog_routes.py`
- Modify `tests/web/test_models_credentials.py`

Steps:

1. Add failing service/route tests proving unauthenticated API-key and subscription requests return a fixed `authentication_required` result without selectable models or provider network calls.
2. Add tests for authenticated API discovery, stale authenticated fallback, and authenticated subscription manifest fallback.
3. Expand subscription manifests to all models currently supported by each adapter, including multiple Claude models and stage recommendations.
4. Let subscription backends optionally expose model discovery only when their capability advertises it; otherwise use the supported manifest.
5. Ensure API-key discovery still uses official provider endpoints only after credential resolution.
6. Keep cache keys credential-scoped and invalidate on provider key/account changes.
7. Run focused catalog tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_model_catalog_service.py \
  tests/web/test_model_catalog_routes.py \
  tests/web/test_models_credentials.py -q
```

## Task 7: Build independent authenticated UI controls

Files:

- Modify `src/mudidi/web/templates/home.html`
- Modify `src/mudidi/web/templates/review.html`
- Modify `src/mudidi/web/templates/run_detail.html`
- Modify `src/mudidi/web/static/app.js`
- Modify `src/mudidi/web/static/app.css`
- Update static asset versions in every template extending the shared layout
- Modify `tests/web/test_dynamic_model_ui.py`
- Modify `tests/web/test_app.py`
- Modify `tests/web/test_theme.py`

Steps:

1. Add failing HTML and Node VM tests for all active-mode cards visible, independent stage provider values, authentication-gated model/reasoning controls, cleared stale values, no unauthenticated `/models` request, and provider-scoped save/delete/login/logout effects.
2. Remove the hidden global provider and subscription-provider selector. Give Stage 1 and Stage 2 provider controls submitted names.
3. Render provider authentication status as non-secret data attributes for API and subscription modes.
4. Replace credential-card filtering with billing-mode filtering only.
5. Add one provider-authentication lookup used by stage and agentic synchronization.
6. On provider/auth change, clear model and custom values before disabling controls. Disable model and reasoning controls until authenticated.
7. Request and apply catalogs only for authenticated provider/stage targets. Preserve Promise.all, request deduplication, abort, and sequence guards.
8. Make Stage 1 changes local to Stage 1; make Stage 2 changes update both passes and caches; keep evaluator/rewriter independent.
9. Refresh only the affected provider after key/account changes.
10. Update responsive styling and asset cache versions, then run focused UI tests and commit.

Verification:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_dynamic_model_ui.py \
  tests/web/test_app.py \
  tests/web/test_theme.py -q
node --check src/mudidi/web/static/app.js
```

## Task 8: Final review, regression, and live deployment

Files:

- Update `docs/production/local-web-app.md`
- Remove obsolete environment/setup references and old single-provider comments in affected files

Steps:

1. Search the repository for obsolete `auth.provider`, global form provider fields, `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH`, and required Google registration guidance; migrate every affected caller/test/doc.
2. Run Ruff formatting and checking on changed Python files.
3. Run the complete test suite and JavaScript syntax check.
4. Request a focused review covering secret boundaries, provider/model routing, disabled-control submission, subscription refresh, and UI race handling. Add a failing regression before each accepted fix.
5. Commit verified corrections.
6. Restart the active `mudidi-current-dashboard` process from `.worktrees/subscription-routing-research`.
7. Browser-drive API and subscription scenarios at desktop and narrow widths. Verify the served asset rather than the source file.
8. Confirm the worktree is clean and report the exact commit and verification counts.

Verification:

```bash
uv run --locked --extra dev --extra web ruff format <changed-python-files>
uv run --locked --extra dev --extra web ruff check <changed-python-files>
node --check src/mudidi/web/static/app.js
uv run --locked --extra dev --extra web pytest -q
```
