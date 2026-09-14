# Dynamic Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static-only model dropdown flow with secret-safe account discovery, stage-specific recommendation groups, resilient caching/fallback, and authentication-mode/provider-specific credential panels.

**Architecture:** A synchronous `ModelCatalogService` in `mudidi.web.models` owns official provider discovery, per-process credential-isolated caching, curated subscription lists, recommendation grouping, and degraded results. FastAPI exposes its non-secret result through one endpoint; browser code requests that endpoint and rebuilds every model select while preserving current selections and hiding irrelevant credential/account panels.

**Tech Stack:** Python 3.11, FastAPI, Pydantic secret wrappers already used by the credential vault, Jinja2, browser JavaScript, pytest, Ruff, Chromium browser verification.

**Spec:** `docs/superpowers/specs/2026-09-13-dynamic-model-catalog-design.md`

## Global Constraints

- Work only in `.worktrees/dynamic-model-catalog` on `feature/dynamic-model-catalog`.
- API-key discovery uses only fixed official HTTPS endpoints and never exposes raw credentials, credential fingerprints, provider payloads, or raw provider exceptions.
- Cache lifetime is exactly 15 minutes; cache identity is provider plus a keyed process-local credential fingerprint; cache data is not persisted.
- Subscription model lists are curated and never call private provider web-app endpoints.
- Stage values are exactly `stage1`, `stage2`, and `verification`; both Stage 2 passes map to `stage2`.
- Provider failures return stale last-successful data or bundled fallback without erasing prior success.
- Discovery guides the UI but is not an execution allowlist; `Other model…` remains available.
- Only the active authentication panel and selected provider card are visible; switching does not delete credentials or log out accounts.
- No new runtime dependency is permitted.

---

### Task 1: Harden and paginate official provider discovery

**Files:**
- Modify: `src/mudidi/web/models.py:1-312`
- Test: `tests/web/test_models_credentials.py`

**Interfaces:**
- Consumes: existing `ModelDiscovery.discover(provider: Provider, *, api_key: str) -> tuple[LiveModelOption, ...]`.
- Produces: the same public method with bounded Anthropic/Gemini pagination, deterministic deduplication, incompatible-family filtering, and redirect-safe `_fetch_json`.

- [ ] **Step 1: Add failing pagination and filtering tests**

Add provider-specific fake fetches that record URLs and return successive pages:

```python
def test_anthropic_discovery_follows_last_id_without_duplicates() -> None:
    pages = {
        "https://api.anthropic.com/v1/models?limit=1000": {
            "data": [{"id": "claude-sonnet-current"}],
            "has_more": True,
            "last_id": "claude-sonnet-current",
        },
        "https://api.anthropic.com/v1/models?limit=1000&after_id=claude-sonnet-current": {
            "data": [
                {"id": "claude-sonnet-current"},
                {"id": "claude-opus-current"},
            ],
            "has_more": False,
        },
    }
    calls: list[str] = []
    models = ModelDiscovery(fetch=lambda url, _headers: (calls.append(url), pages[url])[1]).discover(
        Provider.ANTHROPIC,
        api_key="secret",
    )
    assert calls == list(pages)
    assert [model.model_id for model in models] == [
        "anthropic/claude-opus-current",
        "anthropic/claude-sonnet-current",
    ]
```

Add equivalent Gemini coverage for `nextPageToken`, a repeated-token test that raises `ModelDiscoveryError`, and a parameterized test proving embedding, moderation, speech/audio-only, image-generation, reranker, and safety-classifier IDs are excluded while ordinary generation IDs remain.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_models_credentials.py::test_anthropic_discovery_follows_last_id_without_duplicates \
  tests/web/test_models_credentials.py::test_gemini_discovery_follows_next_page_token \
  tests/web/test_models_credentials.py::test_discovery_rejects_repeated_page_token \
  tests/web/test_models_credentials.py::test_discovery_excludes_incompatible_model_families -q
```

Expected: pagination/filter assertions fail because discovery currently reads one unfiltered page.

- [ ] **Step 3: Implement bounded pagination and filtering**

Keep `_discovery_request()` as the sole source of initial URLs and headers. Add `_MAX_DISCOVERY_PAGES = 20`, `_next_page_url(provider, url, payload)`, and `_is_generation_candidate(provider, raw_id)`. Use `urllib.parse.parse_qsl`, `urlencode`, `urlsplit`, and `urlunsplit` so provider tokens are encoded rather than concatenated.

`ModelDiscovery.discover()` must:

```python
url, headers = _discovery_request(provider, api_key)
found: dict[str, LiveModelOption] = {}
seen_urls: set[str] = set()
for _ in range(_MAX_DISCOVERY_PAGES):
    if url in seen_urls:
        raise ValueError("provider model pagination repeated a page")
    seen_urls.add(url)
    payload = self._fetch(url, headers)
    for model in _parse_live_models(provider, payload):
        found[model.model_id] = model
    next_url = _next_page_url(provider, url, payload)
    if next_url is None:
        return tuple(found[key] for key in sorted(found))
    url = next_url
