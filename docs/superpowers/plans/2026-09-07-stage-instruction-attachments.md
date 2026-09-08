# Stage Instruction Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add complete, reproducible Stage 1 and Stage 2 instruction-file workflows to the dashboard and extraction runtime, including TXT/Markdown/PDF inputs, PDF page selection, Stage 2 pass routing, Review metadata, presets, and agentic parity.

**Architecture:** The existing `pipeline.stage1_guides` and `pipeline.stage2_guides` paths remain authoritative. New pipeline fields carry PDF page selection and Stage 2 scope. A reusable immutable instruction-context object loads text once or preprocesses selected PDF pages once, caches encoded direct-document and raster variants, and supplies model-specific content parts to generation, evaluator, and rewriter calls. The web layer owns safe multipart materialization and sidecar metadata; presets copy and rebase those managed files unchanged.

**Tech Stack:** Python 3.13, Pydantic, FastAPI/Starlette multipart forms, PyMuPDF, Pillow, LiteLLM message content parts, Jinja, vanilla JavaScript, CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-stage-instruction-attachments-design.md`

## Global Constraints

- Each stage accepts exactly one active instruction source: typed text or one uploaded file.
- Dashboard uploads accept only `.txt`, `.md`, and `.pdf`; existing CLI `.docx` guide support remains valid.
- Text instructions must decode as UTF-8 in dashboard uploads, be non-blank in file mode, and contain at most 20,000 characters.
- PDF files must have a `%PDF-` signature, open with PyMuPDF, and contain at least one page.
- PDF page specifications use 1-based positive numbers/ranges; blank means all pages; selected page numbers are normalized to a unique ordered list and must exist.
- `pipeline.stage2_guides_scope` is `pass1`, `pass2`, or `both`, defaulting to `both` for existing YAML and CLI configurations.
- Stage 2 instructions reach only the selected pass or passes; Stage 1 instructions reach every Stage 1 generation call.
- Active agentic evaluator and rewriter calls receive the same textual and PDF instructions as their corresponding generation call.
- PDF source bytes, selected-page artifacts, rendered pages, and encoded data URLs are prepared once per run, not once per dictionary-page worker.
- Direct-document models receive one PDF file part; models requiring rasterization receive ordered image parts plus an explicit page-order/reference notice.
- Instruction references are explicitly labeled as instructions and never as dictionary targets.
- Uploaded files remain inside run-owned bundles; config and manifests contain JSON-serializable managed paths and no credentials.
- Review pages expose metadata only, never instruction text or file contents.
- Presets preserve source mode, original filename, digest, PDF page selection, and Stage 2 scope; a saved file is kept when file mode is submitted without a replacement.
- Existing text-only YAML/CLI configurations require no migration.
- Aggregate browser upload limit remains 100 MiB.
- Use TDD for every changed contract and commit each task after its focused verification passes.

---

### Task 1: Configuration and immutable instruction context

**Files:**
- Create: `src/mudidi/instructions.py`
- Modify: `src/mudidi/config/yaml_config.py`
- Modify: `src/mudidi/utils/pdf_split.py`
- Test: `tests/config/test_instruction_guides_config.py`
- Test: `tests/llm/test_instruction_context.py`

**Interfaces:**
- Consumes: `parse_page_spec`, `render_pdf_pages`, `model_supports_pdf_input`, `image_data_url`, and `file_content_part` conventions.
- Produces: `InstructionMetadata`, `PreparedInstructionContext`, `prepare_instruction_context()`, `read_instruction_text()`, `resolve_instruction_pdf_pages()`, and the three new `PipelineConfig` fields used by all later tasks.

- [ ] **Step 1: Add failing configuration tests**

Add tests proving the old configuration remains valid and these fields round-trip through Pydantic and `redacted_config_dict()`:

```python
pipeline = PipelineConfig(
    stage1_guides=Path("stage1.pdf"),
    stage1_guides_pages="2-4,8",
    stage2_guides=Path("stage2.pdf"),
    stage2_guides_pages="1,3",
    stage2_guides_scope="pass1",
)
assert pipeline.stage1_guides_pages == "2-4,8"
assert pipeline.stage2_guides_pages == "1,3"
assert pipeline.stage2_guides_scope == "pass1"
assert PipelineConfig().stage2_guides_scope == "both"
```

Also assert a page spec without a PDF guide path, a page spec paired with TXT/MD/DOCX, descending ranges, and out-of-bounds PDF pages are rejected with the affected config field named. Repeated pages after expansion must normalize to the first occurrence rather than duplicate the PDF content.

- [ ] **Step 2: Run configuration tests and confirm failure**

Run: `uv run pytest tests/config/test_instruction_guides_config.py -q`

Expected: failures because the three pipeline fields and validation do not exist.

- [ ] **Step 3: Add the pipeline fields and path validation**

Extend `PipelineConfig` with:

```python
stage1_guides_pages: str | None = None
stage2_guides_pages: str | None = None
stage2_guides_scope: Literal["pass1", "pass2", "both"] = "both"
```

Normalize non-blank page specs to unique pages in first-occurrence order without changing range meaning. Extend `validate_config_paths()` so guide paths allow `.txt`, `.md`, `.docx`, and `.pdf`; guide page specs require PDF; PDFs are readable/non-empty and selected pages are within bounds; text files remain readable. Do not require either guide path.

- [ ] **Step 4: Add failing instruction-context tests**

Define tests for these exact public interfaces:

```python
@dataclass(frozen=True, slots=True)
class InstructionMetadata:
    source_path: Path | None
    kind: Literal["none", "text", "pdf"]
    original_filename: str | None
    byte_count: int
    sha256: str | None
    pdf_page_count: int | None
    selected_pages: tuple[int, ...]
    selected_path: Path | None
    selected_sha256: str | None

