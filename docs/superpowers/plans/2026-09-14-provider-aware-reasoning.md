# Provider-Aware Reasoning Implementation Plan

**Goal:** Add model-specific reasoning levels from `minimal` through `max`, complete newest-first subscription catalogs, and entitlement-only subscription model selection across MUDIDI's OpenAI, Claude, Gemini, and OpenRouter paths.

**Architecture:** A new provider-neutral reasoning module owns the portable effort order, immutable model profiles, reviewed provider/model rules, mapping, and safe fallback selection. Structured model records carry reasoning and recency metadata from provider discovery through the catalog API to every browser selector. Subscription transports use provider metadata and reviewed rules to produce exact wire payloads; subscription form submissions are validated against the authenticated catalog and cannot use manual model IDs.

**Tech stack:** Python 3.11, Pydantic, FastAPI, Jinja2, browser JavaScript, pytest, Ruff, authenticated local provider probes, Chromium.

**Specification:** `docs/superpowers/specs/2026-09-14-provider-aware-reasoning-design.md`

## Global Constraints

- Work on `research/subscription-routing` in `.worktrees/subscription-routing-research`.
- Portable enabled efforts are exactly `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`; provider-specific `ultra` is ignored.
- Known models expose only supported efforts. Unknown/manual API-key models expose all six.
- Existing `none` values resolve to the model's lowest supported effort, or low for unknown models.
- No OMP runtime dependency or copied full-provider catalog.
- Subscription mode lists all authenticated provider models, newest first, and has no manual/stale synthetic model option.
- API-key and advanced custom modes retain manual entry.
- Provider errors, raw payloads, access tokens, and capability cache identity never cross the browser or persistence boundary.
- Reactive effort fallback is single-use and only for explicit 400/422 effort errors listing recognized allowed values.
- Write failing behavioral tests before each production change. Run narrow tests during development and the full suite once after final smoke verification.

---

### Task 1: Add the canonical reasoning profile module

**Files:**
- Create: `src/mudidi/llm/reasoning.py`
- Create: `tests/llm/test_reasoning.py`

**Contract:**

- Export `ReasoningEffort`, `ENABLED_REASONING_EFFORTS`, and immutable `ReasoningProfile`.
- Model profiles contain supported efforts, optional default, disable support, effort map, optional wire-model routing, and capability source.
- Export helpers for validating advertised efforts, choosing a supported value, mapping a portable value to wire effort/model ID, and resolving reviewed profiles for MUDIDI providers.
- Unknown models return all six efforts with low initial selection.
- `none` resolves to the lowest known effort and never becomes a provider-default request.

**Steps:**

1. Add failing tests for canonical ordering, malformed/unknown advertised values, `none` normalization, nearest-lower selection, explicit mappings, Gemini Flash/Pro profiles, Claude lineage profiles, OpenAI/Codex families, OpenRouter upstream slugs, and unknown all-six behavior.
2. Run `uv run pytest tests/llm/test_reasoning.py -q` and confirm collection/import failure.
3. Implement the smallest immutable profile/resolver module satisfying those tests. Keep reviewed rules centralized and data-oriented; do not duplicate request construction.
4. Run the focused test plus Ruff on the new files.

---

### Task 2: Carry capability and recency metadata through catalogs

**Files:**
- Modify: `src/mudidi/web/models.py`
- Modify: `tests/web/test_models_credentials.py`
- Modify: `tests/web/test_model_catalog_service.py`
- Modify: `tests/web/test_model_catalog_routes.py`

**Contract:**

- `ModelOption`, `LiveModelOption`, and `CatalogItem` carry `ReasoningProfile`, optional parsed release timestamp, and optional provider order.
- `CatalogResult.to_payload()` emits only the JSON-safe reasoning fields required by selectors.
- Add one newest-first comparator: release timestamp, provider-defined order when valid, parsed numeric/date ID revision, then deterministic name/ID tie-breakers.
- Numeric tuples make `3.10` newer than `3.9`.
- API-key Recommended and Available groups each use the comparator. Subscription results use one complete ordered sequence.

**Steps:**

1. Add failing serialization, malformed timestamp, `3.10 > 3.9`, timestamp precedence, stable tie, and structured-cache tests.
2. Extend the dataclasses and parsers without adding raw provider payload fields.
3. Replace ascending model-ID sorts with the shared comparator.
4. Run the three catalog test modules and Ruff.

