# Task 7 RED/GREEN Report

## Scope

Implemented secure subscription worker handoff and local subscription lifecycle routes, then addressed two security-review rounds.

- Worker stdin accepts the exact non-secret subscription descriptor (`auth_mode` and `provider`) and rejects token-bearing, unknown, mixed, malformed, duplicate-key, nested, and mode-mismatched messages.
- API-key credential-message behavior remains intact, including the legacy environment-source path.
- Subscription workers receive a managed store directory through a fixed argument and `MUDIDI_SUBSCRIPTION_STORE`; subscription child environments are built from an explicit minimal allowlist rather than inherited from the parent. API-key workers retain the filtered legacy environment and receive only the selected API key when its source is the environment.
- Subscription configs use the dedicated encrypted store below the web data directory and keep the API-key vault separate. Store directory, database, and key symlinks are rejected using no-follow opens and regular-file checks.
- Worker failures are bounded and redacted for API-key, OAuth, bearer, JWT, and typed subscription errors. Typed subscription failures use stable category messages.
- Added local-only status, provider availability, warning, POST-only login initiation, callback completion, and logout routes with non-secret response payloads and Claude research warnings.
- Added an explicit provider-neutral two-step PKCE lifecycle. Production OpenAI, Google, and Claude adapters expose `begin_login(redirect_uri)` and `complete_login(code, state, transaction)`; the web app stores short-lived server-side transactions, compares state in constant time, atomically consumes them, and rejects replay.
- Web start, Pass 2 approval, and resume paths avoid resolving or handing API-key credentials for subscription-mode configs.

## Initial RED

Command run before the original implementation:

```text
uv run pytest -q tests/web/test_subscription_worker.py tests/web/test_subscription_routes.py
```

Expected collection failure:

```text
ImportError: cannot import name 'SubscriptionDescriptor' from 'mudidi.web.inference_worker'
```

The new tests failed at collection before the implementation existed.

## Initial GREEN

Required focused command after the original implementation:

```text
uv run pytest -q tests/web/test_subscription_worker.py tests/web/test_subscription_routes.py tests/web/test_production_jobs.py tests/web/test_inference_worker.py
```

Result:

```text
35 passed, 6 warnings in 3.62s
```

## Security review fix round one

The first review identified a nonfunctional callback lifecycle, denylist environment inheritance, duplicate JSON-key acceptance, GET login side effects, store symlink traversal, and missing Claude warnings on callback errors. The first fix round added:

- `SubscriptionLoginTransaction`, `SubscriptionLifecycleBackend`, shared PKCE begin/complete helpers, and adapter implementations for all production subscription providers.
- Route-level transaction storage with a ten-minute expiry, constant-time state comparison, lock-protected consume-once semantics, explicit callback method invocation, POST-only login, and warning-bearing centralized lifecycle errors.
- Subscription-specific environment allowlists in both `JobController` and `production_worker`; API-key workers retain their legacy environment path.
- Duplicate-key rejection through `object_pairs_hook` at every JSON object boundary.
- No-follow directory/file checks and mode-specific secure key reads for the encrypted subscription store; configured runtime paths no longer resolve a final store symlink before validation.
- Real OpenAI adapter integration coverage using a fake OAuth transport and real `SubscriptionStore`, including a fresh backend and runtime resolver reading the persisted credential.
- Generic token/secret-name subprocess boundary coverage, callback warning coverage, all-route local-only coverage, POST/cross-origin login coverage, and directory/database/key symlink coverage.

First-round focused checks:

```text
uv run pytest -q tests/web/test_subscription_routes.py
```

```text
18 passed, 6 warnings in 0.76s
```

```text
uv run pytest -q tests/web/test_subscription_worker.py
```

```text
14 passed, 5 warnings in 0.30s
```

```text
uv run pytest -q tests/llm/test_subscription_types.py tests/web/test_subscription_routes.py tests/web/test_subscription_worker.py
```

```text
54 passed, 6 warnings in 0.74s
```

Required focused command after first-round edits:

```text
51 passed, 6 warnings in 3.78s
```

First-round subscription adapter regression checks:

```text
uv run pytest -q tests/llm/test_subscription_types.py tests/llm/test_subscription_oauth.py tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py tests/llm/test_subscription_dispatch.py
```

```text
190 passed, 5 warnings in 2.52s
```

## Security review fix round two

The scoped re-review identified provider callback path mismatches, Claude's JSON-vs-form token exchange contract, Google account/project bootstrap ordering, API-key environment regression, existing-key permission repair, pathname race windows in the encrypted store, and incomplete public exports. The second fix round added:

- Provider-compatible callback aliases and redirect URIs: OpenAI uses `/subscriptions/openai/auth/callback`, Google uses `/subscriptions/google/oauth2callback`, and Claude uses `/subscriptions/claude/callback`; the legacy generic callback aliases remain local and state-protected.
- Claude two-step completion now uses `request_json_token` with the full authorization-code body (`grant_type`, client ID, code, state, redirect URI, and PKCE verifier), then parses and saves the credential.
- Google two-step completion parses token responses permissively, runs `_ensure_account_and_project`, and saves the enriched credential. Tests cover user-info/project bootstrap and persisted output.
- API-key workers again filter secret-looking inherited variables and add only the explicitly selected environment credential; subscription workers continue using the explicit non-secret allowlist. An actual child process checks generic token/secret names.
- Existing subscription key files are opened no-follow, verified regular, chmoded to `0600`, and read from the same descriptor.
- Subscription SQLite operations open the parent directory and database relative to validated descriptors with `O_NOFOLLOW`, verify the opened inode, and hand the opened database descriptor to SQLite through `/dev/fd`; in-memory journaling avoids sidecar pathname races. Deterministic tests cover directory replacement after validation and database inode replacement before SQLite use without touching the outside target.
- Restored `CompletionResult` and `SubscriptionPolicyError` package exports and added the lifecycle types to `types.__all__`.
- Added expiry, replay, concurrent consume, provider-mismatch, unavailable-Claude-warning, all-provider production route, and real `JobController`/production-worker lifecycle coverage.


## Security review fix round three

The second re-review identified an intermediate-ancestor path traversal window in directory setup and insufficient production-worker lifecycle coverage. The third fix round added:

- Openat-style directory traversal from a trusted root/current-directory descriptor. Every configured component is opened with `O_DIRECTORY|O_NOFOLLOW`; missing components are created with relative `mkdirat` semantics, `..` is rejected, and the validated directory inode remains held for subsequent database/key operations. Tests cover an intermediate symlink and replacement of the validated store directory.
- A production-worker seam test that seeds a real encrypted `SubscriptionStore`, invokes the real `resolve_subscription_runtime` and provider backend status path before deterministic execution, and checks that the seeded credential remains absent from output/logs. The real `JobController`/`Popen` test additionally checks descriptor-only command, event, log, and stderr secrecy; the subprocess environment test remains in place.

Final required web focused command:

```text
uv run pytest -q tests/web/test_subscription_worker.py tests/web/test_subscription_routes.py tests/web/test_production_jobs.py tests/web/test_inference_worker.py
```

```text
66 passed, 6 warnings in 4.52s
```

Final subscription adapter regression command:

```text
uv run pytest -q tests/llm/test_subscription_types.py tests/llm/test_subscription_oauth.py tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py tests/llm/test_subscription_dispatch.py
```

```text
196 passed, 5 warnings in 2.43s
```

Scoped `py_compile` and `git diff --check` also passed. The warnings are dependency deprecations from the existing test environment (Starlette/httpx and SWIG bindings); no formatter, linter, build, or project-wide suite was run.
