# Claude Subscription Routing Research

This document describes MUDIDI's isolated Claude subscription adapter and its
credential boundary.

## Status

`ClaudeResearchBackend` is available whenever subscription billing is selected.
Its status, errors, CLI output, API payloads, and dashboard surfaces expose
actionable authentication state without a blanket disclaimer or setup flag.

## Availability

Claude login, refresh, completion, and logout are enabled by default. The
dashboard shows the Claude account card alongside OpenAI and Google account
cards whenever subscription billing is selected. No fallback to an API key,
another provider, a gateway, or a relay is attempted.

## Researched values and scope

The adapter keeps these values private to its module and uses them only for the
research path:

- Authorization endpoint: `https://claude.com/cai/oauth/authorize`
- Token endpoint: `https://platform.claude.com/v1/oauth/token`
- Messages endpoint: `https://api.anthropic.com/v1/messages`
- OAuth client id: `9d1c250a-e61b-44d9-88ed-5944d1962f5e`
- Scope: `org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload`
- PKCE method: `S256`

A fresh shared PKCE transaction and the fixed callback URI
`http://localhost:54545/callback` are used for each login. In non-container mode,
the receiver binds to loopback on `127.0.0.1:54545`; in container mode, it
listens internally on all container interfaces on port `54545`. Compose publishes port `54545` only on
the host loopback interface. The adapter rechecks callback state before sending
the authorization code to the shared OAuth client. Authorization-code and
refresh-token exchanges use bounded JSON POSTs with the researched Claude Code
user agent through the shared OAuth transport; endpoint redirects and oversized
responses are rejected. All values are observed/researched behavior, not a
public Anthropic OAuth contract.

## Credential boundary

Access and refresh tokens are held in `SubscriptionCredential` only inside
the subscription backend and the separate encrypted `SubscriptionStore`.
When a real store is used, records are Fernet-encrypted in the subscription
SQLite database; the normal API-key credential path and its storage remain
separate. Token fields are excluded from ordinary model dumps and
representations. Status, errors, and metadata are redacted and contain only
safe account and expiry state.

The backend never reads or sets provider API-key environment variables. The
OAuth access token is sent as a bearer token in the provider request
`Authorization` header; it is not copied into the JSON body, status, logs,
subprocess arguments, or environment. The adapter does not send an `x-api-key`
header and never falls back to one.

## Request, discovery, and response boundary

The adapter discovers the authenticated account catalog through Anthropic's
bounded, paginated `/v1/models` endpoint using the same OAuth bearer and Claude
Code headers as completion requests. It retains safe display, creation, and
provider-order metadata; a 401 may refresh once before discovery fails closed.

Completion translates the provider-neutral request into an Anthropic
Messages-compatible JSON request. OAuth requests prepend the identity system
block `You are Claude Code, Anthropic's official CLI for Claude.` before any
caller system text. The adapter supports text content, user image blocks (as
validated base64 data URIs or HTTPS image URLs), PDF document blocks (as
validated base64 data URIs, HTTPS URLs, or Anthropic file IDs), reviewed
extended-thinking budgets for older models, adaptive thinking plus
`output_config.effort` for current models, and the provider's structured
JSON-schema output shape. It normalizes visible text, stop reason, and
input/output/cached/reasoning usage.

Unsupported content, unsupported schema keywords, malformed responses,
thought-only output, schema mismatches, authentication failures, and
policy-restricted responses fail closed with typed subscription errors. A 401
may perform exactly one OAuth refresh and request retry; a second 401 is an
expired-session error.

## Removal path

To remove this integration, stop selecting the `claude` subscription provider,
remove the `ClaudeResearchBackend` module and its focused tests/documentation,
and delete the Claude row from the separate subscription store with the store's
provider logout operation. Removing this module does not require changing or
migrating the existing LiteLLM/API-key path.
