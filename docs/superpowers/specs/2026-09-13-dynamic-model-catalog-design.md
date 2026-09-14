# Dynamic Model Catalog Design

## Goal

Make the model configuration page account-aware without weakening Mudidi's credential boundary or making the page depend on provider availability. API-key mode should discover the models available to the selected account, group curated recommendations separately from unverified available models, and refresh through a short-lived cache. Subscription mode should use a curated compatibility catalog rather than private provider web-app endpoints. The page should display only the credential or subscription account relevant to the selected authentication mode and provider.

## Current State

`ModelCatalog.bundled()` supplies a static catalog to every Stage 1, Stage 2, and agentic model select. The browser renders the same list into each select, filters options by the globally selected provider, and chooses defaults hardcoded in `app.js`. Although `ModelDiscovery` can call official provider list endpoints, production code never invokes it: `app.state.live_models` is initialized empty and only read. The `recommended_for` metadata is not used by the UI. API Credentials and Subscription Accounts are both rendered regardless of the selected authentication mode.

## Scope

This change covers:

- Official API model discovery for OpenAI, Anthropic, Google Gemini, and OpenRouter when API-key billing is selected.
- Fifteen-minute account-aware in-memory caching, forced refresh, and last-successful stale fallback.
- Stage-specific Recommended and Available model groups for Stage 1, both Stage 2 passes, and agentic verification controls.
- A curated model response for OpenAI, Google Gemini, and Claude subscription modes without scraping private web applications.
- Authentication panel and provider-card filtering.
- Preservation of custom model entry and provider-neutral run configuration.

This change does not:

- Scrape consumer provider web applications or private listing endpoints.
- Persist provider account entitlements or credential-derived cache identities to disk.
- Turn discovered models into a server-side execution allowlist.
- Promise that an API model list matches a consumer subscription web-app menu.
- Add account rotation, model benchmarking, pricing recommendations, or automated quality ranking.

## Architecture

### Model catalog service

Add a `ModelCatalogService` in `src/mudidi/web/models.py`. It composes the existing bundled catalog, `ModelDiscovery`, a credential resolver, a clock, and a per-process random cache-key secret. The service is the only application component that knows whether a response came from live discovery, a stale cache entry, or the bundled fallback.

The service API accepts:

- normalized `Provider`;
- logical stage: `stage1`, `stage2`, or `verification`;
- authentication mode: `api_key` or `subscription`;
- `force_refresh`.

It returns a non-secret immutable result containing provider, stage, source, stale state, fetch timestamp, recommended options, available options, and an optional safe warning code/message.

### Cache identity and lifetime

API-key cache entries are keyed by provider plus a keyed BLAKE2/HMAC fingerprint of the resolved credential. The random key exists only for the process lifetime. The raw credential and fingerprint are never returned, logged, or persisted. This prevents a model list discovered for one key from being served after another key replaces it.

A successful entry is fresh for 15 minutes. Normal lookup returns it without network access. Forced lookup bypasses freshness and attempts discovery. A failed refresh retains the last successful entry and returns it as stale with a safe warning. If no prior success exists, the service returns the bundled provider catalog. Missing credentials return the bundled catalog with a `missing_credential` warning and do not call the provider.

Saving or deleting a provider credential invalidates all cache entries for that provider. The browser requests a forced refresh immediately after a successful save. Restarting the application starts with an empty cache and performs lazy discovery; no database migration is required.

### Subscription catalog

Subscription mode never resolves an API key and never performs model discovery. The service returns curated models known to work with the corresponding Mudidi subscription backend. Existing hardcoded subscription model options move out of the template and JavaScript into the catalog source of truth. Provider mapping remains:

- OpenAI subscription to `openai` models;
- Google subscription to `gemini` models;
- Claude subscription to `anthropic` models.

The response source is `curated_subscription`, has no stale state, and has no refresh action. Unsupported subscription providers fail validation before lookup.

## Provider Discovery

