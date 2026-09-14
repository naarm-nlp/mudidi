# Task 1 Report: Provider-Neutral Subscription Contracts

## Scope

Implemented the provider-neutral subscription contract in the three requested source/test files:

- `src/mudidi/llm/subscriptions/__init__.py`
- `src/mudidi/llm/subscriptions/types.py`
- `tests/llm/test_subscription_types.py`

The package exports only provider-neutral enums, immutable Pydantic request/result/capability/status/credential models, the backend protocol, and normalized subscription errors. Credential access and refresh tokens use `SecretStr`, are excluded from Pydantic dumps and repr output, and are omitted from redacted metadata. Error messages and metadata redact token-shaped values. Completion results enforce `billing_mode="subscription"`.

## TDD evidence

### RED

Command:

```text
uv run pytest -q tests/llm/test_subscription_types.py
```

Output:

```text
==================================== ERRORS ====================================
____________ ERROR collecting tests/llm/test_subscription_types.py _____________
ImportError while importing test module 'tests/llm/test_subscription_types.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/llm/test_subscription_types.py:11: in <module>
    from mudidi.llm.subscriptions import (
E   ModuleNotFoundError: No module named 'mudidi.llm.subscriptions'

=========================== short test summary info ============================
ERROR tests/llm/test_subscription_types.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.10s
```

The focused test failed at collection because the requested subscription package did not yet exist.

### GREEN

Command:

```text
uv run pytest -q tests/llm/test_subscription_types.py
```

Output:

```text
..............                                                           [100%]
14 passed in 0.09s
```

No formatter, linter, build, or project-wide test suite was run.

## Review-fix report

### Changes

- Redaction now handles complete `Authorization: Bearer ...` values, quoted JSON token keys, JWT-shaped values, and secret-bearing strings nested in mappings and sequences.
- `SubscriptionStatus` sanitizes metadata during validation, so repr, model dumps, and redacted metadata cannot expose nested credential material.
- Request, result, credential, and status JSON containers are recursively wrapped in immutable dict/list equivalents.
- Structured result payloads use Pydantic JSON-value validation; normalized usage accepts only strict integer/float/null values.
- Added assertions that credential token field names and values are absent from repr, model dumps, and redacted metadata, plus nested status/error redaction coverage.

### Covering tests

The focused test module now covers:

- Authorization bearer and quoted structured-error redaction.
- Nested mapping/sequence redaction across credentials, status, and errors.
- Deep mutation attempts on request messages/schema and result structured JSON/usage.
- Rejection of non-JSON structured payloads and non-numeric usage values.
- Absence of `access_token`/`refresh_token` field names and values from credential-facing surfaces.

### Verification

Command:

```text
uv run pytest -q tests/llm/test_subscription_types.py
```

Output:

```text
..................                                                       [100%]
18 passed in 0.13s
```

## Follow-up review-fix report

### Changes

- Enabled default validation for `SubscriptionStatus.metadata` and `CompletionResult.usage`, so default containers are frozen and cannot later be populated with secrets or mutated.
- Materialized exception `secret_values` once and supplied them to both message and metadata redaction paths, including nested mappings and sequences.

### Covering tests

- `test_subscription_status_default_metadata_is_frozen_and_safe`
- `test_completion_result_default_usage_is_frozen`
- `test_subscription_error_secret_values_are_redacted_from_metadata`

### Verification

Command:

```text
uv run pytest -q tests/llm/test_subscription_types.py
```

Output:

```text
.....................                                                    [100%]
21 passed in 0.09s
```
