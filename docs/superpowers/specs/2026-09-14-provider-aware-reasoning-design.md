# Provider-Aware Reasoning and Subscription Catalog Design

## Goal

Give every MUDIDI inference role a model-aware reasoning selector using the same portable effort taxonomy as Oh My Pi, while preserving correct provider wire behavior. Subscription dropdowns must contain every model advertised to the authenticated account, contain no manual-entry escape hatch, and order the newest models first.

## Approved Product Decisions

- Scope is MUDIDI's supported providers: OpenAI, Anthropic/Claude, Google Gemini, and OpenRouter, plus the existing advanced custom provider.
- The portable reasoning ladder is `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`, ordered from least to most intensive.
- Known models expose only their supported levels.
- Unknown and manually entered API-key/custom models expose all six levels. There is no provider-default-only mode.
- Provider-specific levels outside the portable taxonomy are excluded. In particular, Codex's currently advertised `ultra` preset is not exposed because Oh My Pi's portable taxonomy ends at `max`.
- Subscription mode has no `Other model…` option and no manual model field. Every selectable subscription model must come from that authenticated provider's catalog.
- API-key and advanced custom routing retain manual model entry.
- Subscription model lists are flat account-available lists ordered newest first. Stage recommendation groups must not place an older model above a newer available model.

This design supersedes the subscription-catalog and permanent-Other clauses in `2026-09-13-dynamic-model-catalog-design.md`. API-key discovery, caching, and manual-entry behavior from that design remain in force unless changed below.

## Why a Global Six-Option Dropdown Is Insufficient

Providers expose different effort ladders for different models. The authenticated Codex catalog currently advertises `low/medium/high/xhigh/max` for GPT-5.6 Luna and Terra, while GPT-5.5 stops at `xhigh`. Google Cloud Code Assist advertises Gemini 3.6–3.8 Flash as separate low, medium, and high wire SKUs; `xhigh` and `max` are not valid Gemini Flash wire IDs. Claude generations use either fixed thinking budgets or adaptive thinking plus top-level effort.

Oh My Pi handles this with model metadata: a normalized effort ladder, per-model supported efforts, explicit effort mappings, and variant routing. MUDIDI should adopt that architecture for its provider boundary rather than copy the full OMP provider catalog or send every value blindly.

## Canonical Reasoning Model

Add one provider-neutral reasoning module under `mudidi.llm`. It is the only definition of effort ordering and compatibility behavior used by configuration, CLI, web catalogs, API-key requests, and subscription backends.

The canonical enabled levels are:

```text
minimal < low < medium < high < xhigh < max
```

A reasoning profile contains:

- ordered supported levels;
- an optional advertised default level used to initialize a selector;
- whether reasoning can be disabled;
- an optional portable-level-to-wire-level map;
- an optional portable-level-to-wire-model-ID map;
- the source of the capability: provider metadata, reviewed rule, or unknown.

`none` is not an enabled effort level. Existing persisted `none` values retain their previous practical meaning by resolving to the selected model's lowest supported effort. For an unknown model they resolve to `low`. New selectors show Off only when a known model explicitly supports disabling reasoning; they do not show a generic Provider default option.

Unknown/manual API-key models receive an unknown profile whose supported ladder contains all six portable levels and whose initial value is `low`.

## Capability Resolution

Capability resolution follows a strict precedence order:

1. authenticated or API-key provider metadata that explicitly lists supported effort levels;
2. effort-qualified model variants advertised by the provider;
3. reviewed MUDIDI compatibility rules for that provider and model family;
4. the approved unknown-model profile containing all six levels.

Provider data is validated against the canonical taxonomy. Unknown values are ignored rather than forwarded. Empty or malformed advertised arrays do not erase a valid reviewed rule.

Reviewed rules cover the model families exposed by MUDIDI's four providers. They are data, not request-time ID conditionals spread across transports. A model's resolved profile is baked once when its catalog option is built and returned to callers; browser and transport behavior must not independently infer conflicting capabilities.

### OpenAI and Codex

The Codex subscription parser retains `default_reasoning_level` and each recognized member of `supported_reasoning_levels`. Current object-shaped entries such as `{ "effort": "xhigh" }` are supported. Unknown values such as `ultra` are ignored.

Direct OpenAI API discovery does not consistently expose effort ladders, so reviewed OpenAI model-family rules supply known profiles. Newly discovered unmatched models receive the approved all-six unknown profile.

OpenAI-style requests send the resolved wire effort unchanged unless the profile contains an explicit map.

### Google Gemini

Cloud Code Assist effort-qualified IDs are collapsed into one logical model while preserving the discovered routing table. If low, medium, and high variants exist, those become the profile's supported levels and wire IDs. The existing `-tiered` alias remains folded into the logical model and contributes no additional level.

Reviewed Gemini API-key rules distinguish level-based Gemini 3 models from budget-based Gemini 2.5 models. Unsupported high-end portable levels are omitted from known-model selectors. Explicit maps handle cases such as Gemini 3.6+ Flash `minimal` routing through the low SKU.

