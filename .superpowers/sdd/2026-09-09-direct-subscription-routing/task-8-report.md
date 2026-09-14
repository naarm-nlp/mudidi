# Task 8 — subscription auth CLI and web UX

## Scope

Implemented explicit subscription billing/authentication controls across the CLI, typed config resolution, production run form, dashboard status cards, and run/review metadata. API-key billing remains the default and existing API-key credential discovery/controls remain separate and unchanged. Subscription forms and previews never carry credential fields or token material.

## RED

The focused contract tests were added before the production implementation and initially failed with the expected missing behavior: `16 failed, 56 passed, 5 warnings` (`artifact://2747`). Failures covered absent `auth` parser commands, missing `--provider` alias, absent billing metadata, and missing form auth fields/validation.

## GREEN

- `uv run pytest -q tests/cli/test_cli.py tests/cli/test_subscription_auth_cli.py tests/cli/test_config_execution.py tests/web/test_run_form.py`: **72 passed**.
- `uv run pytest -q tests/web/test_app.py tests/web/test_production_routes.py tests/web/test_subscription_routes.py`: **122 passed**.
- Combined focused task coverage: **194 passed**.
- `python -m py_compile src/mudidi/cli/main.py src/mudidi/cli/run.py src/mudidi/config/yaml_config.py src/mudidi/web/forms.py src/mudidi/web/app.py`: passed.
- `node --check src/mudidi/web/static/app.js`: passed.

## Implementation

- Added `mudidi auth status|login|logout` with positional and `--provider` selection, local encrypted-store adapter construction, safe account/expiry/category output, provider-scoped logout, and secret-safe error/output handling.
- Added `--provider` alias for run auth selection and explicit billing/auth metadata to dry-run previews.
- Rejected `auth.provider` with API-key mode in typed YAML auth validation.
- Added web form auth mode/provider fields, provider-to-model normalization, subscription model defaults, missing-provider validation, and Claude research/policy warning metadata.
- Added local subscription status cards and login/logout controls, with safe account/category/expiry display and browser login polling; API-key credential cards remain separate.
- Added subscription status context, missing-login run validation, safe billing/auth metadata to review/run detail views, and preset auth-state restoration.
- Added styling for subscription cards and auth controls.

## Concerns

- No project-wide suite, formatter, linter, or build was run per delegated-task constraints; parent agent owns final project-wide validation.
- Browser login interaction is wired to the existing local lifecycle routes and `window.open` seam; no external provider calls were made during focused tests.

## Review fixes

- Centralized provider-qualified/native model resolution and applied it to typed config, web forms, CLI overrides, direct completion requests, and all three provider request bodies. Active stage and agentic models now reject incompatible prefixes instead of being relabeled; provider defaults are selected by subscription provider.
- Locked agentic provider controls to the selected subscription provider, preserved compatible preset model state on initial restore, and kept API-key mode able to clear a YAML subscription provider.
- Fixed browser login popup timing by opening a named blank window synchronously, severing `opener`, validating the returned URL, navigating the pre-opened window, closing it on initiation failure, and always entering status polling after successful initiation.
- Returned complete safe subscription status fields from logout routes, rejected conflicting positional/flag provider selections, and allowed Claude logout while research opt-in is disabled.
- Added focused model routing, config, CLI precedence/conflict, adapter body, form, logout, and opt-in tests.

## Review-fix verification

- `uv run --no-sync pytest -q tests/llm/test_subscription_dispatch.py tests/llm/test_subscription_openai_codex.py tests/llm/test_subscription_google_gemini_cli.py tests/llm/test_subscription_claude_research.py tests/config/test_subscription_config.py tests/web/test_run_form.py tests/cli/test_config_execution.py tests/cli/test_subscription_auth_cli.py tests/web/test_subscription_routes.py`: **262 passed**.
- `node --check src/mudidi/web/static/app.js`: passed.
- `python -m py_compile` passed for all changed Python modules.

## Round-two review fixes

- CLI model overrides now use public-namespace membership to distinguish an explicitly supplied `--model` from an omitted parser default. An explicit `gemini/gemini-3-flash-preview` therefore overrides YAML in API-key mode and is rejected when incompatible with OpenAI subscription routing.
- Leaving subscription mode restores every evaluator and rewriter provider option to its normal visible/enabled API-key state before re-enabling the control.

## Round-two verification

- The two new CLI precedence tests failed before the production change (`1` YAML precedence assertion and `1` missing subscription mismatch error), then passed after it.
- Browser smoke on the local UI reproduced the stale provider-option state before the fix and, after the fix, switched subscription → API key and selected evaluator `openai` plus rewriter `anthropic`; all five provider options were visible/enabled for both controls.
- `node --check src/mudidi/web/static/app.js`: passed.
- `uv run --no-sync pytest -q tests/cli/test_config_execution.py -k 'explicit_default_model'`: **2 passed**.

## Round-three review fixes

- Explicitness now uses public sparse-namespace membership, with a legacy parser sentinel so parser-generated `DEFAULT_MODEL` values remain implicit while an explicitly typed identical value remains an override.
- The round-two browser control fix is retained; leaving subscription mode resets every evaluator and rewriter provider option to its normal visible/enabled state.

## Round-three verification

- `uv run --no-sync pytest -q tests/cli/test_model_args.py tests/cli/test_config_execution.py`: **30 passed** (6 warnings).
- `node --check src/mudidi/web/static/app.js`: passed.
- Local browser smoke after the app fix selected evaluator `openai` and rewriter `anthropic` after a subscription → API-key round trip.

## Final integration fix

- Kept the responsive `.credential-grid` and `.subscription-grid` media selectors as separate CSS blocks so the existing compact-stage hierarchy contract and subscription card layout both remain explicit.

## Final integration verification

- `uv run --no-sync pytest -q tests/web/test_theme.py::test_model_panel_preserves_compact_stage_hierarchy tests/web/test_subscription_routes.py`: **32 passed** (6 warnings).
- `git diff --check`: passed.
