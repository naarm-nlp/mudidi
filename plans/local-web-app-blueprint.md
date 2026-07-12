# Blueprint: MUDIDI Local Web Application

Status: reviewed; critical approval-boundary findings incorporated  
Objective: ship a localhost production-inference website that reuses MUDIDI's
typed configuration and extraction engine, requires only local files and an LLM
API key, and enforces human approval of Stage 2 parse rules.

## Source-of-truth documents

Read these before executing any step:

- `docs/design/web-app/overview.md`
- `docs/design/web-app/ux.md`
- `docs/design/web-app/architecture.md`
- `docs/design/web-app/parse-rule-review.md`
- `src/mudidi/config/yaml_config.py`
- `src/mudidi/cli/run.py`
- `src/mudidi/extraction/llm_two_stage.py`
- `src/mudidi/schemas/field_cheatsheet.py`

If this blueprint conflicts with a design document, stop and record a plan
mutation rather than silently choosing one.

## Global invariants

Every PR must preserve these invariants:

1. Existing CLI production and benchmark outcomes remain supported.
2. Web routes construct and validate typed configuration; they never assemble
   CLI strings, mutate `sys.argv`, use `shell=True`, or parse console logs for
   progress.
3. API keys never enter SQLite, presets, resolved config, logs, SSE payloads,
   exceptions rendered to users, or committed fixtures.
4. The server binds to `127.0.0.1` by default.
5. Only one inference worker is active in v1.
6. Web Stage 2 Pass 2 never starts without a server-minted
   `ApprovedParseRules` capability bound to the run, review version, immutable
   managed snapshot, recorded digest, and approval timestamp.
7. Browser disconnect or server restart never implies parse-rule approval.
8. Tests do not require paid API calls. Live provider tests remain opt-in.
9. Generated output files remain compatible with existing CLI tools.
10. No unrelated user work or generated benchmark outputs are committed.

## Pre-flight and workflow

The repository uses git with `main` and GitHub remote `origin`. Before each
step:

```bash
git status --short --branch
git fetch origin
git switch main
git pull --ff-only
git switch -c web/<step-slug>
```

If the worktree is dirty, preserve unrelated work and either use a separate
worktree or stage only an explicit allowlist. Record the base SHA in each PR.
Dependent work uses stacked branches and rebases after its parent merges. Never
push, open a PR, or merge without explicit user approval. Never merge until the
step's exit criteria pass.

Baseline verification:

```bash
uv run pytest -q
uv run mkdocs build --strict
uv run python scripts/generate_docs_reference.py --check
```

Before coding any step, add an **Inputs from dependencies** section to its PR
brief naming exact imported symbols, migration IDs/tables, event schema version,
routes/templates, and fixtures at the recorded parent SHA. If any expected
contract differs, record a plan mutation first. The stable initial handoffs are
`InferenceConfig`, `ApprovedParseRules`, execution event schema v1,
`CancellationToken`, repository protocols for runs/events/reviews, and worker
IPC schema v1.

## Dependency graph

```text
Step 1: execution + Pass 2 authorization contracts
   ├── Step 2: web foundation
   └── Step 3a: persistence schema/repositories
           └── Step 3b: subprocess controller/recovery
Step 2+3a ── Step 4a: wizard/config mapping
Step 4a ── Step 4b: uploads and destructive actions
Step 2+3a+3b ── Step 5: provider/model credentials
Steps 3a+3b+4a ── Step 6a: monitoring and SSE
Steps 3a+6a ── Step 6b: history and artifact browsing
Steps 1+3a+4a ── Step 7a: parse editor/immutable approval
Steps 3b+6a+7a ── Step 7b: authorized Pass 2 resume
Steps 2–7 ── Step 8a: packaging/platform support
Step 8a ── Step 8b: security and E2E
Step 8b ── Step 8c: release docs/final review
```

Parallel opportunities:

- Steps 2 and 3 may run in parallel after Step 1 if they do not edit the same
  dependency/config files.
- Lettered steps are separate PRs; their parent headings below are workstreams,
  not permission to combine them. Each PR carries its own verification subset.
- Step 4 owns run forms; Step 5 owns credential/model widgets and integrates
  only after their shared form protocol is merged.
- Web Stage 2 remains feature-gated until Steps 9 and 10 merge. No earlier step
  may start Pass 2 through a generic path or automatic all-stage policy.