### Anthropic and Claude

Claude subscription discovery uses the authenticated Anthropic `GET /v1/models` endpoint, following bounded pagination. It retains model ID, display name, and `created_at`. The endpoint was verified with the current Claude OAuth credential and returned the full account-visible list, including Fable 5.1, Opus 5, Sonnet 5, Fable 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Opus 4.5, Haiku 4.5, and Sonnet 4.5.

Reviewed Claude lineage rules assign each returned model its budget or adaptive-thinking profile. Adaptive models send the compatible `thinking` shape and top-level output effort; older budget models send bounded thinking budgets. `xhigh` and `max` appear only where that model lineage accepts them.

Anthropic API-key discovery uses the same model IDs and `created_at` ordering metadata, with the same reviewed profiles.

### OpenRouter

OpenRouter discovery retains explicit reasoning support metadata when present and classifies the upstream model slug through the reviewed provider/model rules. A model that advertises reasoning but no level vocabulary receives its reviewed family profile when one exists, otherwise the approved all-six unknown profile.

Provider routing and reasoning capability remain separate: choosing an OpenRouter upstream does not change the normalized MUDIDI model ID or leak provider response data to persisted credentials.

## Model Catalog Contract

Replace tuple-shaped subscription model results with a non-secret structured model record. The common live/catalog model representation carries:

- normalized model ID;
- display name;
- provider;
- image-input compatibility when known;
- reasoning profile;
- optional provider release timestamp;
- optional provider priority/order used only as a deterministic recency fallback.

The browser catalog item includes a JSON-safe reasoning profile and no secrets or raw provider payload. Catalog caches store the structured records so every selector receives the same capabilities and order.

Malformed capability metadata degrades only that model to reviewed or unknown capability resolution. It does not discard an otherwise valid provider model list.

## Subscription Catalog Behavior

OpenAI, Google, and Claude subscription modes all use authenticated provider catalogs:

- OpenAI uses Codex account discovery and includes every visible model row.
- Google uses Cloud Code Assist discovery and includes every logical Gemini model produced after effort-variant and Tiered-alias collapse.
- Claude uses the authenticated Anthropic models endpoint and includes every valid returned Claude model.

Subscription responses do not apply a curated allowlist or stage-recommendation filter. Compatibility metadata may still be shown as supporting text, but every account-advertised model remains selectable.

The subscription browser flow:

1. replaces the dropdown with the complete authenticated provider catalog;
2. orders it newest first;
3. preserves the current selection only if it still exists in that catalog;
4. otherwise selects the newest available model and announces that the saved selection is no longer available;
5. never appends `Other model…`, a custom input, or a synthetic stale-selection option.

Server-side form validation enforces the same boundary. A subscription submission cannot use `__other__`, a custom-model field, a model belonging to another provider, or a model absent from the authenticated/stale-success catalog. A transient refresh failure may use the last successful cached catalog; it must not convert arbitrary user input into a subscription model.

API-key mode keeps curated/live groups, current-selection restoration, and `Other model…`. The advanced custom provider remains manual-only.

## Newest-First Ordering

Sorting must not use descending strings: lexical ordering places versions such as `3.10` below `3.9`.

Each model receives a provider-neutral recency key using this precedence:

1. provider release or creation timestamp, descending;
2. provider-authored priority/order when its semantics are defined;
3. parsed numeric revision and dated-snapshot components from the canonical model ID, compared component by component;
4. normalized display name and model ID as deterministic tie-breakers.

Provider timestamps are parsed strictly and invalid values are treated as absent. Numeric revisions are tuples rather than floating-point values. Preview/date suffixes participate in ordering without making undocumented quality claims.

Subscription mode returns one flat newest-first sequence. API-key Recommended and Available groups retain their meaning, but each group uses the same newest-first comparator.

Acceptance examples:

- Gemini 3.8 precedes 3.7, 3.6, 3.5, and 3.1.
- A future Gemini 3.10 precedes 3.9.
- Claude Fable 5.1 precedes models with earlier `created_at` values.
- Codex uses its provider metadata and numeric family revision; equal-revision variants have deterministic stable ordering.

## Selector Behavior

Every Stage 1, Stage 2 Pass 1, Stage 2 Pass 2, evaluator, and rewriter model control owns a paired reasoning control.

When the model changes, the browser rebuilds the reasoning options from the selected catalog item's profile. It preserves the selected effort when supported. Otherwise it chooses the nearest supported level no greater than the previous level, falling back to the model's advertised default and then its lowest supported level.

Unknown/manual API-key models show all six levels. Known non-reasoning models disable the reasoning control. Models that explicitly support reasoning-off add an Off choice; no generic Provider default choice is added.

The server repeats compatibility resolution and never trusts option metadata submitted by the browser. Presets and rejected forms store only the normalized reasoning choice, not provider capability claims.

## Request-Time Safety

The resolved profile is authoritative for known models. Wire routing applies its model-ID and effort maps before building provider payloads.

