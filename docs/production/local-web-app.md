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
prints `MUDIDI dashboard: http://localhost:8000/`, open that address. The
Compose port is published only on
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

Native runs use PyMuPDF for PDF page extraction; no separate PDF system tool is
required.

Then install the web dependencies and start MUDIDI:

```bash
uv sync --frozen --extra web
uv run mudidi web
```

The default request limit is 110 MiB and the default cumulative managed-upload
limit is 100 MiB. To override these defaults, configure both limits and leave
request headroom for multipart framing:

```bash
uv run mudidi web \
  --max-request-bytes 115343360 \
  --max-upload-bytes 104857600
```

`--max-request-bytes` applies to the complete raw HTTP request. The request
limit must be greater than `--max-upload-bytes`.

Save the API key for your model provider under **API credentials** on the
**New Run** screen by clicking **Save key** beside that provider.
MUDIDI opens `http://localhost:8000`. It binds to loopback and is not intended
for public or LAN deployment. Use `--no-browser` or `--port` when needed.


## Create a run

The **New Run** screen is a five-step wizard:

1. **Input** — upload exactly one dictionary PDF, enter the output directory and
   dictionary pages, and optionally add introduction pages, additional context,
   MDF inputs, and a **Dictionary Profile**.
2. **Pipeline** — choose one of the three supported workflows and an existing
   output policy.
3. **Model** — manage the local provider credential, select the provider,
   model, and reasoning settings, and configure temperature and batch size.
4. **Agentic** — leave verification off or enable its evaluator and correction
   settings.
5. **Review** — submit the complete form for authoritative server validation,
   then inspect the server-rendered, non-secret review before starting the run.

The client validates only the visible, enabled controls in the current wizard
step. **Continue** checks that current panel, focuses the first invalid control,
and shows field explanations and a summary when several controls need attention.
**Back** does not validate or clear values. At final **Review run**, the browser
performs whole-form constraint validation across all enabled fields before
sending the complete multipart form to `/runs/preview`. Browser checks are only
an early convenience: the server remains authoritative for required fields,
PDF page bounds, profile completeness, model settings, and the rest of the
production configuration.
A rejected submission returns to **New Run** with safe, user-facing validation
details. Only when the server associates an error with a rendered field does
the wizard open that field's step and show a field-specific explanation; other
failures still return to **New Run** without field-specific focus.

The **Input** step asks for:

1. one dictionary PDF;
2. the required **PDF dictionary pages** to process. You may enter one page
   (`5`), one range (`10-20`), comma-separated pages (`1,5,9`), or a
   combination (`1,5,10-20`);
3. an output directory on the same computer running MUDIDI;
4. optional introduction pages and additional context;
5. optional MDF parsing-guide pages, an existing guide JSON file, or a custom
   MDF manual PDF.

Page numbers are 1-based: zero, negative numbers, descending ranges, and pages
beyond the uploaded PDF's page count are rejected. Browser-selected files are
copied into an input bundle owned by the run so review, restart, and resume do
not depend on the original browser selection. The output directory remains a
text field because a standard browser cannot disclose an arbitrary absolute
folder path to a localhost server.

The **Pipeline** step presents these mutually exclusive choices:

- **Complete digitization** — transcribes the dictionary, infers and reviews an
  MDF parsing guide, then parses the transcription into MDF;
- **Transcription only** — produces faithful flat Stage 1 text without MDF
  parsing;
- **Parse transcription into MDF (Multi-Dictionary Formatter)** — uses an
  existing transcription, infers and reviews a dictionary-specific guide, and
  emits MDF records.

The existing output policy is separate: **Resume compatible existing
artifacts** reuses compatible work, while **Overwrite existing artifacts**
replaces it. The selected pipeline determines which later inputs and model
controls are active. The dashboard always uses flat Stage 1 output and does not
preserve typography. OCR hints, column mode, and expert OCR/VLM backends remain
available through YAML and the CLI but are intentionally absent from the
dashboard.

The final **Review** page is rendered by the server, not another client-side
wizard panel. It groups the validated non-secret values under **Input**,
**Pipeline**, **Model**, and **Agentic**, reports MDF parsing-guide review
requirements, and provides the start action. Complete and MDF-parsing runs
pause later for explicit MDF parsing-guide approval; that human checkpoint is
distinct from this pre-run configuration review.

The grey values beginning with `ex:` are examples only; they are not submitted
as values. The current examples are `ex: 30-35` for dictionary pages,
`ex: 1-5` for introduction pages, and `ex: 30-32` for representative MDF
parsing-guide pages.

The web dashboard does not accept page images, multiple files, or a folder of
images. Those input modes remain available through YAML and the CLI.

## Dictionary Profile

The optional **Dictionary Profile** can improve extraction accuracy. It asks
for:

- headword language and script;
- one or more paired translation, gloss, or definition languages and scripts;
- a free-form description of the page arrangement;
- the information types found in entries.

Leave the whole section blank when you are unsure. If you answer any profile
question, complete the profile, including a matching script for every target
language; the server rejects partial profiles. The profile is guidance, not
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

Agentic verification is the fourth wizard step and defaults to **Off** because
it adds evaluator and correction-model calls and cost. Select **On** to reveal
the **Custom verification** panel. The applicable Stage 1 and Stage 2 checks
start enabled; you can disable either one, then configure maximum correction
iterations, minimum retry confidence, evaluator and rewriter models, reasoning,
deterministic patches, and concrete retry evidence.