raise ValueError("provider model pagination exceeded its page limit")
```

Wrap all failures through the existing fixed `ModelDiscoveryError` message. Apply the incompatible-family filter before constructing `LiveModelOption`; do not filter arbitrary unknown generative IDs merely because capability metadata is absent.

- [ ] **Step 4: Add and satisfy redirect-target tests**

Monkeypatch `mudidi.web.models.urlopen` with a response whose `geturl()` is `https://evil.example/models`; assert `_fetch_json()` raises before parsing. Validate the final response URL with the same `_MODEL_API_HOSTS` allowlist used for the request URL. Keep the five-second timeout and five-MiB bounded read.

- [ ] **Step 5: Run focused discovery tests and commit**

```bash
uv run --locked --extra dev --extra web pytest tests/web/test_models_credentials.py -q
uv run --locked --extra dev --extra web ruff check src/mudidi/web/models.py tests/web/test_models_credentials.py
git add src/mudidi/web/models.py tests/web/test_models_credentials.py
git commit -m "feat: paginate provider model discovery"
```

---

### Task 2: Add the cached stage-aware catalog service

**Files:**
- Modify: `src/mudidi/web/models.py`
- Create: `tests/web/test_model_catalog_service.py`

**Interfaces:**
- Consumes: `ModelCatalog`, `ModelDiscovery`, `Provider`, and a resolver returning an object with `get_secret_value() -> str` or `None`.
- Produces:
  - `CatalogStage(StrEnum)` with `STAGE1`, `STAGE2`, `VERIFICATION`.
  - `CatalogAuthMode(StrEnum)` with `API_KEY`, `SUBSCRIPTION`.
  - immutable `CatalogItem`, `CatalogWarning`, and `CatalogResult` dataclasses.
  - `ModelCatalogService.list_models(provider, *, stage, auth_mode, force_refresh=False) -> CatalogResult`.
  - `ModelCatalogService.invalidate(provider) -> None`.
  - `CatalogResult.to_payload() -> dict[str, object]` containing only non-secret fields.

- [ ] **Step 1: Add failing recommendation/subscription tests**

Create `tests/web/test_model_catalog_service.py` with a small bundled catalog and fake discovery. Verify:

```python
def test_live_catalog_groups_stage_recommendations_and_available_models() -> None:
    result = service.list_models(
        Provider.OPENAI,
        stage=CatalogStage.STAGE1,
        auth_mode=CatalogAuthMode.API_KEY,
    )
    assert [item.model_id for item in result.recommended] == ["openai/gpt-recommended"]
    assert [item.model_id for item in result.available] == ["openai/gpt-account-only"]
    assert result.source == "live"
    assert result.stale is False
```

Add tests proving a bundled model absent from the live account list is not recommended; fallback groups remaining bundled models under Available; and subscription mode returns only curated subscription models without calling the credential resolver or discovery.

Represent curated subscription entries in `ModelCatalog` rather than template strings. Include the currently supported defaults: `openai/gpt-5.6-terra`, `gemini/gemini-2.5-pro`, and `anthropic/claude-sonnet-4-6`, with `stage1`, `stage2`, and `verification` recommendations matching the corresponding subscription backend capabilities.

- [ ] **Step 2: Run grouping tests and verify RED**

```bash
uv run --locked --extra dev --extra web pytest tests/web/test_model_catalog_service.py -q
```

Expected: import errors for the new catalog service/types.

- [ ] **Step 3: Implement result types and deterministic grouping**

Use frozen slotted dataclasses. `CatalogItem` contains exactly `model_id`, `display_name`, and `compatibility` (`verified` or `unverified`). `CatalogResult.to_payload()` emits ISO-8601 UTC timestamps and converts tuples to lists. Group by normalized ID, preserve deterministic model-ID ordering, and place bundled/live overlaps only in Recommended when the stage matches `recommended_for`.

- [ ] **Step 4: Add failing cache and secret-isolation tests**

Use a mutable UTC clock and two `ResolvedCredential`-shaped fakes. Cover:

- second call before 15 minutes performs no discovery and reports `cached`;
- call at 15 minutes refreshes;
- `force_refresh=True` refreshes before expiry;
- a second credential cannot receive the first credential’s cached list;
- `invalidate(provider)` removes every fingerprint entry for that provider;
- refresh failure returns the last success as `stale`;
- first failure and missing credential return bundled with stable warnings;
- `repr(result)`, `result.to_payload()`, warning text, and raised errors omit raw key and raw provider error.

- [ ] **Step 5: Implement credential-isolated cache behavior**

Use `threading.RLock`, `datetime.now(UTC)` injection, `secrets.token_bytes(32)`, and keyed `hashlib.blake2b(..., digest_size=16, key=process_key)` for the internal fingerprint. Keep the raw key in a local variable only long enough to fingerprint and call discovery. Store only model tuples and timestamps in cache entries.

Fresh successful live data reports `live` on the network request and `cached` on a cache hit. Failed forced/stale refresh returns the retained entry with `source="stale"`, `stale=True`, and a fixed `provider_unavailable` warning. Missing credentials and first failures return bundled recommendations with `source="bundled"`.

- [ ] **Step 6: Run service tests and commit**

```bash
uv run --locked --extra dev --extra web pytest tests/web/test_model_catalog_service.py tests/web/test_models_credentials.py -q
uv run --locked --extra dev --extra web ruff check src/mudidi/web/models.py tests/web/test_model_catalog_service.py
git add src/mudidi/web/models.py tests/web/test_model_catalog_service.py
git commit -m "feat: add cached stage-aware model catalog"
```

---

### Task 3: Expose the catalog and integrate credential invalidation

**Files:**
- Modify: `src/mudidi/web/app.py:180-270, 344-404, 1851-1909`
- Modify: `src/mudidi/web/models.py`
- Create: `tests/web/test_model_catalog_routes.py`
- Modify: `tests/web/test_models_credentials.py`

**Interfaces:**
- Consumes: `ModelCatalogService.list_models()` and `invalidate()` from Task 2; existing `CredentialVault.resolve()`.
- Produces: `GET /models/{provider_name}` with `stage`, `auth_mode`, and `force` query parameters and `Cache-Control: no-store`.

- [ ] **Step 1: Add failing endpoint contract tests**

Build the app with a fake discovery and local credential store. Save a key through the existing credential route, then assert:

```python
response = client.get(
    "/models/openai",
    params={"stage": "stage1", "auth_mode": "api_key"},
)
assert response.status_code == 200
assert response.headers["cache-control"] == "no-store"
assert response.json()["recommended"][0] == {
    "model_id": "openai/gpt-recommended",
    "display_name": "gpt-recommended",
    "compatibility": "verified",
}
```

Add parameterized 4xx tests for unknown provider, custom provider discovery, invalid stage/auth mode, subscription OpenRouter/custom, and `force=true` in subscription mode. Add a degraded 200 test for a missing key and a response-body assertion that secret and raw provider exception strings are absent.

- [ ] **Step 2: Run route tests and verify RED**

```bash
uv run --locked --extra dev --extra web pytest tests/web/test_model_catalog_routes.py -q
```

Expected: 404 because the route does not exist.

- [ ] **Step 3: Construct the service and add the route**

In `create_app()`, construct one service after the credential vault and bundled catalog exist. The resolver must return `app.state.credential_vault.resolve(provider)` directly; `models.py` accesses the narrow `get_secret_value()` protocol.

Use a synchronous FastAPI handler so blocking provider HTTP runs in FastAPI’s worker thread:

```python
@app.get("/models/{provider_name}")
def provider_models(
    provider_name: str,
    stage: CatalogStage,
    auth_mode: CatalogAuthMode,
    force: bool = False,
) -> JSONResponse:
    # validate provider/auth combinations
    result = app.state.model_catalog_service.list_models(
        provider,
        stage=stage,
        auth_mode=auth_mode,
        force_refresh=force,
    )
    return JSONResponse(result.to_payload(), headers={"Cache-Control": "no-store"})
```

Convert only fixed validation failures to 4xx responses. Degraded discovery remains a successful catalog result.

- [ ] **Step 4: Add failing save/delete invalidation tests**

Prime a live entry, save a replacement key, and assert the next model lookup invokes discovery for the new credential. Prime again, delete the credential, and assert lookup uses bundled fallback without discovery. Do not make credential save depend on discovery success.

- [ ] **Step 5: Implement invalidation hooks and remove dead live state**