`ModelDiscovery` retains fixed HTTPS endpoints and the existing host allowlist, five-second timeout, and five-MiB response limit.

- OpenAI consumes `GET /v1/models`.
- OpenRouter consumes its image-input-filtered model endpoint.
- Anthropic follows `has_more` and `last_id` using `after_id`, with a bounded page count.
- Gemini follows `nextPageToken`, with a bounded page count.

Every page is normalized before aggregation. IDs are deduplicated and sorted deterministically. Pagination stops on a missing token, an empty page, a repeated token, or the page bound. Repeated tokens and malformed pagination data produce a safe discovery failure rather than an infinite loop.

Clearly incompatible utility families are excluded before presentation, including embeddings, moderation, speech, transcription-only, audio-only, image-generation, reranking, and safety-classifier models. Known curated entries carry verified image/stage metadata. Other provider-returned generative models remain selectable under Available with `compatibility: unverified`; the UI does not claim they meet Mudidi's multimodal requirements.

## Recommendation and Grouping Rules

The bundled catalog remains the recommendation overlay. For a requested stage:

1. A bundled model appears in Recommended when its provider matches, its `recommended_for` contains the requested stage, and it is present in the live account list.
2. A bundled model absent from the live account list is not recommended in API-key mode.
3. Live generative models not recommended for that stage appear in Available.
4. When discovery is unavailable and bundled fallback is used, the provider's stage-recommended bundled models appear in Recommended and its remaining bundled models appear in Available.
5. Subscription mode returns only curated subscription-compatible models, grouped by stage recommendation.

Stage 2 Pass 1 and Pass 2 both map to `stage2`. Agentic evaluator/rewriter controls map to their effective execution stage, including `verification` where applicable. Model IDs remain normalized with the existing LiteLLM provider prefixes.

## HTTP Contract

Add:

`GET /models/{provider_name}`

Query parameters:

- `stage=stage1|stage2|verification`;
- `auth_mode=api_key|subscription`;
- `force=true|false`, accepted only for API-key mode.

Successful and degraded responses use HTTP 200 so the browser can render the supplied fallback. The JSON object contains:

```json
{
  "provider": "openai",
  "stage": "stage1",
  "auth_mode": "api_key",
  "source": "live",
  "stale": false,
  "fetched_at": "2026-09-13T00:00:00Z",
  "recommended": [
    {
      "model_id": "openai/gpt-example",
      "display_name": "gpt-example",
      "compatibility": "verified"
    }
  ],
  "available": [],
  "warning": null
}
```

Warnings use stable codes and safe fixed messages. Provider payloads, raw exceptions, credentials, and credential fingerprints are excluded. Responses include `Cache-Control: no-store`. Unknown providers, stages, auth modes, unsupported subscription providers, and invalid force combinations return a typed 4xx response.

## Application Integration

`create_app()` constructs the catalog service using the existing model-discovery dependency and credential vault. The credential routes invalidate provider cache state after successful save and delete. The save response remains successful even when the following browser-initiated discovery fails; credential persistence and optional provider discovery are separate operations.

The home page continues to receive bundled catalog data for a useful first paint and offline operation. `_all_models()` no longer relies on an externally mutated `app.state.live_models` dictionary; live results flow through the catalog service and JSON endpoint.

Run form validation continues to normalize custom models and accepts selections supplied by either catalog group. A disappearing or newly unavailable catalog entry does not invalidate an already saved preset; it is restored as a custom/current selection with an explanatory label when necessary.

## Browser Behavior

Each model select declares its logical stage. The browser maintains one catalog snapshot for the active provider/authentication mode and rebuilds all relevant selects with:

- `Recommended for this stage` optgroup;
- `Available from your account — compatibility unverified` optgroup when present;
- the permanent `Other model…` option.

When a fetch completes, the browser preserves each current selection if it remains present. Otherwise it selects the first recommended option, then the first available option, then Other. Responses are ignored if the user changed provider/authentication mode before the request completed. A request sequence token or abort controller prevents an older response from overwriting newer state.