Only stages in the selected pipeline can be verified. Controls for inactive
stages are disabled in the browser, and the server intersects submitted stage
choices with the active pipeline rather than trusting a forged inactive value.
The Stage 1 and Stage 2 production calls still use the one synchronized
run-level provider described below; evaluator and rewriter settings retain
their existing optional role-specific model controls.

## Models and providers

The **Model** step begins with the selected provider's credential card, then
shows model and reasoning controls for active stages, temperature, and batch
size. There is one authoritative run-level `provider` form value. The Stage 1
and Stage 2 provider selectors are synchronized presentations of that value,
not independent per-stage providers: changing either selector updates the
other, and the same single provider is submitted for the run. Existing
multipart field names, including `provider`, `stage1_model`,
`stage2_pass1_model`, `stage2_pass2_model`, and their reasoning fields, remain
unchanged; the wizard changes visibility and presentation, not their meaning.

Stage 1 exposes its model and reasoning when transcription is active. Stage 2
uses one shared model and one shared reasoning selection by default. The
shared values are written to both existing Pass 1 and Pass 2 controls, so both
Stage 2 calls use the same choices. The summary identifies this as **Shared
model**.

For separate Stage 2 choices, select **Advanced · split passes**. The action
copies the current shared model and reasoning into both pass cards and replaces
the shared presentation with:

- **Pass 1** — examines representative pages to infer the dictionary-specific
  MDF markers and entry structure. Its generated guide is reviewed before
  conversion;
- **Pass 2** — applies the approved Pass 1 guide to each authoritative Stage 1
  transcription and emits the per-page MDF records. This is the high-volume
  conversion pass.

Each split-pass card has its own model and reasoning controls, while the
run-level provider remains shared. The summary changes to **Separate pass
models** and shows both models. **Use one Stage 2 model** returns to shared mode
and reuses the previous shared selections. Independently cached split-pass values
survive toggling only within the current page instance; because shared values
replace both pass controls before session storage persists them, a reload or
navigation restores those shared replacement values rather than the independent
split-pass choices.

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
- **Live Logs** — bounded diagnostics with known keys redacted, including
  PDF-splitting progress.
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

On the **Model** step, the selected run-level provider appears in a prominent
credential card with its saved status, a masked input, a reveal button, and
**Save key**. The other provider cards are inside the **Manage keys**
disclosure. Selecting a different provider moves that provider's card into the
selected position without duplicating the card or its field.

The **API credentials** section accepts Gemini, OpenAI, Anthropic, and OpenRouter
keys. Click **Save key** to persist an entered value. Inputs are masked by
default and the eye button explicitly reveals a saved value. The existing
credential contract is unchanged: the key is sent as the `api_key` field to
`POST /credentials/{provider}`, a saved key is revealed only after the
same-origin `POST /credentials/{provider}/reveal` action, and removal uses
`POST /credentials/{provider}/delete`. These actions do not put keys into run
form fields, presets, resolved configuration, logs, command lines, or URLs.

Provider keys are encrypted before their ciphertext is written to SQLite.
MUDIDI stores the encryption key separately at `.credential-key` in the same
private data directory. This protects a copied database from exposing plaintext
credentials, but anyone who can read both files as your local user can decrypt
them. Keep the complete directory private. The dashboard does not fall back to
`.env`; `.env` remains the credential mechanism for CLI and YAML workflows.
The encrypted storage, field names, endpoint paths, and reveal semantics are
unchanged by the wizard presentation.

When MUDIDI uses LiteLLM directly, there is no separate LiteLLM API key. The
model identifier selects a provider and LiteLLM uses that provider's key—for
example, an OpenAI model uses the saved OpenAI key. A LiteLLM virtual or master
key is relevant only when connecting to a separately hosted LiteLLM Proxy.

The browser uses the existing session-storage behavior for wizard continuity:
non-file, non-password form values persist only in the current browser tab.
File inputs and password/key values are intentionally excluded, so session
storage does not restore them and does not carry them across tabs or browser
restarts. Wizard presentation state, including the shared/split Stage 2
choice, is kept separately from the form values.

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
an output such as `outputs/web-output` is written to the repository's host
directory at `outputs/web-output`; Compose mounts `./outputs` at
`/app/outputs`. Paths under `/data` are persisted in the host's `mudidi-data/`
directory. Other absolute host paths are rejected because Docker cannot access
an unmounted host directory. With `uv`, output paths refer directly to the host
filesystem. The first release permits one inference worker at a time.

## Troubleshooting

- **API credential required** — save the matching key under **API credentials**
  on **New Run**.
- **Another inference worker is active** — finish or cancel the current worker.
- **Awaiting MDF Parsing Guide Review** — review and explicitly approve the
  guide; this pause is intentional.
- **Interrupted** — inspect the run and explicitly resume it.
- **Request body too large** — start the dashboard with larger
  `--max-request-bytes` and `--max-upload-bytes` values, leaving request
  headroom for multipart framing.
- **Address already in use on `127.0.0.1:8000`** — another dashboard process is
  already listening. On macOS or Linux, inspect it with
  `lsof -nP -iTCP:8000 -sTCP:LISTEN`, stop the listed process with `kill PID`,
  or start MUDIDI on another port with `uv run mudidi web --port 8080`.
- **Docker cannot connect to the daemon** — start Docker Desktop or the Docker
  service and wait for `docker info` to succeed.

For advanced options omitted from the dashboard, use the
[YAML configuration guide](../getting-started/configuration.md) and
[CLI reference](../reference/cli.md).