@dataclass(frozen=True, slots=True)
class PreparedInstructionContext:
    text: str
    metadata: InstructionMetadata
    pdf_data_url: str | None
    raster_data_urls: tuple[str, ...]
    def content_parts(self, model: str, *, stage_label: str) -> list[dict]: ...
    def manifest_entry(self, *, scope: str | None = None) -> dict[str, object]: ...

def prepare_instruction_context(
    path: Path | None,
    *,
    page_spec: str | None,
    cache_dir: Path,
    models: Sequence[str],
) -> PreparedInstructionContext: ...
```

Cover: no source; TXT and Markdown decoded once; DOCX remains accepted by the runtime; blank/oversized text rejected; PDF all-pages and subset selection; one selected PDF artifact; one rasterization even when `content_parts()` is called repeatedly; Gemini-style direct PDF content; raster image fallback; explicit instruction-reference notice; metadata digests; sanitized manifest values; and invalid page selections.

- [ ] **Step 5: Implement PDF subset extraction and instruction preparation**

Add `extract_pdf_subset(source_pdf: Path, page_numbers: Sequence[int], output_pdf: Path) -> Path` to `pdf_split.py`. It must open the source once, insert selected pages in the requested order into one temporary PDF, atomically replace `output_pdf`, and reuse a current output when its source-digest-and-page-selection fingerprint matches.

Implement `instructions.py` so text bytes are read and decoded once, PDF selection is resolved once, selected-PDF and raster artifacts use a content-addressed source-digest/page-selection cache directory, direct PDF data is encoded once, and raster pages are rendered/encoded only when at least one applicable model lacks confirmed direct-PDF support. One tested capability helper must delegate to `model_supports_pdf_input()` and conservatively rasterize unknown models; do not combine it with the conflicting Gemini-only `needs_pdf_rasterization()` policy. `content_parts()` only allocates small wrapper dictionaries around immutable cached data-URL strings. The first PDF part must be a text notice identifying the attachment as reference instructions and naming the stage.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/config/test_instruction_guides_config.py tests/llm/test_instruction_context.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/mudidi/instructions.py src/mudidi/config/yaml_config.py src/mudidi/utils/pdf_split.py tests/config/test_instruction_guides_config.py tests/llm/test_instruction_context.py
git commit -m "Add reusable instruction attachment context"
```

---

### Task 2: Managed uploads, form validation, Review, and presets