Use the strongest available model for Steps 1, 3a, 3b, 7a, 7b, 8b, and the
final review. Default coding models are sufficient for presentation work.

---

## Step 1 — Configuration-native staged execution and progress contracts

Branch: `web/execution-contracts`  
Depends on: none  
Rollback: revert this PR; no database or UI artifacts exist yet.

### Context brief

`src/mudidi/cli/extract.py` currently owns orchestration and accepts a legacy
`argparse.Namespace`. `src/mudidi/cli/run.py` resolves a typed
`InferenceConfig`, adapts it to that namespace, and invokes extraction. Stage 2
already supports `2-pass-1` and `2-pass-2`, and
`TwoStageLLMExtraction.discover_parse_rules()` writes `parse-rules.json`.

The web app needs callable, typed execution and structured progress. Do not
rewrite the whole extraction engine. Introduce a narrow service boundary and
move only enough orchestration to make Stage 1, Pass 1, and Pass 2 independently
invocable. CLI parity is mandatory.

### Tasks

1. Define versioned execution event models: run/stage/page lifecycle,
   verification/correction, parse rules, usage, and safe log events.
2. Define a cancellation interface checked between pages and expensive stage
   transitions.
3. Define `ApprovedParseRules`: run ID, review/version ID, immutable managed
   snapshot path, SHA-256 digest, and approval timestamp. Web Pass 2 requires
   this server-side capability, not a generic path.
4. Introduce an execution service accepting `InferenceConfig` and callbacks or
   an event sink. It must support:
   - normal all-stage CLI behavior;
   - Stage 1 through completion;
   - Pass 1 discovery through artifact creation;
   - web Pass 2 with `ApprovedParseRules` whose exact loaded bytes are consumed;
   - CLI Pass 2 through a separate compatibility policy unavailable to web.
5. Keep `ExecutionConfig → Namespace` isolated behind the service if full
   removal is too risky. Document the adapter as temporary debt.
6. Make CLI `run` call the same service. Preserve automatic CLI all-stage
   behavior; parity covers output layout, manifests, and stage semantics.
7. Add fake/no-network strategies for deterministic progress tests.
8. Document event ordering and terminal-event guarantees.

Suggested locations:

```text
src/mudidi/execution/
  service.py
  events.py
  cancellation.py
  fake.py
```

### Tests

- Unit tests serialize/validate every event type.
- Service tests prove Stage 1 → Pass 1 → Pass 2 ordering.
- Cancellation tests stop before the next page/stage and leave valid artifacts.
- Existing CLI execution/config tests remain green.
- Parity test compares resolved paths/models/stages with the current adapter.

### Verification

```bash
uv run pytest tests/execution tests/cli tests/config -q
uv run pytest -q
```

### Exit criteria

- A Python caller can execute stages without parsing CLI arguments or stdout.
- Progress is structured and deterministic enough for persistence/SSE.
- Web Pass 2 rejects bare paths, caches, external/generated files, and
  CLI-trusted authorization. CLI keeps an isolated compatibility factory.
- Existing CLI tests and representative dry runs are unchanged.

---

## Step 2 — FastAPI/Jinja/HTMX foundation and application shell

Branch: `web/application-foundation`  
Depends on: Step 1 contracts, but may use a fake execution service initially  
Rollback: remove the optional dependency group and `web/` package.

### Context brief

The selected stack is a server-rendered Python application. No React/Node
runtime is required. The wireframe is
`docs/design/web-app/assets/new-run-wireframe.png`. HTMX and related static
assets must be vendored for offline local operation.

### Tasks

1. Add a `web` optional dependency group containing compatible pinned ranges for
   FastAPI, Uvicorn, Jinja2, multipart uploads, and test support.
2. Add `mudidi web` to the existing command tree. Default host is `127.0.0.1`;
   choose a documented port and optional `--no-open-browser`.
3. Create an application factory with explicit settings and lifespan hooks.
4. Build the shared shell, navigation, flash/error components, form controls,
   and responsive layout from the wireframe.
5. Vendor static dependencies; add content hashes or version comments.
6. Add health and home/New Run placeholder routes.
7. Add Host/Origin groundwork and safe template defaults. Do not expose the
   server on `0.0.0.0` by default.
