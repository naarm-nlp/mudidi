# Local Web Application UX Specification

This document is the source of truth for user-visible dashboard behavior.
Internal database/config keys such as `parse_rules` and routes such as
`/parse-rules` remain unchanged. The canonical generated filename is
`mdf_parsing_guide.json`.

## Application shell

The desktop layout uses a fixed dark left navigation, page header, central
work area, and right summary rail. The header holds a billing status chip that
follows the selected billing mode and, when at least one preset exists, the
saved-preset picker. The sidebar footer holds the light/dark theme toggle. The
wizard has six steps, shown as a numbered tab bar: Input, Pipeline, Context,
Model, Agentic, and the server-rendered Review. This is the shipped New Run
interface:

![MUDIDI New Run desktop dashboard](../../assets/dashboard-home.png)

At narrow widths the navigation moves above the content and the step tabs
show only their numbers, without horizontal page overflow:

![MUDIDI New Run mobile dashboard](../../assets/dashboard-mobile.png)

The original planning wireframe remains available at
[`assets/new-run-wireframe.png`](assets/new-run-wireframe.png).

## Input

The browser selects exactly one dictionary PDF. The dashboard rejects page
images, multiple source files, and page-image folders; those input modes remain
available through YAML and the CLI. The Input step holds only the PDF, the
dictionary and introduction pages, and the output directory. Uploaded
files are copied into run-owned local storage. The output directory is a text
field because browser file APIs do not provide an arbitrary absolute path to a
localhost server. Native (non-container) runs on a machine with a supported
dialog add **Choose folder…**, which asks the server to open the operating
system's folder dialog and writes the selected path into the field.

The dictionary PDF and **PDF dictionary pages** are required. Page fields accept
positive, 1-based Arabic page numbers in any of these forms: one number (`5`),
one ascending range (`10-20`), comma-separated numbers (`1,5,9`), or a
combination (`1,5,10-20`). Roman numerals, zero, negative values, descending
ranges, and pages beyond the uploaded PDF's page count are rejected.

Introduction pages and representative MDF parsing-guide pages are optional and
use the same grammar and PDF bounds. Representative pages must also be included
in the dictionary-page selection. Placeholder examples use faded text with an
`ex:` prefix and show every accepted form (`ex: 30-35 or 30, 32, 35 or 32 or
30, 33-35`) so they cannot be mistaken for submitted values.

The browser marks required controls, but the server remains authoritative. A
missing required value or invalid page specification blocks review, returns the
user to the New Run form, opens the affected section, and marks the field in red
with an associated text explanation.

## Context

Context follows Pipeline so it can hide inputs the chosen pipeline does not
use. Everything on this step is optional. Sections appear in this order:

1. **Dictionary profile** — headword language and script, target languages and
   scripts, the Stage 1 **character inventory** (hidden without Stage 1), a
   free-form page-layout description, and entry information types. The whole
   profile may be left blank.
2. **MDF parsing guide** — a choice between **Create a guide for me**
   (optional pages to learn from; blank uses every page in the dictionary-page selection, not the whole PDF) and
   **Use a guide I already have** (a guide JSON upload). Only the selected
   choice's field is shown (Stage 2 pipelines only).
3. **Advanced: stage instructions** — a toggle at the bottom of the step that
   starts off and reveals the Stage 1 and Stage 2 instruction controls.

Collapsing the advanced toggle hides the instruction controls without
disabling or clearing them, so any entered instructions are still submitted.
While collapsed, a badge shows how many are set and the run summary lists the
stages that have instructions. The toggle opens automatically on load when
instructions are present (preset or restored session) or a server error
targets an instruction field, and when browser validation flags a hidden
instruction field.

## Pipeline

Three radio choices are displayed with keyboard/touch-accessible information
help:

| Choice | Internal stage | Meaning |
|---|---|---|
| Complete digitization | `all` | Transcribe, infer/review MDF parsing guide, parse into MDF |
| Transcription only | `1` | Flat faithful transcription only |
| Parse into MDF | `2` | Existing Stage 1 text to reviewed MDF |

Discovery-only and direct Pass 2 are not dashboard choices. Stage 2 always
includes inference or import of an MDF parsing guide and mandatory review.

