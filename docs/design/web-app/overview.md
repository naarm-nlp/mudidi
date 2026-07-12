# Local Web Application Design

Status: first-release implementation completed on `web/local-app-foundation`.
This document is the product source of truth; the implementation blueprint is in
`plans/local-web-app-blueprint.md`.

## Objective

Provide a local website that lets a user run MUDIDI on their own computer with
only the relevant LLM API key and local dictionary files. The first release is
for production inference, not benchmark sweeps or evaluation.

The application runs on `127.0.0.1`, stores run metadata locally, and calls the
selected LLM provider directly. It does not require a hosted MUDIDI service,
Redis, PostgreSQL, or Node.js at runtime.

## Locked product decisions

- Stack: FastAPI, Uvicorn, Jinja2, small vendored JavaScript, Server-Sent Events,
  SQLite, and a
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
- Every web Stage 2 path requires a durable MDF parsing guide approval checkpoint.
  Pass 2 accepts only a server-minted `ApprovedParseRules` capability bound to
  the run, review version, immutable managed snapshot, and SHA-256 digest.
- Catastrophic Stage 1 recovery is always available; there is no UI switch.
- Exact verifier patches are unlimited; there is no patch-count UI setting.

## Primary users

1. A researcher digitizing one scanned dictionary without learning the CLI.
2. A maintainer who needs stage-specific models, agentic verification, context
   files, and MDF parsing guide control.
3. An expert using a custom LiteLLM model identifier while keeping advanced
   OCR/VLM workflows in YAML or the CLI.

## Navigation

```text
MUDIDI
├── New Run
├── Active Run
├── Run History
├── Saved Presets
├── API Providers
└── Documentation
```

## New-run workflow

```text
Input → Pipeline → Model → Agentic → Review → Start
```

After start, the run follows:

```text
Stage 1
  → Stage 2 Pass 1
  → Awaiting MDF parsing guide review
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
- Agentic verification Yes/No, default No
- Optional Dictionary Profile with paired language/script questions, a
  free-form layout description, and
  entry-information context

### Advanced

- Stage-specific models and reasoning
- Flat Stage 1 output with typography preservation off
- Introduction and alphabet uploads plus direct additional instructions
- Representative MDF parsing guide pages or an existing MDF parsing guide file
- Optional user-uploaded MDF manual with a link to SIL's official documentation
- Stage-specific agentic verification, iteration budget, evaluator/rewriter
  models, minimum confidence, deterministic patches, and concrete-retry gate
- Batch size, page limit, and prompt caching

### Deliberately omitted from the dashboard

- discovery-only and direct Pass 2 commands;
- OCR hints and Stage 1 column mode;
- MinerU, PaddleOCR-VL, GLM-OCR, and Mathpix settings.

These remain supported through YAML and the CLI. Temperature, reasoning,
runtime controls, and custom models remain available in the dashboard.

Benchmark inputs, gold-source controls, sweep fields, evaluation thresholds,
experiment layout names, configuration `kind`/`version`, and internal output
subdirectories are not part of the production UI.

## Model selection

The model picker combines:

1. A bundled, dated catalog of MUDIDI-tested models.
2. Models returned by the configured provider's model-list API.
3. An always-available custom LiteLLM identifier.

Providers are explicit: direct Gemini, direct Anthropic, direct OpenAI,
OpenRouter, or **Other / advanced provider** routing through a user-supplied
LiteLLM identifier. A direct provider is never silently sent through OpenRouter.

Known models are annotated for image input, structured output, reasoning, and
recommended stages. Unknown custom models are accepted with a capability
warning. Model lists may be cached locally, but API keys may not be cached with
them.

Each active pipeline stage has its own provider-filtered picker. Inactive stage
pickers are hidden and disabled. OpenRouter uses manual per-stage model entry
and additionally accepts an optional **OpenRouter Provider** slug; blank means
automatic OpenRouter routing.

## Out of scope for the first release

- Multi-user or remotely hosted operation
- Authentication and authorization
- Concurrent inference workers
- Benchmark sweeps and evaluation dashboards
- Collaborative MDF parsing guide review
- A native desktop wrapper
- Cloud storage or a hosted database
