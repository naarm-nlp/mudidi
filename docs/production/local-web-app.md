# Local web application

MUDIDI includes a localhost dashboard for production inference without YAML or
CLI flags. The pipeline and files stay on the computer running MUDIDI; only
model requests are sent to the selected provider.

## Start the dashboard

### Docker (recommended)

Docker provides the same reproducible MUDIDI environment on macOS, Windows, and
Linux. Install Docker Desktop on macOS or Windows, or Docker Engine with the
Compose plugin on Linux. Start Docker, clone this repository, and run from its
directory:

```bash
docker compose up --build
```

The first build downloads and installs the image dependencies. When the log
says the application has started, open `http://localhost:8000`. The Compose
port is published only on
`127.0.0.1`; do not change it to a public interface because this is a local,
single-user application.

For later starts, no rebuild is required:

```bash
docker compose up
```

To run in the background and follow its logs:

```bash
docker compose up -d
docker compose logs -f
```

Stop a foreground run with `Ctrl+C`. Stop and remove the container with:

```bash
docker compose down
```

The `mudidi-data/` directory beside `compose.yaml` persists the database,
encrypted credentials and encryption key, presets, managed uploads, worker
artifacts, and generated outputs. Keep that directory private and back it up as
one unit. Rebuilding or replacing the container does not delete it.

If Docker reports that it cannot connect to the Docker daemon, start Docker
Desktop (macOS or Windows) or the Docker service (Linux), wait until the engine
is ready, and repeat the Compose command.

### Native dashboard with uv

Native runs use the tools installed on the host computer. Install `pdftk` before
processing a multi-page PDF:

=== "macOS"

    ```bash
    brew install pdftk-java
    ```

=== "Ubuntu or WSL2"

    ```bash
    sudo apt update
    sudo apt install -y pdftk-java
    ```

Then install the web dependencies and start MUDIDI:

```bash
uv sync --frozen --extra web
uv run mudidi web
```

Save the API key for your model provider under **API credentials** on the
**New Run** screen by clicking **Save key** beside that provider.
MUDIDI opens `http://127.0.0.1:8000`. It binds to loopback and is not intended
for public or LAN deployment. Use `--no-browser` or `--port` when needed.

`uv` installs MUDIDI's Python dependencies, but it does not install operating
system programs such as `pdftk`. Docker already includes `pdftk-java` in its
image.

## Create a run

The **New Run** screen asks you to:

1. Choose exactly one dictionary PDF.
2. Enter the required **PDF dictionary pages** to process. You may enter one
   page (`5`), one range (`10-20`), comma-separated pages (`1,5,9`), or a
   combination (`1,5,10-20`).
3. Enter an output directory on the same computer running MUDIDI.
4. Select one pipeline:
   - **Complete digitization**
   - **Transcription only**
   - **Parse transcription into MDF (Multi-Dictionary Formatter)**
5. Choose a provider, model, and independent reasoning level for each active
   stage.
6. Optionally enable **Agentic verification**.
7. Review the resolved configuration and start. The review includes the
   selected dictionary pages, representative parsing-guide pages, Stage 1
   model, Stage 2 Pass 1 model, and Stage 2 Pass 2 model.

The dashboard cannot continue unless the dictionary PDF, dictionary pages, and
the other required fields in the form are present. Page numbers are 1-based:
zero, negative numbers, descending ranges, and pages beyond the uploaded PDF's
page count are rejected. Invalid submissions remain on **New Run** and show a
red outline and explanation on each field that needs attention.

The grey values beginning with `ex:` are examples only; they are not submitted
as values. The current examples are `ex: 30-35` for dictionary pages,
`ex: 1-5` for introduction pages, and `ex: 30-32` for representative MDF
parsing-guide pages.

Browser-selected inputs are copied into an input bundle owned by the run. This
allows review, restart, and resume without depending on the original browser
selection. The output directory remains a text field because a standard browser
cannot disclose an arbitrary absolute folder path to a localhost server.

The web dashboard does not accept page images, multiple files, or a folder of
images. Those input modes remain available through YAML and the CLI.

Dashboard transcription always uses flat Stage 1 output and does not preserve
typography. OCR hints, column mode, and expert OCR/VLM backends remain available
through YAML and the CLI but are intentionally absent from the dashboard.

## Dictionary Profile

The **Dictionary Profile** is optional and can improve extraction accuracy. It
asks for:

- headword language and script;
- translation, gloss, or definition languages and their scripts;
- a free-form description of the page arrangement;
- the information types found in entries.

Leave the whole section blank when you are unsure. The profile is guidance, not
source text, and MUDIDI still checks the scanned page. It does not strictly
limit discovery to the information types you enter: the model may identify
additional entry structures and rules visible in the dictionary.