Call `app.state.model_catalog_service.invalidate(provider)` only after successful credential save/delete. Remove `app.state.live_models` and change `_all_models()` to return bundled first-paint options plus curated subscription options without mutable live state. Deduplicate by model ID.

- [ ] **Step 6: Run route and credential tests and commit**

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_model_catalog_routes.py \
  tests/web/test_models_credentials.py \
  tests/web/test_app.py -q
uv run --locked --extra dev --extra web ruff check src/mudidi/web/app.py src/mudidi/web/models.py tests/web/test_model_catalog_routes.py
git add src/mudidi/web/app.py src/mudidi/web/models.py tests/web/test_model_catalog_routes.py tests/web/test_models_credentials.py
git commit -m "feat: expose account model catalogs"
```

---

### Task 4: Rebuild stage dropdowns and filter authentication panels

**Files:**
- Modify: `src/mudidi/web/templates/home.html:299-367, 398-498`
- Modify: `src/mudidi/web/static/app.js:478-637, 891-967, 1180-1310`
- Modify: `src/mudidi/web/static/app.css`
- Modify: `tests/web/test_theme.py`
- Create: `tests/web/test_dynamic_model_ui.py`

**Interfaces:**
- Consumes: Task 3 JSON payload and existing provider/auth-mode controls.
- Produces: model selects marked with `data-model-stage`; `data-model-refresh`; `data-model-status`; mode/provider-filtered credential and subscription fieldsets/cards; browser functions `loadModelCatalog()`, `applyModelCatalog()`, and `synchronizeCredentialPanels()`.

- [ ] **Step 1: Add failing server-rendered UI contract tests**

Use `TestClient` and the rendered home HTML to assert:

- Stage 1 has `data-model-stage="stage1"`.
- both Stage 2 pass selects have `data-model-stage="stage2"`.
- verification selects have `data-model-stage="verification"` where applicable.
- one refresh button and one `role="status"` live region exist.
- API and subscription fieldsets have stable data hooks.
- subscription model options are rendered from catalog data, not literal one-off template IDs.

Add source-contract assertions that JavaScript uses `AbortController`, creates Recommended/Available optgroups, preserves a selected value, and applies `hidden` to both fieldsets and nonselected cards.

- [ ] **Step 2: Run UI contract tests and verify RED**

```bash
uv run --locked --extra dev --extra web pytest tests/web/test_dynamic_model_ui.py tests/web/test_theme.py -q
```

Expected: missing data hooks, refresh control, optgroup behavior, and panel synchronization.

- [ ] **Step 3: Update template structure and first-paint data**

Add `data-model-stage` to every model select. Replace duplicated literal subscription-default options with the catalog-provided first-paint list. Add a single model discovery toolbar containing Refresh models and an `aria-live="polite"` status. Mark the API fieldset with `data-api-credential-entry`; retain `data-subscription-entry`; keep provider data attributes on every card.

The initial HTML must remain usable without JavaScript: bundled provider options and Other are present in each select, API-key mode is visible, Subscription Accounts is initially hidden, and only the default Gemini credential card is initially visible.

- [ ] **Step 4: Implement request ordering and dropdown rebuilding**

Change OpenRouter from forced manual-only mode to discovered dropdown plus Other; keep only `custom` forced to manual entry.

`loadModelCatalog({force = false})` must:

1. derive provider, auth mode, and subscription mapping;
2. skip custom and show the manual-entry state;
3. abort the previous request;
4. fetch each distinct effective stage once using `Promise.all`;
5. ignore aborted/outdated results;
6. call `applyModelCatalog(select, payload)` for every matching select;
7. render fixed safe status copy from `source`/`warning.code`.

`applyModelCatalog()` captures `select.value`, replaces only generated model options/optgroups, retains Other, restores the old value when present, otherwise selects the first recommended, first available, then Other. Create option text with `textContent`, never HTML interpolation.

- [ ] **Step 5: Implement mode/provider card filtering**

` synchronizeCredentialPanels()` must:

```javascript
const subscription = authModeValue() === "subscription";
apiCredentialEntry.hidden = subscription;
subscriptionEntry.hidden = !subscription;
credentialCards.forEach((card) => {
  card.hidden = subscription || card.dataset.provider !== providerValue.value;
});
subscriptionCards.forEach((card) => {
  card.hidden = !subscription || card.dataset.subscriptionProvider !== (authProvider?.value || "");
});
```

Also disable hidden card inputs/buttons and restore their prior enabled state through the existing status synchronization rather than deleting state. Call this function on initial load, auth-mode change, model-provider change, and subscription-provider change.

After successful key save call `loadModelCatalog({force: true})`. After deletion call normal loading so missing-key bundled fallback is displayed. Subscription login/logout refreshes the curated subscription list without API discovery.

- [ ] **Step 6: Add focused styling and run UI tests**

Style the discovery toolbar, status states, and optgroups using existing spacing/color tokens. Do not redesign unrelated controls. Verify `hidden` remains authoritative at narrow widths.

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_dynamic_model_ui.py \
  tests/web/test_theme.py \
  tests/web/test_run_form.py \
  tests/web/test_app.py -q
uv run --locked --extra dev --extra web ruff check tests/web/test_dynamic_model_ui.py tests/web/test_theme.py
git add src/mudidi/web/templates/home.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css tests/web/test_dynamic_model_ui.py tests/web/test_theme.py
git commit -m "feat: load dynamic stage model options"
```