**Files:**
- Modify: `src/mudidi/web/inputs.py`
- Modify: `src/mudidi/web/forms.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `src/mudidi/web/templates/review.html`
- Test: `tests/web/test_inputs.py`
- Test: `tests/web/test_run_form.py`
- Test: `tests/web/test_dashboard_simplification.py`
- Test: `tests/web/test_production_routes.py`

**Interfaces:**
- Consumes: Task 1 pipeline fields and `InstructionMetadata` validation conventions.
- Produces: managed instruction sidecars, authoritative typed/file validation, structured Review summary keys, and preset keep/replace state used by Task 4.

- [ ] **Step 1: Add failing managed-upload tests**

Add async tests for:

```python
path = await materializer.materialize_instruction_upload(
    "run-1",
    "stage1",
    upload,
    page_spec="2-3",
    stage2_scope=None,
)
```

Require storage beneath `runs/run-1/inputs/instructions/stage1/`, safe original basenames, TXT/MD UTF-8 and 20,000-character validation, PDF signature/readability/page bounds, rejection of PDF pages for non-PDF files, one upload only, aggregate-size enforcement, replacement cleanup, and a `metadata.json` sidecar containing source mode, original filename, suffix, byte count, SHA-256, PDF page count, normalized selected pages, and Stage 2 scope. Extend `materialize_instruction()` so typed files live in the same stage-specific layout and receive `source_mode: typed` metadata. Include legacy sidecar-free preset paths in read tests.

- [ ] **Step 2: Run input tests and confirm failure**

Run: `uv run pytest tests/web/test_inputs.py -q`

Expected: failures because upload materialization and instruction sidecars do not exist.

- [ ] **Step 3: Implement managed instruction files and metadata**

Add:

```python
async def materialize_instruction_upload(
    self,
    run_id: str,
    stage: Literal["stage1", "stage2"],
    upload: UploadFile,
    *,
    page_spec: str | None,
    stage2_scope: Literal["pass1", "pass2", "both"] | None,
    replace: bool = False,
) -> Path: ...