8. Package templates/static files in wheels and verify installed-resource
   lookup rather than relying on the repository CWD.

Suggested locations:

```text
src/mudidi/web/
  app.py
  settings.py
  routes/
  templates/
  static/
```

### Tests

- TestClient exercises health, shell, static resources, and unknown routes.
- Snapshot/semantic tests assert navigation and accessibility landmarks.
- Packaging test builds a wheel, installs it in a temporary environment, and
  renders the app with packaged assets.
- CLI test verifies loopback default and sparse web options.

### Verification

```bash
uv run pytest tests/web/test_app.py tests/web/test_packaging.py -q
uv build
```

### Exit criteria

- `uv run mudidi web` starts a functional loopback site.
- The shell matches the approved information architecture.
- No external CDN is required.
- Wheel-installed templates and assets work.

---

## Step 3 — SQLite persistence and subprocess job controller

Branch: `web/job-controller`  
Depends on: Step 1  
Rollback: remove app database files; output artifacts remain untouched.

### Context brief

Inference must not run inside a request or FastAPI `BackgroundTasks`. The first
release has one active subprocess. SQLite stores metadata/events only; outputs
remain in existing directories. Startup reconciliation must handle a process
that died while the app was closed.

### Tasks

1. Define explicit run statuses and a transition function matching the design
   state machine, including `credentials_required`, `resume_phase`, approval
   version, and digest. There is no `queued → running_stage2` edge.
2. Implement schema migrations/versioning and repositories for runs, events,
   presets, parse-rule reviews, and model-catalog cache.
3. Implement atomic job claiming with a transactional lease or partial unique
   index so two requests cannot start two active workers. Require one Uvicorn
   app process in v1; fail startup on a multi-worker configuration.
4. Launch workers without `shell=True`, pass a run ID and non-secret config
   reference, and capture structured event IPC.
5. Persist each event before making it visible to subscribers.
6. Implement cancellation using a process group and bounded graceful shutdown,
   then forced termination if necessary.
7. Reconcile stale active runs to `interrupted` on startup. Never infer that an
   unrelated PID is the owned worker; store and validate process identity.
8. Implement phase-aware resume. Pre-approval runs return to discovery/review;
   only a valid approval capability resumes Pass 2. Missing in-memory keys move
   to `credentials_required` and require re-entry without persisting secrets.
9. Bound event retention/log size and configure safe SQLite concurrency/WAL
   behavior appropriate for one app process.

### Tests

- Transition-table tests cover every allowed and rejected edge.
- Concurrent start requests result in one claimed worker.
- Worker crash, cancellation, and app restart reconcile correctly.
- Event sequence numbers are monotonic and replayable.
- No secret marker reaches database rows or events.
- Crash injection covers claim-before-start and approval-before-claim recovery.
- Temporary SQLite tests do not touch user outputs.

### Verification

```bash
uv run pytest tests/web/test_run_states.py tests/web/test_jobs.py \
  tests/web/test_repositories.py -q
```

### Exit criteria

- A fake run can start, emit progress, cancel, fail, resume, and complete.
- One-active-worker invariant survives concurrent requests and restart.
- Database contains no large artifacts or credentials.

---

## Step 4 — New Run wizard and typed production configuration

Branch: `web/run-wizard`  
Depends on: Steps 2 and 3a  
Rollback: retain shell; remove wizard routes/forms.

### Context brief

Users configure runs through forms, not YAML. The server still constructs an
`InferenceConfig` and applies its cross-field validation. The exact fields and
conditional behavior are in the UX specification. Benchmark-only and internal
fields are excluded.

### Tasks

1. Define server-side form models for Input, Pipeline, Model, Quality, and
   Review. Keep browser/session draft state non-secret and bounded.
2. Implement conditional form fragments with HTMX: PDF ranges, Stage 2,
   context, stage-specific models, agentic custom settings, and advanced
   backends.
3. Map form state to `InferenceConfig` in one tested function. Do not duplicate
   Pydantic validation rules in routes.
4. Implement local upload/path handling with traversal protection, size limits,
   sanitized app-managed filenames, and cleanup policy.
5. Implement representative-page selection and prerequisite validation.
6. Implement Review validation and redacted resolved summary.
7. Detect existing runs and present resume/choose-another/delete-and-start-over.
   Destructive deletion needs a typed confirmation and constrained root.