---

### Task 5: Preserve preset/custom behavior and verify end to end

**Files:**
- Modify if required by failing behavior: `src/mudidi/web/static/app.js`
- Modify if required by failing behavior: `src/mudidi/web/templates/home.html`
- Modify: `tests/web/test_run_form.py`
- Modify: `tests/web/test_production_routes.py`
- Test: all files changed in Tasks 1-4

**Interfaces:**
- Consumes: completed server and browser catalog contracts.
- Produces: verified end-to-end model selection with saved presets, custom IDs, provider/auth changes, and degraded discovery.

- [ ] **Step 1: Add regression cases for disappearing and custom models**

Add route/form tests proving a submitted or preset model not present in the current catalog still resolves through the existing custom model path and reaches `InferenceConfig`. Add a rendered-state test proving rejected form values remain available rather than silently switching to the first recommendation.

- [ ] **Step 2: Run the regression cases and make only behavior-required fixes**

```bash
uv run --locked --extra dev --extra web pytest \
  tests/web/test_run_form.py \
  tests/web/test_production_routes.py \
  tests/web/test_dynamic_model_ui.py -q
```

If a current-but-absent selection is missing, insert one temporary generated option labeled `<model id> — current selection` before restoring it. Do not add a server execution allowlist or migration shim.

- [ ] **Step 3: Run complete automated verification**

```bash
uv run --locked --extra dev --extra web ruff format src/mudidi/web/models.py src/mudidi/web/app.py tests/web/test_models_credentials.py tests/web/test_model_catalog_service.py tests/web/test_model_catalog_routes.py tests/web/test_dynamic_model_ui.py tests/web/test_run_form.py tests/web/test_production_routes.py
uv run --locked --extra dev --extra web ruff check src/mudidi/web/models.py src/mudidi/web/app.py tests/web/test_models_credentials.py tests/web/test_model_catalog_service.py tests/web/test_model_catalog_routes.py tests/web/test_dynamic_model_ui.py tests/web/test_run_form.py tests/web/test_production_routes.py
uv run --locked --extra dev --extra web pytest tests/web -q
```

Expected: Ruff exits zero and all web tests pass. Existing SWIG and Starlette deprecation warnings may remain; no new warning is accepted.

- [ ] **Step 4: Browser-drive the actual model page**

Start the FastAPI application with deterministic fake discovery responses and use Chromium to verify at desktop and narrow viewport widths:

- API-key mode shows only the selected provider API card.
- subscription mode shows only the selected subscription account card.
- OpenAI, Anthropic, Gemini, and OpenRouter provider changes load grouped options.
- Stage 1 and Stage 2 recommendations differ according to `recommended_for`.
- current selections survive refresh.
- forced refresh visibly enters loading then updated state.
- missing credential and simulated provider failure leave usable bundled/stale options with safe status copy.
- Other model entry reveals and preserves manual input.

Capture observations from the rendered DOM and screenshots; do not treat source assertions as visual proof.

- [ ] **Step 5: Request review and fix all Critical/Important findings**

Dispatch a reviewer limited to the changed source/tests. Ask specifically about secret leakage, credential cache isolation, blocking network placement, pagination bounds, stale fallback, race ordering, accessibility, preset restoration, and divergent auth/provider state. Add a failing regression before each accepted behavior fix, then rerun the focused suite.

- [ ] **Step 6: Commit the verified implementation**

```bash
git add src/mudidi/web/models.py src/mudidi/web/app.py src/mudidi/web/templates/home.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css tests/web
git commit -m "feat: add dynamic account model catalogs"
git status --short --branch
```

The final status may contain only explicitly identified concurrent/user-owned files; none may be included in the implementation commit.