def read_managed_instruction_metadata(path: Path | None) -> dict[str, object] | None: ...
```

The configured path remains the original managed upload. Do not rasterize in the web process. Metadata is written atomically beside the stage source and survives preset copy/rebase.

- [ ] **Step 4: Add failing form and route tests**

Extend `NewRunForm` tests and `/runs/preview` multipart tests for these exact controls:

```text
stage1_instruction_source = typed | file
stage1_instruction_file
stage1_instruction_pdf_pages
stage1_instruction_keep_existing = true | false
stage2_instruction_source = typed | file
stage2_instruction_file
stage2_instruction_pdf_pages
stage2_instruction_keep_existing = true | false
stage2_instruction_scope = pass1 | pass2 | both
```

Cover typed defaults, file-required behavior, typed/file mutual exclusion, blank text uploads, PDF page validation, non-PDF page rejection, disabled-pipeline rejection, default `both`, each Stage 2 scope, validation-return field keys, cleanup after failure, and preservation of safe non-file values.

- [ ] **Step 5: Implement authoritative preview/form behavior**

Add the source/page/scope fields to `NewRunForm`; map only active stage values into `PipelineConfig`. Add instruction uploads to `upload_fields` and keep browser-supplied paths forbidden. In `preview_run()`, process each stage with one shared helper that:

1. rejects uploads in typed mode;
2. rejects non-blank textarea text in file mode;
3. materializes non-blank typed text in typed mode;
4. requires one upload in file mode unless a compatible preset file is explicitly kept;
5. materializes and validates a replacement upload;
6. rejects guide/page/scope values for a pipeline that does not run that stage;
7. puts only managed paths into `payload`.

Use `FormFieldError` with `stage1_instruction_file`, `stage1_instruction_pdf_pages`, `stage2_instruction_file`, or `stage2_instruction_pdf_pages` so errors return to the Input panel.

- [ ] **Step 6: Add failing Review and preset tests**

Assert Review has separate `stage_1_instructions` and `stage_2_instructions` rows with source type, original filename, selected PDF pages/count, and Stage 2 scope, while uploaded/typed contents never appear. Test preset save/load/copy/rebase for typed, TXT, MD, and PDF sources; restored source/page/scope state; saved attachment links; keep without replacement; replacement; and discarding a saved attachment after switching to typed mode.

- [ ] **Step 7: Implement Review and preset metadata**

Replace the legacy `additional_instructions` summary with two metadata summaries. Update `review.html` pipeline keys. Extend `_preset_form_state()`, `_preset_asset_paths()`, and `_preset_asset_links()` using only managed, non-symlink paths and sidecar metadata. Sidecar-free legacy TXT/DOCX preset guides restore as typed text. File-mode presets expose the original filename and file kind without rendering contents.

- [ ] **Step 8: Run focused web tests**

Run: `uv run pytest tests/web/test_inputs.py tests/web/test_run_form.py tests/web/test_dashboard_simplification.py tests/web/test_production_routes.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/mudidi/web/inputs.py src/mudidi/web/forms.py src/mudidi/web/app.py src/mudidi/web/templates/review.html tests/web/test_inputs.py tests/web/test_run_form.py tests/web/test_dashboard_simplification.py tests/web/test_production_routes.py
git commit -m "Add instruction attachment form workflow"
```

---

### Task 3: Runtime pass routing, agentic parity, and manifests

**Files:**
- Modify: `src/mudidi/cli/run.py`
- Modify: `src/mudidi/cli/main.py`
- Modify: `src/mudidi/cli/extract.py`
- Modify: `src/mudidi/extraction/llm_two_stage.py`
- Modify: `src/mudidi/llm/pass_1.py`
- Modify: `src/mudidi/llm/pass_2.py`
- Modify: `src/mudidi/assets/prompts/manifest.json`
- Modify: `src/mudidi/assets/prompts/stage_1/user_benchmark.j2`
- Modify: `src/mudidi/assets/prompts/stage_1/user_inference.j2`
- Modify: `src/mudidi/assets/prompts/stage_2/pass_1/user_single.j2`
- Modify: `src/mudidi/assets/prompts/stage_2/pass_1/user_multi.j2`
- Modify: `src/mudidi/assets/prompts/stage_2/pass_2/user_benchmark.j2`
- Modify: `src/mudidi/assets/prompts/stage_2/pass_2/user_inference.j2`
- Test: `tests/cli/test_config_execution.py`
- Test: `tests/cli/test_command_tree.py`
- Test: `tests/cli/test_run_agentic_args.py`
- Test: `tests/cli/test_extract_instruction_guides.py`
- Test: `tests/llm/test_pass_1_media.py`
- Test: `tests/llm/test_pass_1_multi.py`
- Test: `tests/llm/test_prompt_caching.py`
- Test: `tests/llm/test_prompt_manifest.py`
- Test: `tests/extraction/test_agentic_instruction_context.py`
- Test: `tests/extraction/test_resolved_config_snapshot.py`

**Interfaces:**
- Consumes: Task 1 `PreparedInstructionContext` and Task 1 pipeline fields.
- Produces: exact Stage 1/Pass 1/Pass 2/agentic routing and reproducible stage manifests.

- [ ] **Step 1: Add failing namespace and CLI tests**

Assert `execution_namespace_from_config()` maps `stage1_guides_pages`, `stage2_guides_pages`, and `stage2_guides_scope`. Cover both public command surfaces: add sparse `default=argparse.SUPPRESS` overrides to `cli/main.py` and the `_RUN_OVERRIDE_PATHS` mapping in `cli/run.py`, then keep the legacy direct extraction parser and `register_run_arguments()`/`run_from_args()` forwarding equivalent. The accepted flags are:

```text
--stage-1-guides PATH
--stage-1-guides-pages SPEC
--stage-2-guides PATH
--stage-2-guides-pages SPEC
--stage-2-guides-scope {pass1,pass2,both}
```

Test omitted sparse overrides do not replace YAML values; CLI paths become absolute; `.txt`, `.md`, `.docx`, and `.pdf` are accepted; invalid suffixes, pages without PDF, invalid/out-of-bounds pages, and text beyond 20,000 characters fail; direct-parser scope defaults to `both`.

- [ ] **Step 2: Run CLI tests and confirm failure**

Run: `uv run pytest tests/cli/test_config_execution.py tests/cli/test_extract_instruction_guides.py tests/cli/test_command_tree.py tests/cli/test_run_agentic_args.py -q`

Expected: failures for missing namespace fields, flags, and preparation.

- [ ] **Step 3: Prepare contexts once per run**

Map config fields through sparse CLI overrides, the legacy forwarding layer, and the argparse execution namespace. Replace the eager `stage1_guides_text`/`stage2_guides_text` loading block with `prepare_instruction_context()` after the per-entry output/cache directory is known and before worker threads begin. Store the two immutable contexts on the strategy and use one cache directory per stage under the resolved output. Supply all generation and configured evaluator/rewriter model IDs so required raster variants are prepared before page workers start. Reject PDF guides for `vlm_ocr` and `mathpix_ocr` before worker execution because those backends cannot preserve visual attachment context.

- [ ] **Step 4: Add failing Pass 1 and Pass 2 routing tests**

For Stage 1, assert text guides remain under `USER DEFINED GUIDELINES`, PDF instruction parts precede the explicitly labeled dictionary target image, and repeated calls reuse the same encoded strings. For Stage 2 Pass 1, assert scope `pass1` and `both` include text/PDF references before dictionary samples while `pass2` excludes them. For Pass 2, assert `pass2` and `both` include references while `pass1` excludes them. Cover direct PDF and raster fallback.

- [ ] **Step 5: Implement generation routing**

Add optional `PreparedInstructionContext` parameters to `discover_field_cheatsheet()`, `discover_field_cheatsheet_multi()`, `load_or_discover_parse_rules()`, `_build_direct_mdf_prompt()`, `build_direct_mdf_messages()`, and `extract_direct_mdf()`. Preserve existing callers with an empty default context. Update the prompt templates and `assets/prompts/manifest.json` so textual instructions retain `USER DEFINED GUIDELINES`, name their untrusted source, and visually distinguish reference attachments from introduction/sample/target material. In `TwoStageLLMExtraction`, use `context.text` for guide-template interpolation and append `context.content_parts(model, stage_label=...)` before all dictionary page/sample images. Forward context through both single- and multi-sample Pass 1 paths. Pass Stage 2 context into Pass 1 only for scope `pass1|both`, and into Pass 2 only for `pass2|both`.

- [ ] **Step 6: Add failing agentic parity tests**

Capture evaluator and rewriter messages for Stage 1 and Stage 2. Assert the same selected textual guide block and the same model-appropriate PDF/raster instruction references present in generation are present in every enabled verifier/rewriter call. Assert excluded Stage 2 scopes never leak into the other pass or its agentic calls.

- [ ] **Step 7: Implement agentic parity**

Build agentic user messages as content-part lists when an instruction PDF exists. Append the same guide text to evaluator/rewriter user text and use `content_parts()` with the actual evaluator or rewriter model, not the generation model. Keep the dictionary page image last for Stage 1. Stage 2 agentic calls follow Pass 2 scope because they evaluate Pass 2 output.

- [ ] **Step 8: Add manifest and prompt-cache tests**

Assert Stage 1 and Stage 2 manifests include string paths, source kind, original filename, byte count, SHA-256, PDF page count, selected pages, selected artifact path/digest, and Stage 2 scope. Assert no base64 payload enters manifests/logs. Assert the Pass 1 parse-rule cache and Pass 2 prompt-cache identities change when an applicable instruction digest, selected pages, or scope changes. Add resume tests proving matching identity reuses output while changed bytes, selection, or scope fails with an overwrite-required error before parse-rule/page-output reuse.

- [ ] **Step 9: Implement manifests, cache identity, and resume compatibility**

Replace `_guides_manifest_entry(path, text)` with context metadata serialization. Include instruction fingerprint data in Pass 1 cache identity and `_stage2_prompt_cache_key()`. Compare the applicable instruction identity with existing manifests before accepting parse rules or skipped page outputs; preserve `--overwrite` as the explicit replacement route. Keep provider-reported usage accounting unchanged and preserve `_sanitize_messages()` redaction for every PDF/image data URL.

- [ ] **Step 10: Run focused runtime tests**

Run: `uv run pytest tests/cli/test_config_execution.py tests/cli/test_extract_instruction_guides.py tests/cli/test_command_tree.py tests/cli/test_run_agentic_args.py tests/llm/test_pass_1_media.py tests/llm/test_pass_1_multi.py tests/llm/test_prompt_caching.py tests/llm/test_prompt_manifest.py tests/extraction/test_agentic_instruction_context.py tests/extraction/test_resolved_config_snapshot.py -q`

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/mudidi/cli/main.py src/mudidi/cli/run.py src/mudidi/cli/extract.py src/mudidi/extraction/llm_two_stage.py src/mudidi/llm/pass_1.py src/mudidi/llm/pass_2.py src/mudidi/assets/prompts tests/cli/test_config_execution.py tests/cli/test_extract_instruction_guides.py tests/cli/test_command_tree.py tests/cli/test_run_agentic_args.py tests/llm/test_pass_1_media.py tests/llm/test_pass_1_multi.py tests/llm/test_prompt_caching.py tests/llm/test_prompt_manifest.py tests/extraction/test_agentic_instruction_context.py tests/extraction/test_resolved_config_snapshot.py
git commit -m "Route instruction attachments through pipeline passes"
```

