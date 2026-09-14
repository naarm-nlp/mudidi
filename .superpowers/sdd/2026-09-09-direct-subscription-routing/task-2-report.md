# Task 2 Report: PKCE, Loopback OAuth, and Separate Encrypted Store

## Scope

Implemented the three requested provider-neutral subscription primitives and their focused tests:

- `src/mudidi/llm/subscriptions/pkce.py`
- `src/mudidi/llm/subscriptions/storage.py`
- `src/mudidi/llm/subscriptions/oauth.py`
- `tests/llm/test_subscription_pkce.py`
- `tests/llm/test_subscription_storage.py`
- `tests/llm/test_subscription_oauth.py`

The implementation consumes the public models and errors from `mudidi.llm.subscriptions.types`; it does not duplicate those definitions or use `CredentialVault`/`PersistentCredentialStore`.

## Implementation

### PKCE

- `PkceChallenge.generate()` creates independent cryptographically random verifier and state values.
- Verifiers are constrained to the RFC 7636 43–128 character URL-safe alphabet.
- Challenges use the RFC 7636 `S256` derivation (`base64url(SHA-256(verifier))`, with padding removed).
- Authorization parameters intentionally omit the verifier, and diagnostic representation redacts verifier, challenge, and state values.

### Loopback OAuth receiver

- `LoopbackOAuthReceiver` binds an ephemeral socket on exactly `127.0.0.1` and publishes an HTTP loopback redirect URI.
- Callback paths, host/port, query shape, state, duplicate values, authorization errors, and authorization-code presence are validated before returning `OAuthCallback`.
- Callback responses are fixed, generic, non-caching success/failure text and never include callback values.
- Callback timeout is bounded; listener and browser-opener seams are injectable for deterministic tests without launching a browser.
- Request logging is disabled because the request target contains authorization input.

### HTTPS OAuth HTTP client

- `OAuthHttpClient` accepts only HTTPS provider endpoints, with an optional generic host allowlist supplied by provider modules.
- Authorization-code exchange validates the loopback redirect URI and PKCE verifier; refresh exchanges use the same bounded form transport.
- Token responses are bounded before JSON parsing and checked for required/non-empty access-token data plus optional token-type, refresh-token, and expiry field validity.
- HTTP/auth/transport/malformed-response failures use Task 1 typed errors with safe reason/status metadata only; response bodies, request parameters, and exception details are not included in raised messages.

### Separate encrypted storage

- `SubscriptionStore` creates `subscriptions.sqlite3` and `subscriptions.key` by default beneath its supplied directory, or accepts explicit database/key paths.
- Its own `subscription_credentials` table stores only provider lookup keys and Fernet ciphertext. The encrypted JSON record contains provider, account label, access/refresh tokens, expiry, project/account identifiers, metadata, schema version, and updated timestamp.
- The key and database are forced to mode `0600`; the store directory is restricted to `0700` where possible.
- The cryptography import is lazy, so importing core subscription modules does not require the web extra.
- `load()` is the backend-only token-bearing boundary; `status()`, `repr()`, and Pydantic model dumps remain token-free.
- Invalid key/ciphertext/JSON/model records raise `SubscriptionAuthError` with `category="storage"` rather than returning partial data.

## TDD evidence

### RED

The required focused command was run immediately after creating the tests and before creating the three production modules:

```text
uv run pytest -q tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_oauth.py
```

Observed result:

```text
_____________ ERROR collecting tests/llm/test_subscription_pkce.py _____________
...
E   ModuleNotFoundError: No module named 'mudidi.llm.subscriptions.pkce'
___________ ERROR collecting tests/llm/test_subscription_storage.py ____________
...
E   ModuleNotFoundError: No module named 'mudidi.llm.subscriptions.storage'
____________ ERROR collecting tests/llm/test_subscription_oauth.py ____________
...
E   ModuleNotFoundError: No module named 'mudidi.llm.subscriptions.oauth'
pytest: 3 errors in 0.10s
```

The tests were red at collection because the requested Task 2 production modules did not yet exist.

### GREEN

After implementation and final hardening, the same required command passed:

```text
uv run pytest -q tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_oauth.py
```

Observed result:

```text
..................                                                       [100%]
18 passed in 0.18s
```

An additional import smoke check confirmed that importing `mudidi.llm.subscriptions`, `pkce`, `oauth`, and `storage` does not load `cryptography`; encrypted storage imports it only when a store is instantiated.

No formatter, linter, build, or project-wide test suite was run.

## Concerns

- Provider endpoint host constants and provider-specific OAuth response/account parsing remain owned by later provider adapter tasks, as required.
- No live provider authentication or browser interaction was claimed; all focused OAuth tests use injected seams.

## Review-fix evidence

Regression tests were added before the review fixes and the required focused command was run to capture RED:

```text
uv run pytest -q tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_oauth.py
```

Observed result: the original 18 tests passed and 9 new review-regression tests failed, covering the double-question-mark URL, redirected endpoint, fixed port, malformed error parameters, callback authority/Host handling, stalled request timeout, and overflowing expiry.

Review fixes:

- Build authorization URLs with exactly one `?` for an endpoint without an existing query.
- Use a no-redirect default opener and validate any injected transport's reported final URL against HTTPS and the configured host allowlist.
- Require port `0` so the receiver always binds an ephemeral loopback port.
- Reject blank or duplicate singleton callback parameters, including `error`.
- Require absolute callback authorities and HTTP `Host` headers to match the generated `127.0.0.1:<port>` authority; path-only callback targets remain supported.
- Use a daemonized threaded callback server with accepted-socket timeouts so incomplete clients cannot stall callback timeout or shutdown.
- Validate integer/float expiry values without overflowing integer-to-float conversion and map out-of-range values to typed authentication errors.

After the fixes, the same required command was run:

```text
uv run pytest -q tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_oauth.py
```

Observed result:

```text
...........................                                              [100%]
27 passed in 0.38s
```

No formatter, linter, build, or project-wide test suite was run.

## Final review cleanup

Removed unused `urlopen` from the production OAuth imports and unused `HTTPError`, `Request`, and `urlopen` imports from the OAuth focused tests.

Verification command:

```text
uv run pytest -q tests/llm/test_subscription_pkce.py tests/llm/test_subscription_storage.py tests/llm/test_subscription_oauth.py
```

Observed result:

```text
...........................                                              [100%]
27 passed in 0.38s
```
