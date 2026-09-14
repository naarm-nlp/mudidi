# Provider-Default Temperature Design

> **Status:** Implemented and verified.

## Goal

Make every MUDIDI inference request use the selected model provider's default sampling temperature by omitting the `temperature` parameter entirely.

## Problem

MUDIDI currently defaults run temperature to `0.1` and propagates that value through the CLI, web form, typed configuration, extraction stages, generic LLM client, and subscription adapters. Provider APIs do not share one compatible temperature contract. In particular, Claude extended thinking requires temperature `1` when a temperature is supplied, so a Claude subscription run with reasoning enabled fails before delivery because MUDIDI supplies `0.1`.

Provider-specific clamping does not solve the general problem. It replaces one MUDIDI override with another, requires a growing model-version ruleset, and still prevents the provider from selecting its own default.

## Decision

Remove temperature as a MUDIDI configuration and request concept. MUDIDI will not calculate, normalize, clamp, persist, display, or transmit a sampling temperature. Provider defaults are selected by parameter omission.

This is a clean cutover:

- `models.temperature` is removed from the strict YAML schema.
- `--temperature` is removed from extraction CLI arguments.
- The dashboard temperature control is removed.
- Existing YAML containing `models.temperature` fails strict validation until the field is deleted.
- Existing CLI commands containing `--temperature` fail argument parsing until the option is deleted.
- No compatibility alias or accepted-but-ignored field remains.

## Scope

- Remove temperature from typed run and web-form models.
- Remove temperature from CLI argument registration, normalized run inputs, summaries, and worker/config forwarding.
- Remove temperature from extraction strategy constructors and stage helper calls.
- Remove temperature from generic LLM completion APIs and parameter builders.
- Remove temperature from the provider-neutral subscription completion request.
- Remove temperature handling from Claude and Google subscription request builders. OpenAI Codex already omits provider-neutral temperature and must continue to do so.
- Remove obsolete GPT-5 clamping and Gemini temperature exceptions.
- Remove temperature from preset restoration and generated/reference documentation.
- Migrate all repository callsites and tests in the same change.

## Non-goals

- Do not change reasoning-effort controls, extended-thinking budgets, token limits, retry behavior, model selection, or provider authentication.
- Do not introduce provider-specific temperature defaults in MUDIDI.
- Do not infer a temperature from model names or capabilities.
- Do not silently accept legacy temperature configuration.
- Do not alter unrelated extraction prompts or pipeline stages.

## Data Flow

Before this change, one numeric temperature value flows through every stage:

`CLI/web/config -> run configuration -> extraction strategy -> stage helper -> LLM client/subscription request -> provider request`

After this change, that value and every corresponding parameter are absent. Reasoning configuration continues through the same path independently. Provider request builders construct otherwise identical payloads without a `temperature` key.

For LiteLLM-backed requests, `_build_params` no longer receives temperature and never adds it to completion parameters. For direct subscriptions, `CompletionRequest` no longer carries temperature; Claude and Google builders therefore cannot transmit it. Claude extended thinking can be enabled without a local temperature conflict and Anthropic chooses the effective default.

## User Experience and Migration

The browser no longer displays a Temperature field or related help text. CLI run banners no longer print a temperature value. New generated configuration and documentation omit the field.

Legacy configuration produces an explicit validation or argument-parsing error rather than changing behavior silently. The remediation is deterministic: delete `models.temperature` from YAML or delete `--temperature VALUE` from the command.

Persisted presets or form state must not restore or serialize temperature after the cutover. Existing stored configuration containing the removed strict field follows the same explicit migration rule.

## Error Handling

Removing the field eliminates local model-specific temperature validation and clamping. Provider errors unrelated to temperature retain their current normalization. If a provider later changes its own default or rejects a request without temperature, that provider response is surfaced through the existing error path; MUDIDI does not invent a fallback temperature.

## Testing

Tests must cover observable contracts:

- Strict YAML parsing rejects `models.temperature` as an extra field.
- CLI parsing rejects `--temperature`.
- The web run form contains no temperature control and does not serialize temperature into run configuration or preset state.
- LiteLLM completion parameters contain no `temperature` key across ordinary, GPT-5, and Gemini model families.
- Provider-neutral `CompletionRequest` has no temperature field.
- Claude structured completion with extended thinking builds a request without `temperature` and without raising the former conflict.
- Google subscription requests omit `generationConfig.temperature`.
- OpenAI Codex requests continue to omit temperature.
- Extraction stage, agentic verifier, and rewriter callsites compile and run without temperature arguments.
- The full test suite passes.
- The original one-page Claude subscription extraction is exercised after implementation to confirm the reported failure no longer occurs.

## Documentation

Update the CLI/config reference and any examples or help text that advertise temperature. Remove comments describing model-specific clamping. No compatibility or deprecation documentation is added because this is an explicit clean cutover.

## Security and Performance

The change introduces no new credential or network surface. Omitting one scalar field slightly reduces request construction and payload size. No additional runtime lookup, model capability table, allocation, or retry is introduced.
