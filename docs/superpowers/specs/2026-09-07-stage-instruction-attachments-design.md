# Stage Instruction Attachments Design

**Date:** 2026-09-07  
**Status:** Awaiting written-spec review
**Branch:** `features/brutalist-dashboard-reskin`

## Decision summary

The New Run wizard will retain the existing Stage 1 and Stage 2 custom-instruction textareas and add a mutually exclusive single-file upload mode for each stage. A user may either type instructions or upload one `.txt`, `.md`, or `.pdf` file; the browser and server must never combine both sources for the same stage.

Stage 2 instructions gain an explicit application scope:

1. Pass 1 only — MDF parsing-guide discovery.
2. Pass 2 only — per-page MDF extraction.
3. Both passes — the default and the backward-compatible behavior.

TXT and Markdown uploads become textual prompt instructions. PDF uploads remain document or visual context so scanned pages, diagrams, typography, and embedded images are preserved. A blank PDF page selection means all pages; users may optionally narrow the attachment with the same page/range syntax used elsewhere in the dashboard.

## Goals

- Let users supply substantial or visually structured instructions without pasting them into a textarea.
- Preserve quick typed instructions for small corrections and one-off runs.
- Guarantee exactly one instruction source per stage.
- Let Stage 2 users target field discovery, MDF extraction, or both without leaking instructions into the unselected pass.
- Preserve PDF page imagery and layout across supported providers.
- Keep uploaded material local, validated, run-owned, reviewable, and reproducible through presets.
- Ensure agentic evaluators and rewriters see the same selected instructions as the generation call they evaluate.
- Make repeated multimodal-context cost visible before a run starts.

## Non-goals

- Multiple uploaded instruction files per stage.
- Standalone PNG, JPEG, WebP, or other image uploads. Images must be contained in the uploaded PDF.
- New DOCX uploads in the dashboard. Existing CLI support for text extraction from DOCX remains unchanged.
- OCR-only conversion of uploaded PDFs.
- Remote attachment URLs or provider-hosted file identifiers entered by users.
- Sharing one uploaded file implicitly between Stage 1 and Stage 2.
- Changing the MDF manual, introduction-pages, dictionary-PDF, or parsing-guide upload flows.
- Displaying instruction contents on the Review page.

## Terminology

- **Typed instructions:** text entered in the existing stage textarea.
- **Instruction file:** one uploaded TXT, Markdown, or PDF file for a stage.
- **Instruction source mode:** `typed` or `file`.
- **PDF page selection:** optional 1-based page specification scoped to the instruction PDF; blank means all pages.
- **Stage 2 scope:** `pass1`, `pass2`, or `both`.
- **Visual instruction context:** selected PDF pages included as document or image content parts in an LLM request.

## User experience

### Stage 1

The existing Stage 1 instructions section gains a two-choice source control:

- **Type instructions** — selected by default; shows the current textarea.
- **Upload instruction file** — shows one file input accepting `.txt,.md,.pdf`.

Only the active source control is enabled and submitted. Switching modes clears the inactive value after a confirmation when that value is non-empty. Typed mode may remain blank, meaning no Stage 1 custom instructions. File mode requires exactly one non-empty upload.

When the uploaded file is a PDF, the panel reveals an optional **PDF pages** field. The placeholder and help text use the established syntax: `2-4,8`. Blank means every page in the PDF. The panel shows the resolved page count after server validation rather than attempting to parse the PDF in browser JavaScript.

### Stage 2

Stage 2 uses the same mutually exclusive source control and accepted types. Beneath the active instruction source it shows **Apply to** cards:

- **Pass 1 only — parsing-guide discovery**
- **Pass 2 only — per-page MDF extraction**
- **Both passes**

Both passes is the default. The scope is enabled only when the selected pipeline runs Stage 2. Scope remains visible when typed mode is selected but blank, so a user can configure it before entering instructions.

### Cost and context warning

PDF mode displays a persistent warning:

> Selected PDF pages are attached to every applicable model call. Large files or “all pages” can substantially increase token use, cost, latency, and the chance of exceeding a model context limit.

The Review page reports the resolved number of selected PDF pages and the applicable stage/pass calls. It does not estimate provider-specific token cost because image accounting varies by model and provider.

### Review page

The Review page adds non-secret instruction metadata:

- Stage
- Source: typed, TXT, Markdown, or PDF
- Original filename for file mode
- Resolved PDF pages and page count for PDF mode
- Stage 2 scope

Typed instruction text and uploaded file contents are not rendered. Existing “Additional Instructions” summary copy is replaced by this structured metadata.

### Presets