---

### Task 3: Enrich authenticated subscription discovery

**Files:**
- Modify: `src/mudidi/llm/subscriptions/types.py`
- Modify: `src/mudidi/llm/subscriptions/openai_codex.py`
- Modify: `src/mudidi/llm/subscriptions/google_antigravity.py`
- Modify: `src/mudidi/llm/subscriptions/claude_research.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `tests/llm/test_subscription_openai_codex.py`
- Modify: `tests/llm/test_subscription_google_antigravity.py`
- Modify: `tests/llm/test_subscription_claude_research.py`
- Modify: `tests/web/test_model_catalog_service.py`

**Contract:**

- Replace tuple model results with one non-secret structured subscription model type shared by all subscription backends.
- Codex retains recognized object/string `supported_reasoning_levels`, default level, input modes, and provider priority. Ignore `ultra`.
- Google collapses effort variants while retaining the discovered effort-to-wire-ID map; Tiered remains an alias only.
- Claude implements authenticated, bounded, redirect-safe `GET /v1/models?limit=1000&beta=true` discovery with OAuth bearer and the established Claude CLI headers, pagination, `created_at`, refresh-on-401, and fixed safe errors.
- The app uses authenticated discovery for OpenAI, Google, and Claude. Every valid returned provider model is retained.

**Steps:**

1. Add failing provider parser tests, including malformed rows, ignored `ultra`, Google routing, Claude pagination/repeated tokens, 401 refresh, redirect rejection, response bounds, and secret-safe errors.
2. Add the structured type and migrate every subscription discovery caller/test in one clean cutover.
3. Implement provider-specific metadata extraction and Claude discovery.
4. Update application subscription discovery mapping to include Claude.
5. Run all three subscription test modules plus the catalog service tests and Ruff.

---

### Task 4: Route model-specific reasoning safely

**Files:**
- Modify: `src/mudidi/llm/client.py`
- Modify: `src/mudidi/llm/subscriptions/openai_codex.py`
- Modify: `src/mudidi/llm/subscriptions/google_antigravity.py`
- Modify: `src/mudidi/llm/subscriptions/claude_research.py`
- Modify: relevant `tests/llm/test_*.py`

**Contract:**

- All transports accept the canonical reasoning choice and resolve it through the same profile semantics.
- Codex/OpenAI and OpenRouter pass or explicitly map recognized wire effort.
- Gemini subscription routing uses the profile's wire-model map and emits only valid Google level/budget values.
- Claude budget models emit bounded thinking budgets; adaptive models emit compatible adaptive thinking and top-level output effort through max.
- API-key LiteLLM routing no longer relies on incomplete slug-marker checks.
- OpenAI-compatible 400/422 fallback parses only explicit reasoning-effort errors with allowed values and retries once with the nearest recognized value. Unrelated or ambiguous failures are unchanged.

**Steps:**

1. Add failing wire-payload tests for minimal/xhigh/max representatives across each provider and for `none` lowest-level normalization.
2. Add failing fallback tests: recognized allowed list, special mappings, one retry only, output-present prohibition, ambiguous error, unrelated 4xx, and non-400/422.
3. Replace local Literal/types and scattered model checks with profile resolution.
4. Implement Claude adaptive and budget payloads, Google routes, and Codex/OpenRouter effort mapping.
5. Implement one bounded fallback at the narrow OpenAI-compatible request boundary.
6. Run focused LLM and subscription suites plus Ruff.

---

### Task 5: Expand configuration, CLI, and persistence

**Files:**
- Modify: `src/mudidi/config/yaml_config.py`
- Modify: `src/mudidi/cli/extract.py`
- Modify: `src/mudidi/cli/main.py`
- Modify: `src/mudidi/cli/run.py`
- Modify: `src/mudidi/web/forms.py`
- Modify: affected config/CLI/form tests and examples

**Contract:**

- Import one canonical reasoning type/choice set instead of duplicating Literals and argparse choices.
- Stage 1, both Stage 2 passes, evaluator, and rewriter accept minimal through max.
- Existing `none` inputs normalize to the effective model's lowest effort; unknown resolves to low.
- Persisted active runs contain the resolved effort, not provider capability payloads.

**Steps:**

1. Add failing YAML, CLI, form, and persistence round-trip cases for minimal, xhigh, and max plus existing-none normalization.
2. Replace every duplicated choice list and narrow type.
3. Update command help and checked-in configuration examples that enumerate valid levels.
4. Run config, CLI, run-form, run-store, and worker tests plus Ruff.

---

### Task 6: Enforce entitlement-only subscription model selection

**Files:**
- Modify: `src/mudidi/web/models.py`
- Modify: `src/mudidi/web/forms.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `tests/web/test_model_catalog_service.py`
- Modify: `tests/web/test_run_form.py`
- Modify: `tests/web/test_production_routes.py`
- Modify: `tests/web/test_subscription_routes.py`