8. Add non-secret saved presets or defer persistence wiring behind a repository
   interface from Step 3.

### Tests

- Every visible choice maps to the expected `InferenceConfig`.
- Invalid combinations render field-specific errors.
- PDF/directory, Stage 1-only, Stage 2-only, and complete runs validate.
- Upload traversal, oversized upload, unsafe output deletion, and malformed page
  ranges are rejected.
- API keys never appear in form draft serialization or summaries.

### Verification

```bash
uv run pytest tests/web/test_wizard.py tests/web/test_form_config.py \
  tests/web/test_uploads.py -q
```

### Exit criteria

- A user can configure every agreed production behavior without YAML.
- The final object is a validated `InferenceConfig`.
- Review accurately describes the execution that will occur.

---

## Step 5 — Provider credentials and model catalog

Branch: `web/model-catalog`  
Depends on: Step 2; coordinate form integration with Step 4  
Rollback: custom model entry remains available.

### Context brief

MUDIDI routes through LiteLLM. Direct Gemini, Anthropic and OpenAI are distinct
from OpenRouter. Canonical environment variables are `GEMINI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `OPEN_ROUTER_API_KEY`.

The picker combines a bundled tested catalog, the provider's live model list,
and custom LiteLLM identifiers. A listed model is not automatically known to be
Stage 1-compatible; capability metadata for recommended models is curated.

### Tasks

1. Define provider and model descriptor interfaces with capabilities, display
   label, tested date, recommended stages, and deprecation/status fields.
2. Add a small bundled catalog with tests and a documented update process.
3. Implement provider adapters for model discovery with timeouts, pagination,
   filtering and redacted errors. Mock every provider in tests.
4. Cache non-secret results with expiry; add Refresh.
5. Implement in-memory temporary credential storage and `.env` presence checks.
   Never return configured key values.
6. Group dropdown results as Recommended, Available, and Custom.
7. Block known non-image models for Stage 1; warn, but do not block, unknown
   custom models.
8. Ensure a provider outage falls back to bundled/custom selection.

### Tests

- Provider adapter contract tests cover pagination and failure.
- Filtering excludes irrelevant known embeddings/audio-only models.
- Stage capability warnings/blocks are correct.
- Secret canaries are absent from logs, cache, responses, exceptions and DB.
- Custom identifiers round-trip to `InferenceConfig` unchanged.

### Verification

```bash
uv run pytest tests/web/test_model_catalog.py tests/web/test_credentials.py -q
```

### Exit criteria

- Direct and OpenRouter choices are explicit.
- Model selection works offline with bundled/custom entries.
- Credential handling satisfies the global secret invariant.

---

## Step 6 — Active Run, SSE progress, page detail, usage and history

Branch: `web/run-monitoring`  
Depends on: Steps 3 and 4  
Rollback: job controller remains usable via tests/internal API.

### Context brief

The Active Run tabs are Overview, Parse Rules, Pages, Live Logs, Outputs and
Usage. Persisted events are the source of truth; SSE is a delivery mechanism.
History must survive restart and link awaiting-review runs to the Parse Rules
tab.

### Tasks

1. Add run creation/start/cancel/resume endpoints with CSRF/Origin protection and
   idempotency where relevant.
2. Implement SSE replay with Last-Event-ID and keepalives. Reconnect must not
   lose terminal events.
3. Build Overview stage/page progress and recent event panels.
4. Build Pages list/detail with safe source image, transcription, verifier and
   MDF artifact access. Artifact endpoints use allowlisted roots and media types.
5. Build human-readable logs and expert raw-event disclosure.
6. Build Usage summaries by stage/model using persisted usage events.
7. Build Run History filters and context actions; distinguish deleting a record
   from deleting output files.
8. Add empty, interrupted, failed, cancelled and completed states.

### Tests

- SSE initial stream and reconnect replay ordered events exactly once from the
  requested cursor.
- Cancel/resume buttons are state-correct and idempotent.
- Artifact traversal/download attacks fail.
- History persists and filters accurately after app restart.
- Browser E2E covers fake run progress, cancellation and completion.

### Verification

```bash
uv run pytest tests/web/test_run_routes.py tests/web/test_sse.py \
  tests/web/test_artifacts.py -q