## Additional context

The dashboard can attach:

- required PDF dictionary page numbers using one number, an ascending range,
  comma-separated numbers, or a combination such as `1,5,10-20`;
- optional PDF introduction page numbers using the same syntax;
- a character inventory entered directly as text;
- Stage 1 and Stage 2 additional instructions entered directly as text;
- optional representative MDF parsing guide pages using the same page syntax;
- an existing MDF parsing guide JSON file.

All page numbers must be positive Arabic numbers within the uploaded PDF.
Representative MDF parsing-guide pages must also be included in the selected
dictionary pages. Roman numeral page specifications are not accepted.

Additional instructions are stored as bounded UTF-8 files in the run input
bundle and passed through the same prompt-guide mechanism used by YAML/CLI.

## MDF parsing guide and MDF manual

These names refer to different things:

- **MDF parsing guide** is inferred by the LLM for this particular dictionary.
  It describes the MDF markers and structural rules that Stage 2 should use.
- **MDF manual** is an optional general reference PDF describing MDF markers.

For the MDF manual, choose one of:

- upload your own MDF manual PDF;
- open the
  [official SIL Toolbox Reference Manual](http://www.fieldlinguiststoolbox.org/ToolboxReferenceManual.pdf)
  in a new browser tab; or
- continue without an MDF manual.

The relevant MDF information in SIL's manual starts on page 31 and spans pages
31–95 (65 pages). For better relevance and lower token cost, extract and upload
only the pages describing MDF markers or tags relevant to your dictionary.

If you do not know which markers are relevant, first run **Complete
digitization** without an MDF manual. At the human checkpoint, inspect the MDF
parsing guide inferred by the LLM from your dictionary pages. You can then start
a new run and upload only the corresponding marker pages from the official
manual. The same workflow appears in the dashboard's MDF-manual information
tooltip.

MUDIDI does not bundle or redistribute SIL's manual. A PDF is copied into the
run-owned input bundle only when you upload it yourself. The manual is optional
and does not replace the dictionary-specific MDF parsing guide.

## Agentic verification

Agentic verification defaults to **No** because it adds evaluator and correction
model calls. Selecting **Yes** opens **Custom verification** directly below the
control. Stage 1 and Stage 2 verification are checked initially; you can disable
either applicable stage and configure correction iterations, confidence,
evaluator/rewriter models, reasoning, deterministic patches, and concrete retry
evidence.

Inactive stages are never verified. For example, transcription-only ignores
Stage 2 verification even if a forged form submission includes it.

## Models and providers

The provider-specific catalog is combined with optional live model discovery and
an **Other model** entry. OpenRouter uses a manually entered model such as
`qwen/qwen3-235b-a22b`; MUDIDI adds the LiteLLM `openrouter/` prefix. The
optional **OpenRouter Provider** slug pins an endpoint preference, while blank
uses automatic routing.

Selecting **None / lowest supported** reasoning resolves to `low`; MUDIDI only
sends reasoning controls to model families known to support them.

## Complete-digitization workflow

For a complete run, the dashboard displays these steps in execution order:

1. **Stage 1 — Transcription** processes all selected dictionary pages.
2. **MDF parsing guide discovery** samples the configured representative pages
   from the Stage 1 transcriptions. If no pages were specified, MUDIDI selects
   them automatically.
3. **Review parsing guide** pauses the run for human review and approval.
4. **Stage 2 — MDF conversion** converts each Stage 1 transcription into MDF
   using the approved guide snapshot.

The Overview reports the active stage, completed-page count, current page, and
failure details. Updates arrive while the run is active. Select **Cancel run**
to interrupt an active worker. An interrupted run can be resumed from its safe
checkpoint, and compatible completed artifacts are reused when the output
policy is **Resume**.

## MDF parsing guide review checkpoint

Complete and MDF-parsing runs pause when Stage 2 **infers** an MDF parsing guide.
Open **MDF parsing guide**, review its markers and guide rules, make any edits,
and select **Approve and continue MDF parsing**.

Saving a draft is not approval. Page parsing cannot start without a server-minted
approval bound to the exact run, review version, immutable snapshot, digest, and
approval time.

After approval, the dashboard page becomes read-only. It shows the immutable
snapshot actually used by Stage 2; editing it after the run finishes cannot
change the completed output or the guide used by a future run. To reuse revised
rules, save or upload the intended parsing-guide JSON when configuring a new
run.

A user-uploaded existing MDF parsing guide follows a different path: MUDIDI
copies it into the run-owned input bundle, validates its JSON and marker format,
and uses it directly without Pass 1 discovery or a human checkpoint. The guide
is validated again when Stage 2 loads it. Uploading a guide therefore means the
user is supplying the intended parsing rules, while malformed files still fail
safely before MDF parsing.

## Monitor, inspect, and correct pages

Run views include:

- **Overview** — durable status, progress, resume, and cancellation.
- **MDF parsing guide** — structured review before approval and the read-only
  approved snapshot afterward.
- **Page Viewer & Editor** — the rendered source page beside editable generated
  text, with previous/next controls and a slider across processed pages.
- **Live Logs** — bounded diagnostics with known keys redacted.
- **File Artifacts** — downloads constrained to the validated output directory.
- **Usage** — reported token and cost totals.

The Page Viewer & Editor becomes useful before the whole pipeline finishes:

- when only Stage 1 exists for a page, it shows the source and Stage 1
  transcription;
- when Stage 2 finishes that page, it shows both Stage 1 and Stage 2 MDF;
- newly completed pages become available automatically while a run is active;
- only processed pages appear in the slider.

Saving changes replaces the corresponding Stage 1 and/or Stage 2 text file in
the run's configured output directory. A Stage 1 correction does **not**
regenerate an existing Stage 2 file, so correct Stage 2 separately or rerun MDF
conversion when consistency matters. Avoid editing the page currently being
written by the worker because its output may replace your change.

## Run history

Run history can be searched by run ID and filtered by status or provider. Each
inactive run has a **Remove** action directly in the list, and **Delete all
history** removes all inactive dashboard records and their managed inputs.
These actions do not delete generated files from the configured output
directory. Active runs must first finish or be cancelled.

Runs and events survive restart. A run active when the app stopped becomes
interrupted and must be resumed explicitly. Presets own independent copies of
their managed inputs, so deleting or cleaning a source run does not break a
saved preset.

## Credentials and local data

The **API credentials** section on **New Run** accepts Gemini, OpenAI,
Anthropic, and OpenRouter keys. Click **Save key** to persist an entered value.
Inputs are masked by default and the eye button explicitly reveals a saved
value. Provider keys are encrypted before their ciphertext is written to
SQLite. They never enter presets, resolved configuration, logs, command lines,
or URLs.

MUDIDI stores the encryption key separately at `.credential-key` in the same
private data directory. This protects a copied database from exposing plaintext
credentials, but anyone who can read both files as your local user can decrypt
them. Keep the complete directory private. The dashboard does not fall back to
`.env`; `.env` remains the credential mechanism for CLI and YAML workflows.

When MUDIDI uses LiteLLM directly, there is no separate LiteLLM API key. The
model identifier selects a provider and LiteLLM uses that provider's key—for
example, an OpenAI model uses the saved OpenAI key. A LiteLLM virtual or master
key is relevant only when connecting to a separately hosted LiteLLM Proxy.

Web data defaults to:

```text
~/.local/share/mudidi/
├── mudidi-web.sqlite3   # run history, presets, encrypted key ciphertext
├── .credential-key     # local encryption key; keep private
├── presets/             # preset-owned managed inputs
└── runs/                # run-owned inputs and worker artifacts
```

Override the complete data directory with:

```bash
uv run mudidi web --data-dir path/to/private-app-data
```

Generated dictionary files remain in the selected output directory. In Docker,
paths under `/data` are persisted in the host's `mudidi-data/` directory. With
`uv`, output paths refer directly to the host filesystem. The first release
permits one inference worker at a time.

## Troubleshooting

- **API credential required** — save the matching key under **API credentials**
  on **New Run**.
- **Another inference worker is active** — finish or cancel the current worker.
- **Awaiting MDF Parsing Guide Review** — review and explicitly approve the
  guide; this pause is intentional.
- **Interrupted** — inspect the run and explicitly resume it.
- **Request body too large** — compress the dictionary PDF or create a smaller
  PDF containing the pages needed for the run, then upload that single PDF.
- **`pdftk is not available on PATH` / `extraction returned 1`** — a native
  `uv` run cannot split a multi-page PDF until `pdftk-java` is installed. Use
  `brew install pdftk-java` on macOS or `sudo apt install -y pdftk-java` on
  Ubuntu/WSL2, then restart the dashboard and create a new run.
- **Address already in use on `127.0.0.1:8000`** — another dashboard process is
  already listening. On macOS or Linux, inspect it with
  `lsof -nP -iTCP:8000 -sTCP:LISTEN`, stop the listed process with `kill PID`,
  or start MUDIDI on another port with `uv run mudidi web --port 8080`.
- **Docker cannot connect to the daemon** — start Docker Desktop or the Docker
  service and wait for `docker info` to succeed.

For advanced options omitted from the dashboard, use the
[YAML configuration guide](../getting-started/configuration.md) and
[CLI reference](../reference/cli.md).
