# Claude Policy-Warning Contract Removal Design

## Goal

Remove the blanket Claude subscription legal and compatibility disclaimer from every MUDIDI surface.

The removal includes backend status and error metadata, public status serialization, runtime routing, CLI output, web JSON and rendered pages, browser JavaScript and styling, tests, and documentation.

## Preserved behavior

This change does not remove or weaken operational controls:

- `MUDIDI_ENABLE_CLAUDE_SUBSCRIPTION_RESEARCH=1` remains required.
- `ClaudeResearchBackend` remains isolated from API-key/LiteLLM routing.
- The internal `research_only` and `opt_in_enabled` metadata flags remain available for explicit control flow.
- Provider HTTP 403 responses and provider-reported policy rejections remain typed `SubscriptionPolicyError` failures.
- Local-only subscription routes, credential encryption, secret redaction, and authorization boundaries remain unchanged.

A missing research opt-in becomes an actionable configuration error naming the required environment variable. It does not carry the removed legal or compatibility disclaimer.

## Contract cutover

`SubscriptionStatus.policy_warning` is removed rather than left as an always-empty compatibility field. Claude is its only producer, so retaining the field would leave dead branches in the runtime, CLI, and dashboard and would permit old metadata to reappear.

Status and routing decisions use explicit state:

- `authenticated`
- `expires_at`
- `metadata["research_only"]`
- `metadata["opt_in_enabled"]`
- typed error categories

They never infer policy state from warning-string presence.

Legacy credential or upstream error metadata may still contain a `policy_warning` key. Safe serialization and Claude normalization must discard that key so an old encrypted credential cannot reintroduce the removed text. New credentials and errors do not write it.

## Backend and runtime

### Shared subscription types

Remove `policy_warning` from `SubscriptionStatus` and from `redacted_metadata`. Extend safe metadata filtering to omit a legacy `policy_warning` key.

### Claude backend

Delete the blanket-warning constant and public `policy_warning` property. Remove the warning from:

- `_error` metadata
- normalized shared errors
- status metadata
- credential metadata
- all `SubscriptionStatus` instances

The explicit opt-in guard continues raising a typed error, but its message becomes a concise configuration instruction. Existing provider-policy rejection messages remain because they report an actual request failure rather than a blanket warning.

The Claude transport module and focused tests currently contain substantial concurrent, uncommitted OAuth/transport work. Warning removal must be surgical and preserve those changes without reformatting or reverting them.

### Runtime resolution and CLI

`resolve_subscription_runtime` replaces warning-string checks with explicit `research_only` plus disabled `opt_in_enabled` metadata. The CLI no longer classifies a status from `policy_warning` or prints a policy-warning line. Missing opt-in remains observable through the typed error/category and actionable message.

## Web application

Remove the dashboard-level Claude warning constant and warning extraction helper. Subscription status payloads no longer include a `warning` key solely for the Claude blanket disclaimer. Policy categorization for disabled opt-in uses explicit status metadata.

Remove `policy_warning` from prepared-run and review summaries. Remove its rendering from:

- the Model-step authentication choice
- Claude subscription cards
- the Review authentication group
- run-detail authentication metadata

Delete the corresponding JavaScript constant, selectors, show/hide logic, template elements, and warning-only CSS when no remaining page uses that class. Functional login error messages remain in their existing generic error presentation.

## Documentation

Remove instructions that mandate displaying the blanket warning from active subscription-routing documentation and repository design/implementation documents. Retain factual setup instructions for explicit opt-in and the technical `research_only` classification where needed to explain the implementation boundary. Do not retain claims about Anthropic approval, provider-policy violation, or compatibility warranty as a standing product warning.

## Testing

Test-first changes cover observable contracts:

1. Claude status and errors contain no `policy_warning` field or metadata key.
2. Missing opt-in still fails closed with an actionable environment-variable message.
3. Runtime routing blocks disabled opt-in from explicit metadata rather than warning text.
4. CLI status/login output does not print the removed warning.
5. Subscription status/login JSON does not expose the blanket warning.
6. Home, Review, and run-detail HTML contain neither the text nor warning elements.
7. Browser interaction selecting Claude shows no warning region while normal provider/status controls remain usable.
8. Repository search confirms no production or active-documentation copy of the disclaimer remains.

Run the focused Claude, subscription contract, CLI, web form, subscription route/integration, and run-route suites; then run the complete repository suite after concurrent Claude work stabilizes. Independently review the final diff before committing.

## Acceptance criteria

- No user-visible, API-visible, CLI-visible, or metadata-visible copy of the blanket warning remains.
- `SubscriptionStatus` has no `policy_warning` contract.
- Old metadata cannot leak `policy_warning` through safe serialization.
- Claude research opt-in still fails closed when disabled.
- Actual provider-policy failures remain typed and actionable.
- Every dashboard page renders without Claude warning copy or empty warning scaffolding.
- Concurrent Claude transport work remains intact.
