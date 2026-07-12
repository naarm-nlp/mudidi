# Local Web Application Design

Status: proposed design for implementation. This document is the product source
of truth; the implementation blueprint is in
`plans/local-web-app-blueprint.md`.

## Objective

Provide a local website that lets a user run MUDIDI on their own computer with
only the relevant LLM API key and local dictionary files. The first release is
for production inference, not benchmark sweeps or evaluation.

The application runs on `127.0.0.1`, stores run metadata locally, and calls the
selected LLM provider directly. It does not require a hosted MUDIDI service,
Redis, PostgreSQL, or Node.js at runtime.

## Locked product decisions

- Stack: FastAPI, Uvicorn, Jinja2, HTMX, Server-Sent Events, SQLite, and a
  dedicated inference subprocess.
- UI configuration is direct form interaction. Users do not need to write or
  import YAML.
- Form submissions are validated by the existing Pydantic `InferenceConfig`;
  the web layer must not construct CLI strings or invoke a shell.
- The initial release permits one active inference worker. Queued and completed
  runs remain visible.
- API keys come from `.env` or an in-memory session entry. They never enter
  SQLite, saved presets, resolved configuration, logs, or browser storage.
- The server binds to `127.0.0.1` by default. Non-loopback exposure is outside
  the first release unless authentication and additional network protections
  are explicitly added.
- Every web Stage 2 path requires a durable parse-rule approval checkpoint.
  Pass 2 accepts only a server-minted `ApprovedParseRules` capability bound to
  the run, review version, immutable managed snapshot, and SHA-256 digest.
- Catastrophic Stage 1 recovery is always available; there is no UI switch.
- Exact verifier patches are unlimited; there is no patch-count UI setting.

## Primary users

1. A researcher digitizing one scanned dictionary without learning the CLI.
2. A maintainer who needs stage-specific models, agentic verification, context
   files, and parse-rule control.
3. An expert using a custom LiteLLM model identifier or advanced OCR/VLM
   backend.

## Navigation

```text
MUDIDI
├── New Run
├── Active Run
├── Run History
├── Saved Presets
├── API Providers
├── Advanced Backends
├── Settings
└── Documentation
```

## New-run workflow

```text
Input → Pipeline → Model → Quality → Review → Start
```

After start, the run follows:

```text
Stage 1
  → Stage 2 Pass 1
  → Awaiting parse-rule review
  → explicit approval
  → Stage 2 Pass 2
  → Complete
```

Transcription-only runs omit all Stage 2 states. Stage 2-only runs use existing
Stage 1 text, but supplied or cached rules still enter review. A web route can
never select the CLI's legacy automatic Pass 2 policy.

## Configuration exposure

The UI uses progressive disclosure rather than displaying every internal flag.

### Basic

- PDF or page-image input
- PDF dictionary and introduction page ranges
- Output directory
- Complete, transcription-only, or Stage 2-only pipeline
- Provider, API key status, model, and reasoning
- Standard or verified quality preset

### Advanced

- Stage-specific models and reasoning
- Flat/column Stage 1 mode and typography preservation
- Introduction, alphabet, OCR hint, dictionary-language, and Toolbox context
- Representative parse-rule pages or an existing parse-rules file
- Stage-specific agentic verification, iteration budget, evaluator/rewriter
  models, minimum confidence, deterministic patches, and concrete-retry gate
- Batch size, page limit, and prompt caching

### Expert

- Stage 2 discovery-only runs that finish at the review checkpoint; Pass 2 is
  available only through approval or an authorized resume
- Temperature, guides, and media reference mode
- MinerU, PaddleOCR-VL, GLM-OCR, and Mathpix settings

Benchmark inputs, gold-source controls, sweep fields, evaluation thresholds,
experiment layout names, configuration `kind`/`version`, and internal output
subdirectories are not part of the production UI.

## Model selection

The model picker combines:

1. A bundled, dated catalog of MUDIDI-tested models.
2. Models returned by the configured provider's model-list API.
3. An always-available custom LiteLLM identifier.

Providers are explicit: direct Gemini, direct Anthropic, direct OpenAI,
OpenRouter, or custom LiteLLM routing. A direct provider is never silently sent
through OpenRouter.

Known models are annotated for image input, structured output, reasoning, and
recommended stages. Unknown custom models are accepted with a capability
warning. Model lists may be cached locally, but API keys may not be cached with
them.

## Out of scope for the first release

- Multi-user or remotely hosted operation
- Authentication and authorization
- Concurrent inference workers
- Benchmark sweeps and evaluation dashboards
- Collaborative parse-rule review
- A native desktop wrapper
- Cloud storage or a hosted database