A single Refresh models button is visible for supported API-key providers. It forces a server refresh and updates all model selects. The adjacent live region reports loading, live, cached, stale, bundled, missing-credential, and error states without exposing provider details.

After a key is saved, the browser forces refresh for that provider. After deletion, it clears dynamic options and loads bundled fallback. OpenRouter now uses its discovered model list and retains manual routing through Other model; the `custom` provider remains manual-entry only.

## Authentication Panel Filtering

API-key mode:

- displays the API Credentials fieldset;
- hides the Subscription Accounts fieldset;
- displays only the credential card matching the selected model provider;
- displays an explanatory no-key-required/manual-routing state for `custom` rather than another provider's card.

Subscription mode:

- hides the API Credentials fieldset;
- displays the Subscription Accounts fieldset;
- keeps the subscription provider selector visible before login;
- displays only the card matching the selected subscription provider;
- updates the global model provider through the existing OpenAI/Google/Claude mapping.

Hidden controls are disabled where they participate in forms or actions, and restored when shown. Hiding is presentation-only: switching mode/provider never deletes credentials or logs out a subscription. Initial server-rendered state and rejected-form/preset restoration must produce the same visibility as subsequent JavaScript changes.

## Error Handling and Security

- All provider requests remain server-side.
- Model-list requests use fixed allowlisted HTTPS hosts; provider redirects outside the allowlist fail closed.
- Response sizes, timeouts, pagination pages, and duplicate tokens are bounded.
- Credential values are only passed through secret-bearing resolver/request boundaries.
- Cache keys are keyed, process-local fingerprints; raw keys are not dictionary keys.
- Exceptions are normalized to fixed warning codes/messages before reaching JSON or UI.
- A failed forced refresh cannot erase the last successful list.
- Browser responses use `no-store` and contain only model/display/capability metadata.
- Custom model entry remains an explicit escape hatch and is visually separated from verified recommendations.

## Testing Strategy

### Unit tests

- Provider endpoint selection, headers, normalization, pagination, repeated-token defense, deduplication, and incompatible-family filtering.
- Stage-specific recommendation overlay and API-key/subscription grouping.
- Fresh cache hit, 15-minute expiry, forced refresh, credential fingerprint separation, save/delete invalidation, stale success retention, missing-key fallback, and exception redaction with a controllable clock.

### Route tests

- Endpoint schema, parameter validation, unsupported subscription provider behavior, `Cache-Control: no-store`, degraded HTTP 200 responses, and absence of credentials/raw provider errors.
- Credential save/delete invalidation integration.
- Home context and preset restoration remain valid with bundled first-paint options.

### UI tests

- API-key and subscription fieldset visibility.
- Exactly one selected-provider card visible in each mode.
- Recommended and Available optgroups for Stage 1, both Stage 2 passes, and agentic controls.
- Selection preservation, fallback selection, Other entry, forced refresh, stale warning, loading state, and out-of-order response protection.
- Existing run-form, subscription login/logout, credentials, theme, and production route regressions.

### Browser verification

Run the application with deterministic fake discovery data and drive the model page at desktop and narrow viewports. Verify provider changes, authentication-mode changes, selected-card visibility, optgroup contents, selection preservation, manual refresh, and degraded fallback on the rendered surface.

## Acceptance Criteria

- Selecting an API-key provider loads account-accessible models from its official API at most once per 15-minute cache window unless manually refreshed.
- Stage model dropdowns distinguish curated recommendations from unverified available models and use stage-specific recommendation metadata.
- Provider discovery failure never makes the page unusable and never discards the last successful list.
- Subscription mode uses only curated compatibility models and no private web-app discovery.
- Only the active authentication panel and selected provider card are visible.
- Credentials, credential fingerprints, raw provider payloads, and raw exceptions never appear in browser responses, logs, cache persistence, or model objects.
- Existing custom model and run submission behavior remains compatible.