Dashboard runs always use the two-stage LLM strategy, flat Stage 1 output, and
typography preservation off. OCR hints, Stage 1 column mode, and expert
OCR/VLM/Mathpix controls remain CLI/YAML features.

## Model

Only active-stage model controls are visible. Complete runs show Stage 1, Stage
2 Pass 1, and Stage 2 Pass 2. Transcription shows Stage 1. MDF parsing shows the
two Stage 2 models.

Provider-specific choices combine the bundled catalog, optional live discovery,
and custom LiteLLM identifiers. OpenRouter requires manual model entry and
offers an optional **OpenRouter Provider** routing slug.

## Agentic loop

Agentic loop is an On/Off choice and defaults to Off. Selecting On
opens **Custom loop settings** directly below it. Applicable Stage 1 and
Stage 2 boxes
are initially checked and may be unchecked. The backend intersects these values
with active stages and ignores forged inactive values.

Custom controls cover iterations, minimum confidence, evaluator/rewriter models
and reasoning, deterministic patches, and concrete retry evidence. The UI states
what the loop does and that it adds model calls, time, and cost.

## Additional instructions

Each active stage presents one instruction source at a time, inside the
Context step's advanced toggle:

1. **Type instructions** uses a bounded multiline UTF-8 text value.
2. **Upload instruction file** accepts exactly one `.txt`, `.md`, or `.pdf`
   attachment.

TXT and Markdown attachments must decode as UTF-8, contain non-blank text, and
stay within the same 20,000-character limit as typed instructions. PDF
attachments remain document or visual context rather than being flattened to
plain text. Their optional page selector uses the dashboard's positive
1-based page/range grammar; blank means every attachment page.

Stage 2 adds a Pass 1, Pass 2, or Both-passes scope. Both is the default.
Changing sources after selecting a file requires confirmation because the
browser cannot retain that file after it is cleared. Review and preset screens
display attachment metadata, while the worker supplies the resolved content to
the selected stage and agentic evaluator/rewriter paths.

## MDF parsing guide and MDF manual

**MDF parsing guide** is the dictionary-specific artifact inferred by Stage 2
Pass 1 or imported from user JSON. **MDF manual** is bundled general reference
material.

The dashboard exposes no MDF manual control. Every run whose pipeline includes
Stage 2 Pass 1 adds the bundled extracted SIL Toolbox MDF Reference Manual text
to the Pass 1 parsing-guide discovery system prompt, using approximately 30K
input tokens once per run (Pass 1 is a single request). Pass 2 never receives
the manual. The repository owner has confirmed redistribution permission for
the bundled text.

Representative MDF parsing guide pages include help explaining that Stage 2
samples them to infer dictionary-specific MDF markers and entry structure.

## Review

The pre-run view summarizes input, output, pipeline, models, Agentic state,
additional context, MDF manual use, and MDF parsing guide review requirement.
All required inputs, page syntax, and PDF page bounds are validated before a
durable run is created.

## Active Run

```text
Overview | MDF parsing guide | Page Viewer & Editor | Live Logs | File Artifacts | Usage
```

The same tab bar appears on every run page with the current view marked.
Unavailable views stay in place as disabled labels rather than being removed.
Overview shows progress, current state, recent events, and relevant actions.
Pages and page evidence show source, transcription, verification, and MDF output.

## MDF parsing guide review

The review screen progresses through waiting, inferring, review required, and
approved states. It edits the MDF parsing-guide fields: markers/descriptions,
guide rules, and abbreviations.

Saving is not approval. Approval validates the guide, writes an immutable
snapshot, binds it to the run and review version, records its SHA-256, and only
then authorizes MDF page parsing. Closing the browser or server never implies
approval. This checkpoint applies to LLM-inferred guides. A valid guide that the
user explicitly uploads is copied into managed storage and used directly after
format validation, without showing this review screen.

## Run History and presets

History exposes user-friendly MDF parsing guide status labels while preserving
internal stored status values. Presets copy managed inputs into preset-owned
storage. Preparing a preset clones those files into the new run so neither
depends on the other's lifecycle.

## Accessibility and responsive behavior

- semantic fieldsets, legends, and labels;
- visible focus and keyboard/touch-operable help;
- status conveyed through text rather than color alone;
- errors associated with fields without echoing sensitive values;
- responsive monitoring/review, with desktop recommended for large setup.