---

### Task 4: Dashboard source controls and themed uploads

**Files:**
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `src/mudidi/web/static/app.css`
- Modify: `src/mudidi/web/static/app.js`
- Modify: every template carrying the dashboard app bundle version
- Test: `tests/web/test_app.py`
- Test: `tests/web/test_theme.py`

**Interfaces:**
- Consumes: Task 2 form names, preset asset dictionaries, keep-existing flags, and validation field keys.
- Produces: accessible typed/file switching, themed uploads, PDF-page disclosure, Stage 2 scope controls, and browser state submitted to Task 2.

- [ ] **Step 1: Add failing rendered-markup and CSS tests**

Assert both stages render semantic source radio groups, typed panels, one `.txt,.md,.pdf` file input, themed `⇧ Choose file` triggers, live filename status, hidden keep-existing controls, conditional PDF-page fields, the persistent PDF cost warning, and Stage 2 scope cards with full pass labels. Assert the native file chooser is visually hidden, all controls use established accent/border/shadow tokens, and mobile rules do not create document-level overflow.

- [ ] **Step 2: Run UI contract tests and confirm failure**

Run: `uv run pytest tests/web/test_app.py tests/web/test_theme.py -q`

Expected: failures because the instruction source panels do not exist.

- [ ] **Step 3: Build the Stage 1 and Stage 2 panels**

