# Task 6: Typed Auth Configuration and Runtime Backend Selection

## Scope

Implemented typed authentication selection and in-process subscription backend routing for YAML-configured extraction. API-key mode retains the existing LiteLLM path; subscription mode routes completion, structured completion, usage, Stage 1, Pass 1, Pass 2, and agentic verifier/rewriter calls through the selected provider backend.

## RED

Before the production changes, the new focused tests were run with:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py
```

Result:

```text
9 failed, 1 passed
```

The failures covered the expected missing behavior: no typed `auth` config, no CLI namespace auth fields, no benchmark subscription rejection, completion helpers not accepting a backend, and the two-stage strategy not accepting backend context.

## GREEN

After implementing the typed boundary, runtime resolver, client dispatch, extraction propagation, and CLI mapping, the required focused command was run:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/extraction tests/cli/test_config_execution.py
```

Result:

```text
68 passed, 5 warnings in 4.10s
```

The warnings are existing dependency deprecation warnings from SwigPy types.

Additional smoke check:

```text
uv run python -c 'from mudidi.cli.main import build_parser; a=build_parser().parse_args(["run", "--auth-mode", "subscription", "--auth-provider", "openai", "--config", "run.yaml", "--dry-run"]); assert a.auth_mode == "subscription" and a.auth_provider == "openai"; print("auth CLI flags parsed")'
```

Output:

```text
auth CLI flags parsed
```

## Implementation notes

- `AuthConfig` defaults to `api_key`, accepts only the supported provider enum values, and requires a provider for subscription mode.
- Benchmark extraction rejects subscription mode before execution.
- Redacted resolved configuration contains only auth mode/provider selection; credentials remain in the backend/store boundary and are not serialized into the namespace or resolved config.
- `resolve_subscription_runtime` eagerly checks backend status; missing credentials and provider policy warnings fail before `extract.main` starts page processing.
- `complete`, `complete_with_usage`, and `complete_structured` dispatch only when a backend/runtime is supplied. The existing API-key branch continues through `_build_params`, API-key lookup, and LiteLLM.
- Subscription usage is normalized to `prompt_tokens`, `completion_tokens`, `total_tokens`, optional token-detail fields, `cost_usd`, and `billing_mode="subscription"`.
- Existing fake-callable seams remain compatible because backend parameters are keyword-only and omitted when no backend is selected.
- Public sparse CLI parsing now accepts `--auth-mode` and `--auth-provider` for `mudidi run` and `mudidi benchmark run`.

## Changed files

- `src/mudidi/config/yaml_config.py`
- `src/mudidi/cli/main.py`
- `src/mudidi/cli/run.py`
- `src/mudidi/cli/extract.py`
- `src/mudidi/extraction/llm_two_stage.py`
- `src/mudidi/llm/pass_1.py`
- `src/mudidi/llm/pass_2.py`
- `src/mudidi/llm/client.py`
- `src/mudidi/llm/subscriptions/types.py`
- `src/mudidi/llm/subscriptions/__init__.py`
- `tests/config/test_subscription_config.py`
- `tests/llm/test_subscription_dispatch.py`

## Review-fix verification

Review identified provider-boundary gaps not covered by the initial fake-backend tests. The follow-up fixes:

- Dereference `$defs`/`$ref` schemas, remove unsupported constraints while preserving representable union/null types, and apply OpenAI strict-schema requirements recursively. Schema-valued `additionalProperties` are rejected explicitly at the selected-provider boundary. Tests pass the normalized `FlatTranscriptionResponsePlain`, `TranscriptionResponsePlain`, and `AgenticVerifierDecision` schemas through the OpenAI, Google, and Claude schema validators and real request builders.
- Translate OpenAI Responses multimodal content to `input_text`/`input_image`, accept validated image data URIs and HTTPS image URLs, and advertise `image_input=True`.
- Refresh one expired stored credential before returning an authentication error. Claude’s explicit opt-in policy warning remains distinct from missing/expired credentials.
- Lazily load dotenv and LiteLLM only when the API-key path uses them; subscription-safe imports/dispatches do not initialize either dependency or debug mode.
- Preserve `billing_mode` through bounded agentic verifier/rewriter aggregation, explicitly using `mixed` if modes differ.
- Restore `AuthMode` and `SubscriptionStatus` wildcard exports while retaining `SubscriptionRuntime`.
- Make the strategy backend parameter keyword-only.