Saving a preset copies the selected instruction file into the preset-owned bundle and persists:

- Instruction source mode
- Managed instruction path
- Original filename and content digest
- PDF page specification
- Stage Stage 2 scope

Loading a preset restores the source choice, filename display, page specification, and Stage 2 scope. Browser file inputs cannot be populated programmatically, so the loaded preset is represented as an existing managed attachment with **Keep** and **Replace** actions rather than a synthetic file-input value.

## Form and validation contract

The multipart New Run form adds these controls:

- `stage1_instruction_source`: `typed | file`
- `stage1_instruction_file`: optional upload
- `stage1_instruction_pdf_pages`: optional page specification
- `stage2_instruction_source`: `typed | file`
- `stage2_instruction_file`: optional upload
- `stage2_instruction_pdf_pages`: optional page specification
- `stage2_instruction_scope`: `pass1 | pass2 | both`

The existing textarea names remain `stage1_additional_instructions` and `stage2_additional_instructions`.

Server validation is authoritative:

- Typed mode rejects a non-empty uploaded file.
- File mode requires exactly one upload and rejects non-blank textarea content.
- TXT and Markdown must be valid UTF-8 after upload.
- Extracted textual instructions retain the existing 20,000-character limit.
- PDF files must have a valid PDF signature, be readable by PyMuPDF, and contain at least one page.
- A non-PDF upload rejects a PDF page specification.
- PDF page numbers are 1-based, unique after normalization, and must exist in the uploaded PDF.
- Blank PDF pages resolve to every page.
- Stage 2 instruction values are rejected when the selected pipeline does not run Stage 2; the default `both` scope remains inactive and is not submitted.
- Existing safe-filename, path-containment, content-signature, and aggregate 100 MB upload rules remain mandatory.
- Validation errors return to the Input wizard step with the selected mode and safe non-file values preserved.

## Persisted configuration

Typed instructions continue to be materialized as run-owned UTF-8 text files. Uploaded files are copied into stage-specific run-owned instruction directories using validated basenames. Runtime configuration therefore references only managed local paths, never browser-provided paths.

The extraction configuration retains the existing `pipeline.stage1_guides` and `pipeline.stage2_guides` paths. Those paths may now refer to TXT, Markdown, DOCX from existing CLI flows, or PDF. New fields record PDF selection and Stage 2 routing:

- `pipeline.stage1_guides_pages`
- `pipeline.stage2_guides_pages`
- `pipeline.stage2_guides_scope`, default `both`

The default preserves current YAML and CLI behavior. Existing text-only configurations require no migration.

A run-owned instruction metadata manifest records source mode, original filename, normalized suffix, byte count, SHA-256 digest, PDF page count, selected pages, and Stage 2 scope. This manifest supports Review rendering, preset rebasing, auditability, and artifact diagnosis without reading file contents into templates.

## Materialization and preprocessing

### Text files

TXT and Markdown uploads are decoded once as UTF-8. The loaded text is appended beneath the existing `USER DEFINED GUIDELINES` heading. The prompt labels the source filename but treats the contents as untrusted user instructions rather than system policy.

### PDF files

PDF selection is resolved once during run preparation:

1. Validate the document and page specification.
2. Materialize a selected-pages PDF when the selection is narrower than the whole document.
3. Preserve the original run-owned upload and record the selected artifact digest.
4. At execution time, use the selected-pages PDF directly when the model supports document input.
5. Otherwise rasterize the selected pages once into run-owned images and reuse them across calls.

The runtime must not read or encode the same local PDF independently for every page worker. Immutable selected-page artifacts and rasterized images are prepared once, then reused.

## Prompt integration

Every attachment is preceded by explicit text identifying it as reference instructions, not as the dictionary page to transcribe or parse. Prompt order remains deterministic.

### Stage 1

- Typed/TXT/Markdown instructions are included in the Stage 1 user instruction text.
- PDF pages are attached to every Stage 1 transcription call.
- The dictionary page remains explicitly labeled as the transcription target and is placed after instruction references.
- If Stage 1 agentic verification is active, evaluator and rewriter calls receive the same textual instructions and PDF content used by generation.

### Stage 2 Pass 1

When scope is `pass1` or `both`:

- Text instructions are included in parsing-guide discovery.
- PDF pages are attached to the discovery request together with the representative dictionary pages.
- Discovery prompt text distinguishes instruction references from dictionary samples.

When scope is `pass2`, neither text nor PDF instruction content reaches Pass 1.

### Stage 2 Pass 2

When scope is `pass2` or `both`:

- Text instructions are included in every per-page MDF extraction call.
- PDF pages are attached as reference context.
- The approved parsing guide, source transcription, and dictionary page retain their existing authority and labels.
- Active Stage 2 agentic evaluator and rewriter calls receive the same selected instructions.

When scope is `pass1`, neither text nor PDF instruction content reaches Pass 2 or its agentic calls.

## Provider and model behavior

The implementation reuses the existing document-content and PDF-rasterization paths rather than introducing provider-specific request construction.

- Direct-document models receive one PDF content part.
- Models requiring rasterization receive ordered image content parts plus a page-order note.
- Unsupported or oversized requests fail with a precise validation/execution error; MUDIDI does not silently discard instruction pages.
- Split Stage 2 models resolve PDF capability independently for Pass 1 and Pass 2.
- Provider credentials and attachment contents remain excluded from logs and review templates.

## Manifest and usage behavior

Run manifests describe textual and PDF instructions with JSON-serializable paths, source type, selected pages, and digest. The earlier guide-path serialization invariant remains enforced.

Usage records remain provider-reported. The Usage page does not fabricate attachment token estimates. Repeated attachment cost appears naturally in per-call provider usage and is aggregated by the existing multi-phase accounting logic.

## Failure and resume behavior

- A failure while validating or materializing instructions prevents run validation; no worker starts.
- A provider rejection caused by attachment type, size, or context length marks the run failed with the provider error and stage/pass identity.
- Resume reuses immutable managed attachments and preprocessed PDF artifacts when their digests and page selections match.
- Replacing a preset attachment or changing its PDF pages changes the resolved configuration and requires overwrite when targeting an existing output slot.
- Cancellation and run-record deletion retain their existing semantics for run-owned attachment bundles.

## Accessibility and responsive behavior

- Source choices use a semantic radio group.
- File inputs retain visible labels and accepted-type descriptions.
- PDF page help is associated through existing accessible help controls.
- Stage 2 scope cards expose the full pass names, not only “Pass 1” and “Pass 2”.
- Switching source modes moves focus to the newly active control.
- Hidden controls are disabled and removed from keyboard navigation.
- Attachment metadata and validation errors wrap without document-level overflow at mobile widths.

## Testing strategy

### Form and configuration

- Typed/file mutual exclusion for both stages.
- Empty file-mode rejection and inactive-control exclusion.
- Stage 2 scope defaults and pipeline compatibility.
- Preview and Review metadata for every source and scope.
- Preset save, load, keep, replace, and managed-path rebasing.

### Upload safety

- TXT/Markdown UTF-8 validation and 20,000-character limit.
- PDF signature, readability, page-count, and page-range validation.
- Filename traversal, symlink, empty upload, and aggregate byte-limit rejection.
- Uploaded content is stored only beneath the run or preset bundle.

### Prompt routing

- Stage 1 text and PDF reach generation, evaluator, and rewriter.
- Stage 2 `pass1` reaches only discovery.
- Stage 2 `pass2` reaches only extraction and its agentic calls.
- Stage 2 `both` reaches both passes without duplicate content parts.
- Split Stage 2 models independently choose direct PDF versus rasterized images.
- Dictionary target pages and instruction reference pages retain unambiguous ordering and labels.

### Resume and manifests

- Selected-page preprocessing is reused when digest and page selection match.
- Changed attachment or selection invalidates incompatible resume artifacts.
- Run manifests remain JSON serializable for every accepted instruction type.
- Instruction contents and credential values do not appear in Review metadata.

### Surface verification

- Browser-drive typed/upload switching, PDF-page disclosure, validation errors, Stage 2 scope, Review metadata, preset restoration, and responsive behavior.
- Run one Stage 1 PDF-instruction smoke and one Stage 2 split-scope smoke through the actual worker with provider calls stubbed at the LLM boundary.
- Run the complete dashboard regression suite after behavioral smoke verification.

## Acceptance criteria

Implementation is complete only when:

1. Each stage accepts either typed instructions or one TXT, Markdown, or PDF file, never both.
2. Blank PDF page selection attaches all pages; valid explicit selections attach only those pages.
3. Stage 2 instructions obey Pass 1, Pass 2, or Both routing without cross-pass leakage.
4. Selected instructions reach corresponding agentic evaluator and rewriter calls.
5. Direct-PDF and rasterized-image model paths both work.
6. Review, presets, manifests, resume behavior, and usage accounting remain correct.
7. Unsafe, invalid, or unsupported uploads fail before worker execution.
8. Existing text-only YAML, CLI, dashboard, and preset behavior remains compatible.
9. Changed contracts have deterministic tests and the dashboard surface is browser-verified.