**Contract:**

- Subscription catalog result contains one flat newest-first `available` sequence with every authenticated model and no curated allowlist filtering.
- Server rejects `__other__`, custom model fields, cross-provider IDs, and IDs absent from the authenticated or last-successful subscription catalog.
- A missing saved model is replaced by the newest current model with a safe visible notice; it is never restored as a synthetic stale option.
- API-key/custom submission behavior is unchanged.

**Steps:**

1. Add failing complete-catalog, newest-first, manual/cross-provider/stale rejection, stale-success acceptance, and unavailable-preset tests.
2. Add catalog lookup/validation methods that use server-owned structured records and existing cache locking.
3. Integrate validation into the authoritative form/submission path without trusting browser metadata.
4. Run catalog, run-form, production-route, and subscription-route tests plus Ruff.

---

### Task 7: Drive model-aware reasoning selectors in the browser

**Files:**
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `tests/web/test_app.py`
- Modify: `tests/web/test_dynamic_model_ui.py`
- Modify: any existing JavaScript harness fixtures in `tests/web/test_app.py`

**Contract:**

- Every model option retains its reasoning profile in safe data attributes or an in-memory catalog map.
- Model changes rebuild the paired reasoning selector using only supported known levels; unknown/manual API-key models show all six.
- Unsupported preserved values choose nearest supported no-greater effort, then advertised default, then lowest.
- Subscription mode renders one `Available from your subscription — newest first` group, does not append Other/current-selection entries, and hides/disables custom model fields.
- API-key and custom modes continue to append and operate Other/manual entry.
- All stage, split-pass, evaluator, and rewriter controls stay synchronized.

**Steps:**

1. Add failing server-rendered option and JavaScript behavior tests for six levels, known subsets, model switching, subscription no-Other behavior, newest-first order, unavailable selection replacement, and API-key manual retention.
2. Replace hardcoded reasoning option markup with a safe initial representation and reusable JavaScript population helper.
3. Branch catalog application explicitly by auth mode; do not infer subscription status from labels.
4. Run UI/template tests and the repository's JavaScript syntax check.

---

### Task 8: Verify end to end, update docs, and commit

**Files:**
- Modify: `README.md` and relevant `docs/` pages that enumerate reasoning or subscription model behavior
- Modify: generated references only through the project's existing generator

**Steps:**

1. Run focused final suites covering reasoning, providers, catalogs, config/CLI, forms, routes, workers, and UI.
2. Start the dashboard from the completed source and run authenticated catalog probes for Codex, Google, and Claude. Confirm all account-visible models survive normalization and are newest-first.
3. Drive Chromium through subscription OpenAI, Google, and Claude selections. Assert no option contains Other, each provider's newest model is first, Gemini 3.8 precedes older revisions, and reasoning options match the selected model profile.
4. Exercise one accepted high-end effort for each authenticated provider that advertises it. Confirm the actual wire model/effort from bounded diagnostic capture without logging credentials or response bodies.
5. Run the full project test/lint/documentation validation commands once.
6. Update user documentation and generated references for the portable ladder, model-aware selectors, newest-first catalogs, and subscription manual-entry removal.
7. Review only the final changed-file set for unrelated user work, then commit all verified implementation files with a focused message.
8. Preserve the final durable provider capability and verification findings in `~/wiki/`, rebuild/lint the wiki graph, and commit only the wiki files from this work.

## Completion Evidence

Delivery must report:

- exact focused/full test and lint counts;
- authenticated provider models observed and newest entries;
- browser assertions for subscription no-Other and reasoning subsets;
- accepted live high-end effort probes;
- implementation commit hash;
- any provider limitation that could not be verified, marked explicitly rather than inferred.
