# Task 9 — Contract, Security, and Opt-In Smoke Verification

## Scope

This task adds local-only contract coverage for all three direct subscription providers and a web-to-worker smoke path. Every fake transport is in-process; no live login, user credential store, gateway, or relay is used.

## RED evidence

The first focused run was:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_contract.py tests/web/test_subscription_integration.py
```

It initially reported two failures in the newly added smoke tests:

- The worker config assertion expected a top-level `auth_mode`, while the redacted config intentionally stores the nested `auth` object.
- The malformed OpenAI fixture used `status=completed` with an `output` list containing only an unknown item. The current adapter returned an empty normalized result instead of raising.

The first issue was a test assertion error and was corrected. The second is a confirmed adapter limitation, not a hidden test pass: it is documented in the design specification, and the malformed smoke test now uses a response with no visible output so the fail-closed behavior is asserted.

The review-round red run also caught test-harness setup mistakes (a missing import, an undefined log path, a literal backslash-n descriptor, and a worker status check before `wait`). Those were corrected before the final green run.

A second review found that the shared image sentinel was not a real PNG and that the state sentinel was missing from the persisted worker-boundary tuple. The contract now decodes a deterministic 1x1 PNG before asserting each provider translation, and every worker-boundary surface includes state.

## GREEN evidence

Focused new coverage:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_contract.py tests/web/test_subscription_integration.py
38 passed, 6 warnings
```

Local web fake-provider smoke alone:

```text
uv run --no-sync pytest -q tests/web/test_subscription_integration.py -k local_fake_provider
1 passed, 3 deselected, 6 warnings
```

Narrowly relevant dispatch/config/CLI execution/worker contracts:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_dispatch.py tests/config/test_subscription_config.py tests/cli/test_config_execution.py tests/web/test_subscription_worker.py
71 passed, 5 warnings
```

Subscription route, CLI auth, and run-form contracts:

```text
uv run --no-sync pytest -q tests/web/test_subscription_routes.py tests/cli/test_subscription_auth_cli.py tests/web/test_run_form.py
78 passed, 6 warnings
```

Corrected production-worker contracts:

```text
uv run --no-sync pytest -q tests/web/test_production_jobs.py
12 passed, 5 warnings
```

Combined final targeted selection:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_contract.py tests/web/test_subscription_integration.py tests/llm/test_subscription_dispatch.py tests/config/test_subscription_config.py tests/cli/test_config_execution.py tests/web/test_subscription_worker.py tests/web/test_production_jobs.py tests/web/test_subscription_routes.py tests/cli/test_subscription_auth_cli.py tests/web/test_run_form.py
199 passed, 6 warnings
```

The first web smoke retains the real `JobController` subprocess and checks its descriptor/argv/config/event/log/stderr boundaries. A `Popen` spy captures the actual subscription child environment and verifies API-key/token names and values are absent. The second smoke calls `production_worker.main` in-process with the actual descriptor and without `--offline-executor`; it patches only `execute_extraction_config`, observes the active managed-store environment, reloads the real encrypted store, resolves the real OpenAI backend with a fake transport, performs completion, and asserts the normalized result after `main` returns.

The shared provider contract now covers OpenAI, Google, and Claude structured type mismatches and forbidden additional properties. Each fake response is syntactically valid, reaches the transport, produces the typed `structured_schema_mismatch` error, and keeps credential/malformed sentinels out of the error.

The production fixture mismatch found in the first broader targeted run was corrected by parameterizing its config helper for auth/model and constructing OpenAI cases with `openai/gpt-5.6-terra`; production validation code was not changed.

## Security and policy review

- **Endpoint allowlists:** provider OAuth and request endpoints use fixed HTTPS constants and the shared URL validator with provider-specific host allowlists. Existing provider/OAuth tests cover rejected endpoint hosts and loopback redirect requirements.
- **Callback and PKCE:** the shared OAuth layer validates loopback redirects, callback state, bounded ASCII state, and PKCE verifier/challenge pairing. The new web smoke captures the generated state, computes RFC 7636 S256 from the exchanged verifier, compares it with the authorization challenge, and checks the verifier is absent from callback, descriptor, argv, config, event, log, and stderr surfaces.
- **Encrypted store and permissions:** the web smoke reloads the real `SubscriptionStore`, verifies both tokens round-trip, and verifies token bytes are absent from the SQLite database. Existing storage contracts cover encryption-key/database mode `0600`, directory mode `0700`, permission repair, and symlink/race rejection.
- **Redaction and worker isolation:** provider errors, refresh status, callback responses, and all worker boundary text are checked for token/code/state/verifier absence. The captured subscription child environment excludes `OPENAI_API_KEY` and `HF_TOKEN` names and values. Existing shared type/storage tests also cover redacted representations and nested metadata.
- **API-key isolation and fallback:** the shared dispatch contracts keep API-key calls on the existing LiteLLM path, while subscription calls use the typed backend and do not invoke LiteLLM or API-key lookup. The subscription contract checks bearer authorization is the only secret-bearing request field and no API-key header is emitted. A provider policy error remains typed and fail-closed.
- **Claude policy:** Claude is explicitly configured with research opt-in in the shared provider contract. Runtime resolution and the web route both reject missing opt-in with the research-only/Anthropic-approved warning, without touching the transport.
- **Structured/image boundaries:** all providers receive the same structured schema contract and validate the normalized JSON result. The shared image fixture is a decoded deterministic 1x1 PNG carried through a MUDIDI data URI and translated to each native provider representation; malformed base64 is rejected before transport.
- **Malformed responses:** all providers have fake malformed-response coverage with typed transport errors and redacted error surfaces. The web smoke additionally verifies an OpenAI response with no visible output cannot become a successful extraction.


## Observed limitation

The design specification now records the confirmed OpenAI envelope limitation: a `2xx` `status=completed` payload with an `output` field containing no visible text can produce an empty `CompletionResult` rather than `missing_visible_text`. Until tightened, callers must not treat an empty normalized text result as successful extraction. Responses without `output`/`output_text` still fail closed, as covered by the web smoke.

The six warnings in the focused runs are existing dependency deprecations (PyMuPDF SWIG types and the Starlette/httpx TestClient integration); no live-provider warning or credential-store access occurred.

Project-wide tests, formatters, linters, and builds were intentionally not run; the parent task owns the single project-wide suite.