uv run pytest tests/e2e/test_web_run.py -q
```

### Exit criteria

- A fake full run is observable without page refresh.
- Reconnection and restart do not corrupt status.
- Outputs and logs are safely inspectable.

---

## Step 7 — Mandatory parse-rule review, approval and Pass 2 resume

Branch: `web/parse-rule-review`  
Depends on: Steps 1, 3, 4 and 6  
Rollback: Stage 2 web execution remains disabled; never bypass approval.

### Context brief

This is the highest-risk feature. Read
`docs/design/web-app/parse-rule-review.md` completely. The current Pass 1 output
schema is `DictionaryMarkerCheatsheet` with dictionary name, marker rows, rules,
and abbreviations. Existing CLI Pass 2 reads `parse-rules.json`.

The web flow writes generated and draft variants. Approval creates a
content-addressed immutable snapshot; canonical `parse-rules.json` is only a
compatibility copy. A server-minted capability, not a path, authorizes Pass 2.

### Tasks

1. Make every web complete/Stage 2 path—including supplied, imported, or cached
   rules—transition to `awaiting_parse_rules_review`. Do not expose direct Pass
   2 or allow web routes to choose the CLI automatic policy.
2. Atomically save `parse-rules.generated.json` and review metadata. Do not
   expose partially written files.
3. Build the Parse Rules tab states and complete schema editor.
4. Add representative-page evidence: safe image and Stage 1-text views.
5. Add draft save, reset, validate, sample-change/regenerate and approval.
6. Share one semantic validator across editor, approval, and worker startup. It
   normalizes markers and rejects empty/invalid codes, duplicates, and normalized
   collisions such as `lx` versus `\\lx`.
7. Approval follows a crash-consistent protocol: validate draft bytes; write and
   `fsync` `approved/<sha256>.json`; then in one SQLite transaction record the
   review/version/digest and enqueue Pass 2. Update `parse-rules.json` only as a
   compatibility copy and reconcile orphaned snapshots at startup.
8. Mint `ApprovedParseRules` only from that committed database row. The worker
   reads the immutable snapshot once, validates its digest/schema, and passes
   those exact loaded bytes/model to Pass 2 without later cache/path resolution.
9. Make approval transactional/idempotent so double-click/concurrent requests
   launch only one worker.
10. Treat post-approval edits as a new invalidation/rerun workflow; never mutate
   rules used by an active/completed Pass 2.
11. On restart or failure, validate `resume_phase`, approval version, and digest
    before Pass 2 resume; missing temporary credentials require re-entry.
12. Existing parse-rule files enter the same review state after being copied
    into the run, never execute from an external mutable path.

### Tests

- Pass 2 cannot start from generated, draft, invalid, missing or tampered rules.
- Bare paths and cached/imported/CLI-trusted rules cannot authorize web Pass 2.
- Closing browser/server leaves the run awaiting review.
- Approval double-submit starts exactly one Pass 2 worker.
- Edits cover markers, descriptions, rules and abbreviations and validate via
  `DictionaryMarkerCheatsheet`.
- Regeneration invalidates approval and preserves provenance/usage.
- Restart restores review; approved worker failure resumes Pass 2 without
  repeating Pass 1.
- E2E: complete run pauses, user edits a marker/rule, approves, and Pass 2 output
  records the approved digest.
- Crash injection covers snapshot-before-DB, DB-before-claim, claim-before-start,
  regeneration-versus-approval, and two approvals from separate connections.

### Verification

```bash
uv run pytest tests/web/test_parse_rule_review.py -q
uv run pytest tests/e2e/test_parse_rule_checkpoint.py -q
```

### Exit criteria

- It is impossible through supported web routes for Pass 2 to bypass approval.
- Approved content and digest exactly match what Pass 2 consumes.
- Awaiting-review status is durable and obvious in Active Run and History.

---

## Step 8 — Packaging, hardening, E2E acceptance and release documentation

Branch: `web/release-hardening`  
Depends on: Steps 2–7  
Rollback: do not advertise/install the web extra; CLI remains supported.

### Context brief

This step turns the integrated feature into a releasable local application. It
must test wheel-installed behavior, real subprocess orchestration with fake LLM
providers, network boundaries, secret redaction and the complete user journey.

### Tasks

1. Finalize `mudidi[web]` installation and `mudidi web` documentation for uv.
2. Add a fake/offline provider and deterministic sample fixture for E2E and
   demonstrations.
3. Add CI jobs for web unit/integration/E2E, wheel install and static/reference
   drift. Cache browsers responsibly.
4. Threat-model localhost risks: DNS rebinding/Host, CSRF/Origin, XSS from LLM
   output, path traversal, arbitrary download/delete, subprocess injection,
   secret leakage, unbounded upload/events/logs, stale PID reuse.
5. Implement/fix every high-severity threat and test it.
6. Test clean install on Linux and macOS; document Windows status explicitly.
7. Add startup diagnostics for writable app data, database migration, provider
   keys and output permissions without revealing secrets.
8. Update README/MkDocs with web quickstart, UI guide, parse-rule checkpoint,
   troubleshooting, privacy and limitations.
9. Update codemaps and `.reports/codemap-diff.txt`.
10. Run an adversarial product review against every global invariant and screen
    acceptance path.

### Acceptance journeys

1. New user installs web extra, supplies direct provider key, runs Stage 1 and
   downloads text.
2. Complete run pauses after Pass 1, survives restart, edits/approves rules and
   completes Pass 2.
3. User cancels mid-page, restarts app and explicitly resumes.
4. Provider model listing fails; bundled/custom model still runs.
5. Invalid key/model/path shows actionable redacted errors.
6. Existing output offers resume vs explicit start-over safely.

### Verification

```bash
uv sync --extra dev --extra docs --extra web
uv run pytest -q
uv run mkdocs build --strict
uv run python scripts/generate_docs_reference.py --check
uv build
# install wheel in a fresh temporary environment and run web smoke test
```

### Exit criteria

- All acceptance journeys pass without paid APIs.
- Full tests, strict docs and wheel smoke tests pass in CI.
- No unresolved high-severity security findings.
- Documentation accurately describes local-only scope and parse-rule approval.

---

## Anti-pattern catalog

Reject a PR that introduces any of these without an approved plan mutation:

- Running inference in a request handler or FastAPI `BackgroundTasks`
- Shelling out to the public CLI from the web server
- Scraping stdout for progress
- Keeping a web worker alive while waiting hours for human approval
- Treating generated parse rules as approved
- Accepting a bare parse-rule path or direct `2-pass-2` request from a web route
- Storing API keys in SQLite, presets, cookies, localStorage or resolved config
- Binding publicly by default
- Using a CDN required for the app to function
- Adding Celery/Redis/PostgreSQL for the single-user v1
- Duplicating Pydantic validation logic in JavaScript
- Serving arbitrary user-supplied paths
- Deleting an output tree from an unchecked path
- Using PID existence alone as proof of worker ownership
- Allowing multiple start/approve requests to spawn duplicate workers
- Combining the web feature with benchmark/evaluation UI scope
- Rewriting the entire extraction engine before delivering a vertical slice

## Review record

On 2026-07-12 an adversarial architecture review rejected the original draft's
direct `queued → running_stage2` edge and generic parse-rule path. This revision
adds `ApprovedParseRules`, immutable content-addressed snapshots, crash-consistent
filesystem/database ordering, exact-byte consumption, phase-aware resume,
credential re-entry, one-process/one-worker enforcement, earlier security
gates, and smaller lettered PR units. Critical findings are resolved; remaining
medium implementation details are acceptance criteria for their owning PRs.

Security is incremental, not deferred to Step 8: the PR introducing a form,
artifact route, subprocess, SSE stream, or rendered model output must include
its CSRF/Origin, Host, path-root, injection, XSS, size-limit, and redaction tests.
V1 supports macOS and Linux. Windows remains documented as experimental until
process-group cancellation, browser launch, path handling, wheel resources, and
clean data-directory initialization pass on Windows CI.

## Plan mutation protocol

Record mutations at the end of this file before changing dependency order or
scope.

Each mutation includes:

```text
Date:
Decision:
Reason/evidence:
Affected steps and dependencies:
New risks:
Verification changes:
Approved by:
```

Allowed mutation types:

- Split an oversized PR while preserving exit criteria.
- Insert a prerequisite discovered through evidence.
- Reorder only when dependency and shared-file analysis is updated.
- Defer a feature only if the affected acceptance journey is explicitly moved
  out of the release.
- Abandon the plan if the execution boundary or product goal materially changes;
  write a replacement blueprint instead of silently repurposing this one.

## Mutation log

None.