For OpenAI-compatible endpoints, a single reactive fallback is permitted only when all of these conditions hold:

- the response is HTTP 400 or 422;
- the error explicitly identifies the reasoning-effort field or value;
- the error lists recognized allowed levels;
- the original request produced no model output.

The retry selects the nearest allowed enabled level, with the same special cases used by OMP (`minimal` to `low`, `xhigh` to `max` or `high`, and `max` to `xhigh`). It runs at most once. Unrelated 4xx errors, ambiguous messages, and errors without a recognized allowed set are returned unchanged.

This fallback reduces failures for newly changed provider catalogs but cannot guarantee that an opaque unknown endpoint accepts every offered level. Under the approved unknown-model policy, MUDIDI surfaces an explicit provider error rather than silently removing reasoning or switching to a provider default.

## Configuration and Persistence

One canonical reasoning type replaces the duplicated Literals and argparse lists in the LLM client, YAML models, agentic config, web forms, and CLI commands.

All stage, split-pass, evaluator, and rewriter fields accept the full portable ladder. Existing defaults remain low unless a selected known model advertises a different initial default in the browser. Persisted runs retain their resolved effort for reproducibility.

Existing `none` preset/form values are normalized to the model's lowest supported effort when loaded. New writes do not emit `none` unless Off is explicitly selected for a model that supports it. Documentation and examples use the canonical names.

No credential, capability payload, or provider error body is added to persisted run configuration.

## Error Handling and Security

- Model and capability discovery use existing fixed HTTPS allowlists, bounded response sizes, timeouts, pagination limits, redirect checks, and credential-safe error normalization.
- Claude subscription model discovery sends OAuth bearer authentication and the established Claude CLI-compatible headers; it never sends an API key.
- Capability parsing accepts only recognized scalar fields and canonical effort names.
- Browser responses contain only model display/capability/recency metadata required by the UI.
- Subscription model validation uses server-owned catalog records, not hidden form fields.
- Refresh failure preserves the last successful model list and its reasoning profiles.
- Unknown model behavior is explicit: all levels are offered, but ambiguous provider rejection is not hidden or retried repeatedly.

## Testing Strategy

### Reasoning unit tests

- Canonical ordering and validation for minimal through max.
- Known provider/model profiles and explicit effort mappings.
- Unknown-model all-six behavior.
- Unsupported stale-value selection and `none` migration.
- Malformed/unknown provider metadata fallback.

### Catalog tests

- Codex object-shaped supported levels and ignored `ultra` values.
- Google effort-SKU collapse, routing metadata, and Tiered-alias deduplication.
- Claude OAuth models discovery, pagination, timestamp parsing, and secret-safe failures.
- OpenRouter reasoning metadata plus reviewed/unknown resolution.
- Structured capability cache preservation.
- Complete subscription catalogs with no curated filtering.
- Numeric/timestamp newest-first ordering, including `3.10 > 3.9`.

### Configuration and CLI tests

- YAML, CLI, web form, preset, and run round trips for `minimal`, `xhigh`, and `max`.
- Existing `none` normalization.
- Subscription submissions reject manual, stale, cross-provider, and absent model IDs.
- API-key and custom modes retain manual model entry.

### Transport tests

- OpenAI/Codex pass-through and mapped efforts.
- Claude budget and adaptive-thinking payloads through `max`.
- Gemini level/budget configuration and wire-model routing.
- OpenRouter extra-body reasoning values.
- One-time, field-specific 400/422 fallback and non-retry of unrelated failures.

### Browser verification

- Subscription dropdowns for OpenAI, Google, and Claude contain every authenticated provider model, contain no Other option or manual field, and place the newest model first.
- API-key/custom mode still exposes manual entry.
- Changing models rebuilds every paired reasoning selector with the correct supported subset.
- Unknown manual models show all six levels.
- Preset restoration cannot reintroduce an unavailable subscription model.

Authenticated final probes cover current Codex, Google, and Claude catalogs and at least one high-end effort accepted by each provider that advertises it.

## Acceptance Criteria

- The canonical MUDIDI reasoning ladder supports `minimal`, `low`, `medium`, `high`, `xhigh`, and `max` across config, CLI, web, persistence, and request transports.
- Known model selectors show only supported levels; unknown/manual API-key models show all six.
- No generic Provider default choice is presented.
- Model-specific wire mappings prevent unsupported effort names or synthetic model IDs.
- All authenticated subscription models from OpenAI, Google, and Claude appear in their respective dropdowns.
- Subscription dropdowns provide no manual model-entry path, including stale synthetic selections.
- Subscription submissions are server-validated against the authenticated or last-successful catalog.
- Subscription models are ordered newest first, with Gemini 3.8 above older revisions and numeric ordering correct for future multi-digit revisions.
- API-key/custom manual model behavior remains available.
- Explicit reasoning-effort rejections may trigger only one safe, evidence-based fallback; unrelated failures are unchanged.
- Credentials and raw provider payloads remain outside browser responses, logs, and persisted run data.
