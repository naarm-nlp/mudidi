# Task 10 — Google OAuth Registration Boundary Fix

## Scope

The final audit blocker was the default Google subscription backend construction in the CLI auth factory, web app factory, and subscription runtime resolver. Each path previously constructed `GoogleGeminiCliBackend` without the deployment-local OAuth client registration, so Google login could fail before token exchange and refresh could not use the registered client. The audit also reported one trailing-whitespace line in `oauth.py` and an extra blank line at the end of the Claude adapter test.

## Fix

- Added one explicit deployment configuration seam: `MUDIDI_GOOGLE_OAUTH_CLIENT_ID`.
- Added `google_oauth_client_id_from_environment()` in the Google adapter. It reads only that named variable, trims and bounds it with the existing safe-string validation, and returns no client secret or token material.
- Updated the CLI auth factory, default web backend factory, and `resolve_subscription_runtime()` to pass the same resolved client ID into `GoogleGeminiCliBackend`.
- Added the same public registration variable to the subscription worker's explicit environment allowlist in both `JobController` and `production_worker`. OAuth access/refresh tokens, authorization codes, client secrets, API-key variables, and arbitrary inherited environment values remain excluded.
- Documented deployment setup and the precise fail-closed missing-registration limitation in `docs/superpowers/specs/2026-09-09-direct-subscription-routing-design.md`.
- Removed the reported trailing whitespace from `src/mudidi/llm/subscriptions/oauth.py` and the extra EOF blank line from `tests/llm/test_subscription_claude_research.py`.

When registration is absent, the backend remains constructible for safe status handling, but Google login and refresh raise typed errors with `reason=oauth_client_configuration_missing` before any token exchange. No API-key or external credential-store fallback is attempted.

## RED evidence

The new environment-backed login/refresh test initially failed during collection because the requested helper did not exist:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_google_gemini_cli.py tests/cli/test_subscription_auth_cli.py tests/config/test_subscription_config.py tests/web/test_subscription_routes.py tests/web/test_subscription_worker.py
```

```text
ImportError: cannot import name 'google_oauth_client_id_from_environment' from 'mudidi.llm.subscriptions.google_gemini_cli'
```

## GREEN evidence

The focused Google/CLI/runtime/web/worker/production selection passed after the fix:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_google_gemini_cli.py tests/cli/test_subscription_auth_cli.py tests/config/test_subscription_config.py tests/web/test_subscription_routes.py tests/web/test_subscription_worker.py tests/web/test_production_jobs.py
```

```text
112 passed, 6 warnings
```

This selection includes:

- CLI factory registration injection.
- Default web app factory registration injection.
- Runtime resolver registration injection with a seeded Google credential.
- Fake-transport Google login and refresh using the environment-resolved client ID.
- Safe login and refresh failure when registration is absent.
- Worker and production-worker allowlisting of only the Google public client ID, with client secret exclusion.

Existing subscription adapter contracts also passed:

```text
uv run --no-sync pytest -q tests/llm/test_subscription_types.py tests/llm/test_subscription_oauth.py tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py tests/llm/test_subscription_dispatch.py
```

```text
203 passed, 5 warnings
```

Targeted syntax checks passed with no output:

```text
python -m py_compile src/mudidi/cli/main.py src/mudidi/llm/client.py src/mudidi/llm/subscriptions/google_gemini_cli.py src/mudidi/llm/subscriptions/oauth.py src/mudidi/web/app.py src/mudidi/web/jobs.py src/mudidi/web/production_worker.py src/mudidi/web/inference_worker.py tests/cli/test_subscription_auth_cli.py tests/config/test_subscription_config.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py tests/web/test_subscription_routes.py tests/web/test_subscription_worker.py tests/web/test_production_jobs.py
```

The required whitespace check passed:

```text
git diff --check
```

## Commit

- `674e1fc fix: wire deployment Google OAuth registration`

## Concerns and limits

No live Google authentication, provider request, user credential store, OMP gateway/relay, or authenticated smoke was used or claimed. The Google Cloud Code Assist and OAuth surfaces remain provider-owned research behavior. The public client ID is deployment registration metadata; access/refresh tokens, authorization codes, and any client secret remain backend-local and are not included in CLI/web responses, worker descriptors, logs, subprocess arguments, or the subscription environment allowlist. Formatters, linters, builds, and the project-wide test suite were not run; they remain the parent task's responsibility.
