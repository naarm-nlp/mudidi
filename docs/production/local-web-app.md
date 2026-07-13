# Local web application

MUDIDI includes a localhost dashboard for production inference without YAML or
CLI flags. The pipeline and files stay on the computer running MUDIDI; only
model requests are sent to the selected provider.

## Install and launch

```bash
uv sync --extra web
uv run mudidi web
```

Save the API key for your model provider on the **API providers** screen.
MUDIDI opens `http://127.0.0.1:8765`. It binds to loopback and is not intended
for public or LAN deployment. Use `--no-browser` or `--port` when needed.

## Create a run

The **New Run** screen asks you to:

1. Choose a source PDF, page images, or a folder of page images.
2. Enter an output directory on the same computer running MUDIDI.
3. Select one pipeline:
   - **Complete digitization**
   - **Transcription only**
   - **Parse transcription into MDF (Multi-Dictionary Formatter)**
4. Choose a provider and one model for each active stage.
5. Optionally enable **Agentic verification**.
6. Review the resolved configuration and start.

Browser-selected inputs are copied into an input bundle owned by the run. This
allows review, restart, and resume without depending on the original browser
selection. The output directory remains a text field because a standard browser
cannot disclose an arbitrary absolute folder path to a localhost server.

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
source text, and MUDIDI still checks the scanned page.

## Additional context

The dashboard can attach:

- PDF dictionary and introduction page numbers using Arabic numbers, commas,
  and ranges such as `1-12,15`;
- a character inventory entered directly as text;
- Stage 1 and Stage 2 additional instructions entered directly as text;
- representative MDF parsing guide pages;
- an existing MDF parsing guide JSON file.

Roman numeral page specifications are not accepted. When the dictionary source
is one PDF, use its numeric introduction-page field.

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

## MDF parsing guide review checkpoint

Complete and MDF-parsing runs pause when Stage 2 **infers** an MDF parsing guide.
Open **MDF parsing guide**, review its markers and guide rules, make any edits,
and select **Approve and continue MDF parsing**.

Saving a draft is not approval. Page parsing cannot start without a server-minted
approval bound to the exact run, review version, immutable snapshot, digest, and
approval time.

A user-uploaded existing MDF parsing guide follows a different path: MUDIDI
copies it into the run-owned input bundle, validates its JSON and marker format,
and uses it directly without Pass 1 discovery or a human checkpoint. The guide
is validated again when Stage 2 loads it. Uploading a guide therefore means the
user is supplying the intended parsing rules, while malformed files still fail
safely before MDF parsing.

## Monitor and inspect

Run views include:

- **Overview** — durable status, progress, resume, and cancellation.
- **MDF parsing guide** — structured Stage 2 guide review and approval.
- **Output Preview** — bounded Stage 1 and Stage 2 previews.
- **Live Logs** — bounded diagnostics with known keys redacted.
- **File Artifacts** — downloads constrained to the validated output directory.
- **Usage** — reported token and cost totals.

Runs and events survive restart. A run active when the app stopped becomes
interrupted and must be resumed explicitly. Presets own independent copies of
their managed inputs, so deleting or cleaning a source run does not break a
saved preset.

## Credentials and local data

The **API providers** screen accepts Gemini, OpenAI, Anthropic, and OpenRouter
keys. The inputs are masked by default and the eye button explicitly reveals a
saved value. Provider keys are encrypted before their ciphertext is written to
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

Generated dictionary files remain in the selected output directory. The first
release permits one inference worker at a time.

## Troubleshooting

- **API credential required** — save the matching key under **API providers**.
- **Another inference worker is active** — finish or cancel the current worker.
- **Awaiting MDF Parsing Guide Review** — review and explicitly approve the
  guide; this pause is intentional.
- **Interrupted** — inspect the run and explicitly resume it.
- **Request body too large** — select a smaller input set or split the source
  before creating the run.

For advanced options omitted from the dashboard, use the
[YAML configuration guide](../getting-started/configuration.md) and
[CLI reference](../reference/cli.md).
