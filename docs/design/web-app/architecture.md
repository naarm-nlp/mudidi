# Local Web Application Architecture

## Components

```text
Browser
  ↕ HTML/forms/SSE
FastAPI application
  ├── Jinja templates and vendored static assets
  ├── Pydantic form/config boundary
  ├── SQLite repositories
  ├── provider model catalog
  └── job controller
         ↕ structured events
      worker subprocess
         └── configuration-native MUDIDI execution services
                └── LiteLLM → selected provider
```

The web app is an optional dependency group and CLI entry such as `mudidi web`.
It binds to `127.0.0.1` by default and may open the browser automatically.

## Required extraction boundary

The web layer must not manipulate `sys.argv`, shell out to `mudidi run`, or rely
on parsing console logs for progress. Before UI execution is integrated, expose
configuration-native services for:

- input materialization and run preparation
- Stage 1 page execution
- Stage 2 Pass 1 discovery
- Stage 2 Pass 2 execution requiring an `ApprovedParseRules` capability with
  run ID, review/version ID, immutable managed snapshot, SHA-256 digest, and
  approval timestamp
- cancellation checks
- typed progress/events and usage updates

The existing CLI continues to use these services and must retain outcome parity.
The current `ExecutionConfig → argparse.Namespace` compatibility adapter may
remain behind the service boundary temporarily, but no web route may depend on
it directly.

## Job execution

Inference is heavy, long-running work and runs in a dedicated subprocess, not a
FastAPI request or ordinary background task. The first release permits one
active worker.

The controller records the worker PID/process identity, consumes structured
events, updates SQLite, and broadcasts SSE. Cancellation terminates the worker
process group and leaves resumable artifacts intact.

On application startup, runs marked active but lacking a live owned worker are
reconciled to `interrupted`, never silently restarted.

## Run state machine

```text
draft → validated → queued
queued → running_stage1 | discovering_parse_rules | awaiting_parse_rules_review
running_stage1 → discovering_parse_rules | completed | failed | cancelled
discovering_parse_rules → awaiting_parse_rules_review | failed | cancelled
awaiting_parse_rules_review → running_stage2 | cancelled
running_stage2 → completed | failed | cancelled
active state → interrupted  (startup reconciliation)
interrupted → queued | awaiting_parse_rules_review | credentials_required
credentials_required → queued | awaiting_parse_rules_review
```

State transitions are checked transactionally. Resume persists the interrupted
phase, approval version, digest, and non-secret provider identity. A run without
an in-memory credential enters `credentials_required`; re-entry never persists
the key.

Approval is accepted only from `awaiting_parse_rules_review`. The server
validates draft bytes, writes and `fsync`s a content-addressed immutable
snapshot, then records its digest/review version and enqueues Pass 2 in one
SQLite transaction. `parse-rules.json` is only a compatibility copy. Startup
reconciliation removes or records orphaned snapshots and repairs incomplete
approval/enqueue states.

Web routes cannot construct `ApprovedParseRules`. The worker reads the immutable
snapshot once, validates its digest and schema, and supplies those exact loaded
rules to Pass 2 without resolving a cache or mutable path. CLI compatibility
uses a separate legacy/trusted-path policy that is unavailable to web routes.

## SQLite

SQLite stores metadata, not large artifacts or secrets. Initial tables:

- `runs`: identity, status, redacted resolved config, paths, progress,
  timestamps, worker identity, error summary, usage summary
- `run_events`: ordered structured events for recovery and UI replay
- `presets`: named non-secret form values
- `parse_rule_reviews`: generated/approved paths and hashes, timestamps, review
  status, regeneration count
- schema migration/version table

Use short transactions and a repository layer. Tests use isolated temporary
databases. Large images, text, MDF, logs, and agentic artifacts stay in the
existing output tree.

## Parse-rule artifacts

For Stage 2 discovery:

```text
parse-rules.generated.json  original Pass 1 output
parse-rules.draft.json      optional atomic review draft
parse-rules.json            compatibility copy, not approval authority
approved/<sha256>.json      immutable authoritative snapshot consumed by Pass 2
parse-rules-review.json     hashes, timestamps, samples, status, provenance
```

Existing CLI behavior expecting `parse-rules.json` is preserved. The web worker
must run discovery in `2-pass-1` mode and pause. It may invoke `2-pass-2` only
with the server-minted approval capability. Pass 2 records the approved SHA-256
digest in its run metadata so later edits cannot obscure which rules produced
the outputs.

## Progress protocol

Workers emit versioned events rather than free-form strings. Minimum event
types:

- run/stage started, completed, failed, cancelled
- page started/completed/failed
- verifier attempt and correction completed
- parse rules generated/approved
- usage/cost updated
- log message with level and safe structured context

Events include run ID, sequence number, timestamp, stage and page where
applicable. The controller persists before broadcasting so reconnecting clients
can replay from the last event ID.

## API keys and providers

Canonical keys include `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
and `OPEN_ROUTER_API_KEY`. Direct providers remain distinct from OpenRouter.

Keys loaded from `.env` are never returned. Temporarily submitted keys live only
in server memory, are passed to the worker environment, and are cleared after
the run/server lifetime. Logs, exceptions, SSE payloads, SQLite, presets, and
resolved configs are redacted.

The model catalog combines a bundled tested registry, live provider model-list
results held in process memory, and custom LiteLLM identifiers. Provider
failures fall back to the bundled catalog and never block custom entry.

## Security boundaries

- Loopback bind by default; reject non-loopback mode unless explicitly enabled
- Validate Host and Origin and protect state-changing forms against CSRF/DNS
  rebinding even on localhost
- No `shell=True`; subprocess arguments are structured
- Resolve and validate paths; prevent upload traversal and unsafe recursive
  deletion
- Require explicit confirmation for output deletion/start-over
- Escape generated text in templates; treat OCR/LLM output as untrusted
- Vendor HTMX/static assets so local operation does not depend on a CDN
- Bound upload sizes, event retention, log size, and SQLite growth
- Never expose arbitrary file download paths

## Testing strategy

- Unit: state transitions, repositories, form-to-config mapping, redaction,
  model filtering, parse-rule edits and validation
- Integration: worker events, cancellation, restart reconciliation, approval
  resume, existing CLI parity
- E2E: new run wizard, validation errors, live progress, parse-rule edit and
  approval, cancellation/resume, history, credential absence
- Network-free test mode with fake provider and deterministic worker
- Paid provider tests remain explicitly marked integration tests