The review-fix focused command was run:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_types.py tests/agentic/test_verifier_loop.py tests/llm/test_client_rate_limit.py tests/llm/test_client_reasoning.py tests/llm/test_client_reasoning_integration.py
```

Result:

```text
113 passed, 3 skipped, 5 warnings in 3.53s
```

The review-fix test additions include real provider request-builder coverage, expired-credential refresh coverage, import-time side-effect coverage, agentic subscription billing aggregation, OpenAI image translation, and wildcard export preservation.

The original Task 6 required command was also rerun after the review fixes:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/extraction tests/cli/test_config_execution.py
```

Result:

```text
76 passed, 5 warnings in 4.91s
```

## Round-two review-fix verification

The second review identified five remaining provider-boundary gaps. The follow-up fixes:

- Normalize OpenAI structured schemas with every declared property recursively included in `required`, while preserving nullable and scalar-union semantics as JSON-Schema type lists. Structural unions that cannot be represented without narrowing are rejected rather than silently reduced.
- Preserve schema-valued `additionalProperties` in provider-neutral normalization and reject it explicitly when a selected subscription provider cannot represent it.
- Require OpenAI strict schemas with declared properties to provide matching recursive `required` entries at the adapter boundary.
- Route every inline OpenAI image form through one data-URI validator that requires an exact `base64` parameter token and strict base64 decoding. Invalid source payloads, near-match parameters, and TIFF media types are rejected; supported PNG/JPEG/WEBP/GIF data URIs and HTTPS URLs remain accepted.
- Add focused regression coverage for real extraction schemas, scalar unions/nullability, schema-valued mappings, strict required fields, malformed image source data, near-match data-URI parameters, and TIFF rejection.

The round-two focused command was run:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_types.py tests/agentic/test_verifier_loop.py tests/llm/test_client_rate_limit.py tests/llm/test_client_reasoning.py tests/llm/test_client_reasoning_integration.py
```

Result:

```text
119 passed, 3 skipped, 5 warnings in 3.07s
```

The warnings remain dependency deprecation warnings from SwigPy types.

The original Task 6 focused command was rerun after the round-two fixes:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/extraction tests/cli/test_config_execution.py
```

Result:

```text
78 passed, 5 warnings in 3.79s
```

## Round-three review-fix verification

The third review found two remaining OpenAI adapter boundary gaps. The follow-up fixes:

- Require every strict object schema to declare `additionalProperties: false`, require unique `required` names, and require the unique names to exactly cover declared properties. Direct adapter tests cover partial, duplicate, missing-closure, and `true`-closure schemas.
- Validate every explicitly supplied image MIME type, including HTTPS image parts, against the supported PNG/JPEG/WEBP/GIF allowlist. HTTPS TIFF parts now fail locally instead of being forwarded.

The round-three focused command was run:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_types.py tests/agentic/test_verifier_loop.py tests/llm/test_client_rate_limit.py tests/llm/test_client_reasoning.py tests/llm/test_client_reasoning_integration.py
```

Result:

```text
123 passed, 3 skipped, 5 warnings in 3.31s
```

## Round-four review-fix verification

The final scoped review found three remaining OpenAI image/schema edge cases. The follow-up fixes:

- Apply strict-object required uniqueness and exact coverage even when `properties` is absent, treating the declaration as empty; an object containing ghost or duplicate required names now fails locally.
- Select MIME aliases by key presence rather than truthiness, so explicitly supplied empty, `None`, numeric, or boolean values cannot bypass the supported-media allowlist. Regression coverage verifies rejection of falsey values and acceptance of PNG/JPEG/GIF/WEBP HTTPS image MIME values.
- Restore trimming before data-URI or HTTPS scheme dispatch, with whitespace-wrapped supported image content covered by the valid translation test.

The round-four focused command was run:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_types.py tests/agentic/test_verifier_loop.py tests/llm/test_client_rate_limit.py tests/llm/test_client_reasoning.py tests/llm/test_client_reasoning_integration.py
```

Result:

```text
132 passed, 3 skipped, 5 warnings in 2.67s
```

## Round-five review-fix verification

The final scoped review found one malformed-schema edge case: an explicit `required: null` value was being treated like an absent keyword. The follow-up fix distinguishes absent `required` from explicit null for both object and non-object schemas, rejecting the malformed declaration before transport.

Direct regression coverage now includes explicit-null object and non-object schemas. The focused subscription command was rerun:

```text
uv run pytest -q tests/config/test_subscription_config.py tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_types.py tests/agentic/test_verifier_loop.py tests/llm/test_client_rate_limit.py tests/llm/test_client_reasoning.py tests/llm/test_client_reasoning_integration.py
```

Result:

```text
134 passed, 3 skipped, 5 warnings in 2.94s
```