Replace the two standalone textareas with two `fieldset.instruction-source-panel` controls. Each has `Type instructions` selected by default and `Upload instruction file`; only the active panel is enabled. Reuse the compact lavender upload pattern added for the MDF guide, with an upward-arrow SVG, hard shadow, uppercase label, hidden native input, and `aria-live` filename status. The PDF-page field accepts `2-4,8`; blank copy states all pages. Stage 2 always shows `Pass 1 only — parsing-guide discovery`, `Pass 2 only — per-page MDF extraction`, and `Both passes`, defaulting to Both.

- [ ] **Step 4: Add failing JavaScript behavior tests**

Use the existing Node VM harness pattern to prove mode switching disables inactive controls, confirms before clearing a non-empty inactive source, clears rejected uploaded/typed values only after confirmation, preserves/restores preset keep state, requires a file only when file mode lacks a kept preset, shows PDF pages only for PDF selections or saved PDFs, hides them for TXT/MD, updates filename status, and removes all Stage 2 controls from submission when the selected pipeline excludes Stage 2.

- [ ] **Step 5: Implement source synchronization**

Initialize every `[data-instruction-source-panel]` after `restoreRunForm()`. One generic synchronizer owns radio state, textarea/file disabled flags, file `required`, keep-existing state, PDF-page visibility/disabled state, focus movement, filename status, and confirmation rollback. Do not add stage-specific duplicate logic. Integrate with `synchronizePipeline()` and `persistRunForm()`.

