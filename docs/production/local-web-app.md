# Local web application

MUDIDI includes a localhost website for running production inference without
writing YAML or assembling CLI flags. It executes the pipeline on your own
computer; only model requests leave the machine for the provider selected by
the run.

## Install and launch

Install MUDIDI with the web dependencies:

```bash
uv sync --extra web
```

Add the API key for your selected provider to `.env`, or enter a temporary key
on the website's **Providers & Keys** screen. Then launch the app:

```bash
uv run mudidi web
```

MUDIDI opens `http://127.0.0.1:8765` in your browser. The server only accepts
`127.0.0.1` or `localhost`; it is not designed for LAN or public deployment.
Use `--no-browser` to prevent automatic browser opening, and use `--port` if
port 8765 is already occupied.

## Create a run

The **New Run** screen exposes the production settings most users need:

1. Enter a local PDF/page directory, or upload a PDF or page images into
   run-owned local storage.
2. Choose an output directory.
3. Select Stage 1 only, Stage 2, or the complete pipeline.
4. Select Anthropic, OpenAI, Gemini, OpenRouter, or custom LiteLLM routing.
5. Choose a bundled multimodal model or enter a custom model identifier.
6. Select the quality and reasoning settings, then review and start the run.

Use **Require a new or empty directory** for a fresh run. Select **Resume
compatible existing artifacts** only when you deliberately want the pipeline's
manifest-based resume behavior; the website never silently deletes output.

The browser form is converted into the same strict typed configuration used by
the CLI. Advanced configuration remains available through the YAML/CLI
workflow; the website does not store or generate a user-facing YAML file. Its
progressive disclosures also expose stage-specific models, agentic verification,
context and guide files, runtime controls, and the MinerU, PaddleOCR-VL,
GLM-OCR, and Mathpix expert backends.

The provider screen contains a dated offline model catalog. With a provider key
available, **Refresh available models** queries that provider's official model
list and keeps the non-secret result in process memory. Provider discovery
failure never blocks the bundled catalog or custom LiteLLM identifiers.

## Parse-rule approval checkpoint

For Stage 2 and complete runs, MUDIDI deliberately pauses after discovering
parse rules. Open **Parse Rules**, review every marker and rule, make any edits,
and choose **Approve & continue**.

Pass 2 cannot start without that explicit approval. The approved rules are
stored as an immutable, run-bound snapshot, and Pass 2 verifies the snapshot
before using it. Saving a draft does not authorize extraction.

## Monitor and inspect

Each run has these views:

- **Overview** — durable status, progress events, and cancellation.
- **Parse Rules** — structured Stage 2 rule review and approval.
- **Pages** — bounded previews of Stage 1 and Stage 2 page text.
- **Page evidence** — the safe source image/PDF, transcription, MDF, events,
  and related downloads for one page.
- **Live Logs** — bounded worker diagnostics with known API keys redacted.
- **Outputs** — downloads constrained to the run's validated output directory.
- **Usage** — token and reported cost totals from generated usage files.

The browser receives progress through resumable server-sent events. Run history,
events, and parse-rule review state survive an app restart. A run that was active
when the app stopped is marked interrupted instead of being silently resumed.
The user can then resume it explicitly; approved Pass 2 resumes only from the
authenticated immutable parse-rule snapshot.

**Run History** can filter by run ID, status, and provider. A validated run can
also be saved as a reusable preset. Presets contain typed non-secret settings
and revalidate their paths when preparing a new run.

## Credentials and local data

Provider keys are resolved from temporary process memory first and `.env` or
the process environment second. They are sent to the child worker through a
private standard-input message and are not written to the run database,
configuration snapshot, command line, or URL.

By default, web metadata is stored under `~/.local/share/mudidi/`. Select a
different location with:

```bash
uv run mudidi web --data-dir path/to/private-app-data
```

Generated dictionary files remain in the output directory selected for the
run. The app allows only one inference worker at a time so competing runs do
not overwrite shared pipeline state or unexpectedly multiply API usage.

## Troubleshooting

- **API credential required** — add the provider key on **Providers & Keys** or
  to `.env`, then start the prepared run again.
- **Another inference worker is active** — wait for, cancel, or finish the
  current run before starting another.
- **Awaiting parse-rule review** — review and explicitly approve the generated
  rules; this pause is intentional.
- **Interrupted** — the local server exited while the worker was active. Inspect
  its logs and outputs, then explicitly resume the run.
- **No output or usage files yet** — those views are valid before extraction has
  produced their corresponding artifacts.

For the complete advanced configuration surface, use the
[YAML configuration guide](../getting-started/configuration.md) and
[CLI reference](../reference/cli.md).