- [ ] **Step 6: Bump static asset versions and run UI tests**

Increment the shared `dashboard-ui-N` version in `_layout.html`, every template that loads `app.js`, and the two version contract tests. Run: `node --check src/mudidi/web/static/app.js && uv run pytest tests/web/test_app.py tests/web/test_theme.py -q`.

Expected: JavaScript syntax succeeds and all UI tests pass.

- [ ] **Step 7: Browser-verify the actual dashboard**

Restart the managed dashboard and verify at 1568px and 390px widths: source switching, confirmation behavior, TXT/MD/PDF selection, filename status, PDF-page disclosure, saved-file keep/replace state, Stage 2 scope selection, keyboard tab/Enter activation, focus indicators, error return to Input, and no horizontal overflow. Capture the actual computed accent background, 3px border, hard shadow, and responsive bounds.

- [ ] **Step 8: Commit**

```bash
git add src/mudidi/web/templates src/mudidi/web/static/app.css src/mudidi/web/static/app.js tests/web/test_app.py tests/web/test_theme.py
git commit -m "Add stage instruction upload controls"
```

---

### Task 5: Documentation and end-to-end regression

**Files:**
- Modify: `docs/reference/cli.md`
- Modify: `docs/reference/config.md`
- Modify: `docs/superpowers/specs/2026-09-07-stage-instruction-attachments-design.md`
- Test: `tests/config/test_docs_reference.py`
- Test: `tests/web/test_production_routes.py`
- Test: `tests/extraction/test_resolved_config_snapshot.py`
- Test: `tests/extraction/test_instruction_worker_smoke.py`

**Interfaces:**
- Consumes: all completed tasks.
- Produces: generated references, implemented-spec status, end-to-end proof, and a clean branch.

- [ ] **Step 1: Add end-to-end and actual-worker regression cases**

Add production-route cases that prepare runs for: Stage 1 TXT, Stage 1 selected-page PDF, Stage 2 Pass 1-only Markdown, Stage 2 Pass 2-only PDF, Both-pass PDF, typed/file exclusion, preset keep, and preset replacement. Inspect the prepared config and managed metadata.

Add two actual-worker smokes with provider calls stubbed only at the LLM boundary: one Stage 1 selected-PDF instruction run and one split-model Stage 2 scope run. Exercise the real execution namespace, preparation, strategy, prompt builders, manifests, and output lifecycle; assert applicable generation/agentic messages receive instructions, unselected passes do not, and the run-owned selected/raster artifacts are reused.

- [ ] **Step 2: Run end-to-end cases**

Run: `uv run pytest tests/web/test_production_routes.py tests/extraction/test_resolved_config_snapshot.py tests/extraction/test_instruction_worker_smoke.py -q`

Expected: all tests pass.

- [ ] **Step 3: Regenerate CLI and config references**

Run: `uv run python scripts/generate_docs_reference.py`.

Confirm `docs/reference/cli.md` documents both PDF page flags and Stage 2 scope, while `docs/reference/config.md` contains the three new pipeline fields for every generated configuration kind.

- [ ] **Step 4: Mark the approved specification implemented**

Change the spec status from `Awaiting written-spec review` to `Implemented` and add the implementation commit range only after Tasks 1-4 exist. Do not rewrite the approved behavior.

- [ ] **Step 5: Run behavioral smoke, then complete verification**

Repeat the desktop/mobile browser smoke for the complete workflow before the complete dashboard regression. Then run:

```bash
uv run pytest -q
node --check src/mudidi/web/static/app.js
uv run python scripts/generate_docs_reference.py --check
```

Expected: both actual-worker smokes and the browser workflow pass, the full suite passes, generated references are clean, and JavaScript parses.

- [ ] **Step 6: Commit**

```bash
git add docs/reference/cli.md docs/reference/config.md docs/superpowers/specs/2026-09-07-stage-instruction-attachments-design.md tests/config/test_docs_reference.py tests/web/test_production_routes.py tests/extraction/test_resolved_config_snapshot.py tests/extraction/test_instruction_worker_smoke.py
git commit -m "Document stage instruction attachments"
```
