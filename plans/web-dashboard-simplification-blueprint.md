# Blueprint: Web Dashboard Simplification and MDF Terminology

Status: reviewed; critical upload-lifecycle and packaged-resource findings incorporated
Objective: simplify the local production dashboard around the supported
language-model workflow, make agentic verification explicit, replace path-like
inputs with safe browser uploads, and distinguish the LLM-inferred **MDF parsing
guide** from the optional official **MDF manual**.

## Source-of-truth documents

Read these before executing any step:

- `plans/local-web-app-blueprint.md`
- `docs/design/web-app/overview.md`
- `docs/design/web-app/ux.md`
- `docs/design/web-app/architecture.md`
- `docs/design/web-app/parse-rule-review.md`
- `src/mudidi/web/forms.py`
- `src/mudidi/web/app.py`
- `src/mudidi/web/inputs.py`
- `src/mudidi/config/yaml_config.py`
- `src/mudidi/cli/extract.py`

This blueprint refines the dashboard created by the local-web-app blueprint. It
does not remove advanced capabilities from the CLI, YAML schema, or extraction
engine.

## Locked product decisions

1. The changes are limited to the local web dashboard unless a typed execution
   field is needed to carry new web input. CLI and YAML users retain expert OCR
   backends, OCR hints, Stage 1 column mode, guide files, and current internal
   parse-rule contracts.
2. User-visible terminology is:
   - **MDF parsing guide**: the dictionary-specific parsing instructions inferred
     by the LLM in Stage 2 Pass 1 and reviewed before Stage 2 Pass 2.
   - **MDF manual**: the optional general 65-page MDF reference PDF bundled with
     MUDIDI or a shorter PDF uploaded by the user.
3. Internal compatibility names remain unchanged: Python symbols, database
   fields, statuses, routes such as `/parse-rules`, filenames such as
   `parse-rules.json`, and serialized event/config keys keep `parse_rules`.
4. The web dashboard always builds `pipeline.strategy: two_stage`,
   `pipeline.stage1_mode: flat`, and `pipeline.stage1_typography: false`, and it
   never supplies `input.ocr_text`. Advanced alternatives remain supported
   outside the dashboard.
5. Agentic verification is off by default. When enabled, both applicable stages
   are checked by default, and users may uncheck either stage. A stage absent
   from the selected pipeline is disabled and is always mapped to `false`.
6. Pipeline selection has three radio choices:
   - Complete digitization
   - Transcription only
   - Parse transcription into MDF (Multi-Dictionary Formatter)

   The discovery-only choice is removed from the dashboard because MDF parsing
   already includes discovery and mandatory review of the inferred MDF parsing
   guide.
7. The bundled MDF manual is optional. Users can select **No MDF manual**, **Use
   bundled MDF manual**, or **Upload a custom MDF manual**. The bundled choice
   warns that the manual has 65 pages and may substantially increase token
   usage. The UI recommends uploading only relevant pages when the required MDF
   markers are already known.
   The currently inspected source file has 65 pages, is 530,169 bytes, and has
   SHA-256 `6c654140ab6a9914baf1f6384750b0b10e7408c72bf16df1242f9ff4bb7cd015`;
   Step 2 must re-verify these facts and separately establish redistribution
   permission before packaging it.
8. The bundled manual is downloadable through a fixed, read-only application
   route. No route accepts a caller-supplied filesystem path.
9. Browser inputs use uploads instead of typed source paths wherever practical.
   Page images support multi-file and directory selection; introduction,
   alphabet, existing MDF parsing guide, and custom MDF manual inputs use file
   selection. The output directory remains a typed local path because standard
   browser file APIs cannot disclose an arbitrary folder path to a localhost
   server. A future native desktop wrapper may replace that final path field.
10. PDF page specifications accept positive Arabic numbers, commas, and ranges
    only, for example `1-12,15,18-20`. Roman numerals are rejected.
11. Stage 1 and Stage 2 additional instructions are optional multiline text in
    the browser. The server materializes them as bounded UTF-8 files in the
    run-owned input bundle and reuses the existing YAML/CLI guide-file execution
    contract; the public YAML schema does not gain redundant string fields.

## Global invariants

Every change must preserve these properties:

1. Web runs continue through the typed `InferenceConfig` boundary; no CLI string
   construction, `sys.argv` mutation, or shell execution is introduced.
2. The Stage 2 human-approval boundary remains mandatory. Changing labels must
   not weaken `ApprovedParseRules`, snapshot digest, version, or run binding.
3. Uploads are copied beneath the run-owned data directory, validated before
   use, cleaned up after failed previews, and never interpreted as arbitrary
   server paths.
4. The built-in manual download is a constant packaged resource with an
   attachment filename and PDF media type. It cannot traverse or enumerate the
   filesystem.
5. No secrets enter forms beyond the existing ephemeral credential flow, run
   snapshots, logs, downloads, tests, or source control.
6. Hidden or removed fields are not silently honored if submitted manually.
   `NewRunForm` remains `extra="forbid"`.
7. No paid model calls are required by tests.
8. All controls are usable by keyboard and expose real labels, fieldsets,
   legends, focusable help buttons, and accessible error text. Tooltips must not
   rely on hover alone.
9. The bundled PDF may be committed and redistributed only after its provenance
   and license are recorded. If redistribution is not permitted, stop Step 2
   and record a plan mutation rather than silently shipping it.
10. A successful preview owns an immutable input bundle for its run. Resumes and
    reviews never depend on temporary uploads or the currently installed package
    version. Presets own independent copies rather than references to a run.

## Dependency graph

```text
Step 1: dashboard contract and terminology tests
   ├── Step 2: uploads + packaged MDF manual
   └── Step 3: pipeline + agentic form simplification
Steps 1+2 ── Step 4: context inputs and direct instructions
Steps 2+3+4 ── Step 5: complete user-visible terminology migration
Steps 1–5 ── Step 6: E2E, packaging, security, and documentation gate
```

Parallel opportunities:

- Steps 2 and 3 may run in parallel after Step 1 if their changes to
  `app.py`, `forms.py`, and `home.html` are coordinated through small commits.
- Documentation inventory for Step 5 may begin after Step 1, but wording should
  not be merged until the corresponding UI and routes exist.
- Use the strongest available model for upload security, packaged-resource
  review, cross-field validation, and the final adversarial review. Routine
  templates, CSS, wording, and snapshots can use the default coding model.

## Workflow

This repository currently uses branch `web/local-app-foundation`. The default
workflow is one descendant branch, `web/dashboard-simplification`, with six
atomic commits in the order below. If stacked PRs are preferred, each listed
step branch must target the immediately preceding step branch, and be retargeted
to `main` only after its parent merges. Do not push, publish, or merge without
explicit user approval. Before every step:

```bash
git status --short --branch
git diff --check
uv run pytest tests/web -q
```

Stage only explicit paths. Never use `git add -A`. Preserve unrelated user
changes and generated benchmark/evaluation output.

---

## Step 1 — Freeze the simplified dashboard contract

Objective: make the requested web-only scope and terminology testable without
committing red tests.
Branch: `web/dashboard-contract`
Depends on: none
Rollback: revert the tests/design update; runtime behavior is unchanged.

### Context brief

The current dashboard exposes `PipelineChoice.DISCOVER_RULES`, `QualityChoice`,
OCR strategy and backend fields, OCR hints, Stage 1 mode, path-based guide
fields, and user-facing “parse-rules” wording. Before deleting these controls,
encode the intended web-only contract and terminology so later PRs cannot
accidentally remove CLI/YAML support or bypass Stage 2 approval.

### Files

- `docs/design/web-app/overview.md`
- `docs/design/web-app/ux.md`
- `docs/design/web-app/architecture.md`
- `docs/design/web-app/parse-rule-review.md`
- `tests/web/test_run_form.py`
- `tests/web/test_app.py`
- `tests/web/test_parse_rule_routes.py`

### Tasks

1. Add a short glossary distinguishing MDF parsing guide and MDF manual while
   documenting that internal `parse_rules` identifiers are compatibility
   details.
2. Document the three pipeline radio choices, agentic off-by-default behavior,
   mandatory Stage 2 review, web-only flat Stage 1, and absence of expert OCR.
3. Add green characterization tests proving the authoritative YAML models still
   accept current OCR, stage mode, guide-file, and discovery-only options.
4. Write the exact future semantic assertions for radios, accessible help,
   expandable Agentic settings, numeric-only intro examples, and file inputs in
   this design brief. Add each red test locally at the start of its owning
   implementation step; never merge an intentionally failing test.
5. Add terminology assertions for New Run, Review, Active Run, history/detail,
   output browsing, and the MDF parsing guide review page.
6. Inventory all user-visible occurrences with:

   ```bash
   rg -n -i "parse[- ]rules?|toolbox reference|quality|expert OCR|OCR hint|stage 1 mode|guide file" \
     src/mudidi/web docs tests/web
   ```

   Classify each match as user-visible, internal compatibility, or historical
   design text. Store the classification in the PR description.

### Tests and verification

```bash
uv run pytest tests/web/test_run_form.py tests/web/test_app.py \
  tests/web/test_parse_rule_routes.py -q
uv run pytest tests/config tests/cli -q
```

### Exit criteria

- Existing shared capabilities and the approval boundary have green
  characterization tests; future assertions have explicit owning steps.
- The designs state that feature removal is web-only.
- The internal Stage 2 authorization contract is explicitly preserved.

---

## Step 2 — Generalize managed uploads and package the MDF manual

Objective: give every run durable, validated inputs and make the official MDF
manual safely available from source and installed builds.
Branch: `web/mdf-manual-and-uploads`
Depends on: Step 1
Rollback: revert the resource/upload commit; existing run data remains valid.

### Context brief

`InputMaterializer` currently supports one uploaded PDF or a set of page images
under a run-owned directory. Other form controls accept raw server paths. The
official manual exists locally as
`assets/Pages from ToolboxReferenceManual.pdf`, but `assets/*` and `*.pdf` are
ignored and `pyproject.toml` only force-includes `PROMPT.json`. Therefore a wheel
cannot currently use or serve that manual.

### Files

- `.gitignore`
- `pyproject.toml`
- `src/mudidi/assets/MDFReferenceManual.pdf` (new packaged asset)
- `src/mudidi/web/inputs.py`
- `src/mudidi/web/app.py`
- `src/mudidi/web/templates/home.html`
- `tests/web/test_inputs.py`
- `tests/web/test_app.py`
- `tests/web/test_security.py`
- `tests/web/test_packaging.py` (new if no installed-wheel test exists)

### Tasks

1. Verify the manual's provenance and redistribution permission. Record its
   SHA-256, byte size, page count, source, and licensing decision in the PR.
2. Copy the approved PDF to `src/mudidi/assets/MDFReferenceManual.pdf`, add the
   narrow ignore exception, and explicitly include it in the wheel. Do not
   unignore arbitrary PDFs or the entire `assets/` directory.
3. Add a package-resource helper that reads the exact manual bytes both from a
   source checkout and an installed wheel without relying on the process CWD.
   Do not persist a temporary `importlib.resources.as_file()` path in config.
4. Add a fixed `GET /assets/mdf-manual` route that streams those packaged bytes
   with `application/pdf`, attachment disposition, a stable safe filename, and
   existing security headers.
5. Refactor `InputMaterializer` into explicit role methods with exact engine
   contracts and per-role destinations:
   - pages: one PDF, or `.png/.jpg/.jpeg/.webp` files from multi-file/directory
     upload;
   - introduction: one `.pdf/.png/.jpg/.jpeg/.webp/.txt/.md/.docx` file, or a
     directory containing those formats;
   - alphabet: one `.png/.jpg/.jpeg/.webp/.gif/.txt/.md/.docx` file;
   - existing MDF parsing guide: one schema-valid JSON file;
   - custom MDF manual: one PDF;
   - direct Stage 1/Stage 2 instructions: server-created UTF-8 `.txt` files.
6. Validate content as well as suffix: PDF signature and page readability,
   decodable images, UTF-8 text, readable DOCX container, and the authoritative
   MDF parsing-guide schema. Reject special files and symlinks. Retain
   basename/path traversal checks, unique destinations, atomic `.part`
   writes, total-size limits, cleanup on error, and per-run isolation. Define
   role-specific size limits rather than silently sharing the current 25 MB cap
   if the 65-page manual needs a different policy.
7. Replace corresponding raw path controls with `type=file`. Add separate
   page/introduction directory choosers using `webkitdirectory` as progressive
   enhancement; ordinary file/multi-file selection remains available. Directory
   hierarchy is discarded because the engine consumes one flat directory;
   validate relative names, flatten safely, and reject duplicate basenames.
8. Parse uploads explicitly in the preview route. Never fold arbitrary
   `UploadFile` values into the Pydantic string payload. On any validation or
   preparation failure, discard every role under the pending run ID.
9. On preview, create `<data_dir>/runs/<run_id>/inputs`, materialize every user
   upload there, and write config paths only to that final bundle. Selecting the
   bundled manual copies the exact packaged bytes into the same bundle and
   records its SHA-256 and verified page count; it never points execution at the
   package installation. A failed preparation removes the bundle. A successful
   preparation commits it to the durable run.
10. Define lifecycle rules:
   - active, review-pending, failed-resumable, and completed runs retain their
     own input bundle;
   - explicit terminal-run deletion removes only that run's bundle;
   - cancellation alone does not remove inputs needed for resume/history;
   - startup removes stale staging/`.part` files and, after a documented grace
     period, run directories with no database row;
   - a saved preset copies required managed inputs to
     `<data_dir>/presets/<preset_id>/inputs` and rewrites its config;
   - preparing a preset clones its assets into the new run and rewrites paths,
     so deleting either source does not break the other.
11. Reconcile per-role upload limits with the current 25 MB global request cap.
   Define one aggregate maximum plus multipart overhead and add an ASGI receive
   byte counter so missing, chunked, or false `Content-Length` cannot bypass it.
12. Keep the typed output directory field and add help text explaining that it
   is a folder on the same computer running MUDIDI.

### Tests and verification

```bash
uv run pytest tests/web/test_inputs.py tests/web/test_security.py \
  tests/web/test_app.py -q
uv build
# Install the wheel into a temporary uv environment, then assert the manual
# resource exists and GET /assets/mdf-manual returns a PDF attachment.
```

Test traversal names, duplicate/flattened names, unsupported or spoofed files,
mixed PDF/images, invalid JSON schemas, oversized and chunked bodies,
interrupted writes, cross-role collisions, cleanup after form errors, abandoned
preview/startup cleanup, cancel/resume, review/restart, preset clone independence,
fixed-route security, and wheel-installed resource lookup. Inspect both wheel
and sdist and assert the bundled PDF is exactly 65 pages and matches the recorded
SHA-256.

### Exit criteria

- A wheel-installed dashboard can download the bundled MDF manual and can copy
  its exact bytes into a durable run-owned input bundle.
- The download route exposes no arbitrary path capability.
- Run, preset, cleanup, resume, and restart ownership rules are covered by tests.
- All input-like fields in scope use managed uploads; output remains the only
  typed filesystem path.
- Redistribution evidence is recorded or this step is stopped with a mutation.

---

## Step 3 — Replace Pipeline/Quality controls with radios and Agentic settings

Objective: expose only the three supported production workflows and make paid
agentic verification an explicit off-by-default choice.
Branch: `web/pipeline-agentic-controls`
Depends on: Step 1
Rollback: revert form/template/JS changes; persisted runs use resolved config
and remain readable.

### Context brief

`NewRunForm` currently maps `QualityChoice.STANDARD`, `VERIFIED`, and `CUSTOM`
to agentic stage booleans. The default is `VERIFIED`, while the requested web
default is no agentic verification. Pipeline selection also exposes a
discovery-only mode and uses a select menu. Expert OCR configuration contributes
many web-only fields even though production inference should always use the
language-model pipeline.

### Files

- `src/mudidi/web/forms.py`
- `src/mudidi/web/templates/home.html`
- `src/mudidi/web/templates/review.html`
- `src/mudidi/web/static/app.js`
- `src/mudidi/web/static/app.css`
- `tests/web/test_run_form.py`
- `tests/web/test_app.py`
- `tests/web/test_production_routes.py`

### Tasks

1. Remove `QualityChoice`, the `quality` form field, and
   `PipelineChoice.DISCOVER_RULES` from the web form contract. Do not delete
   internal `2-pass-1` execution support.
2. Replace pipeline selection with an accessible radio fieldset. Each choice
   has a focusable information button whose help text works on hover, focus, and
   touch/click. Do not encode essential help solely in a `title` attribute.
3. Add `agentic: bool = false`. Place a collapsed/hidden Custom verification
   section immediately below it. When Agentic is Yes, reveal the section and
   render both applicable `verify_stage1` and `verify_stage2` checkboxes as
   checked on the initial page. Keep their Pydantic defaults false: in an HTML
   submission, an absent checkbox means the user unchecked it. Do not use a
   default of true to reinterpret absence.
4. Dynamically disable and uncheck verification controls for stages outside the
   selected pipeline. Repeat the same intersection in `_verification_stages()`
   so behavior is correct without JavaScript or under a forged request.
5. Disable hidden custom-verification inputs when Agentic is No and make the
   backend ignore any forged verification settings. When validation re-renders
   the form, preserve explicit user choices rather than resetting checked state.
   Without JavaScript, Agentic Yes plus a missing checkbox means false; Agentic
   No always produces false/false. The resulting `AgenticConfig.stage1` and
   `.stage2` must obey those rules server-side.
6. Rename the wizard step and summaries from Quality to Agentic and show a
   concise `Off`, `Stage 1`, `Stage 2`, or `Stage 1 + Stage 2` result.
7. Remove all expert OCR/VLM/Mathpix fields from `NewRunForm` and the template.
   Build default `VlmConfig`/`MathpixConfig` only as required by the typed root
   model, and always map the web strategy to `two_stage`.
8. Remove `stage1_mode` from `NewRunForm`; always map `flat` and typography
   false. Add forged-payload tests proving removed fields produce 422 rather
   than changing execution.
9. Keep model controls dynamic by active stages: transcription shows only Stage
   1; MDF parsing shows only Stage 2 Pass 1/Pass 2; complete shows all three.

### Tests and verification

```bash
uv run pytest tests/web/test_run_form.py tests/web/test_app.py \
  tests/web/test_production_routes.py -q
uv run pytest tests/config tests/cli -q
```

Cover raw multipart and no-JavaScript submissions, Agentic Yes plus missing
checkboxes, Agentic No plus forged settings, initial checked presentation,
validation-error re-rendering, each stage combination, forged removed fields,
radio accessibility, and the continued mandatory MDF parsing guide review for
Stage 2 pipelines.

### Exit criteria

- The form offers exactly three pipeline workflows and no expert OCR controls.
- Agentic is off by default and stage selection is correct server-side.
- Every dashboard run uses two-stage LLM strategy, flat Stage 1, and no
  typography preservation without reducing CLI/YAML capability.

---

## Step 4 — Simplify context inputs and materialize additional instructions

Objective: replace specialist path fields with understandable uploads/text and
map them through existing typed execution contracts.
Branch: `web/context-and-instructions`
Depends on: Steps 1 and 2
Rollback: revert web text materialization and upload mappings; old CLI/YAML
guide paths remain unaffected.

### Context brief

The dashboard currently accepts `ocr_text`, `stage1_guides`, `stage2_guides`,
`toolbox_pdf`, and `parse_rules_file` as typed paths. The engine already loads
guide files and appends their text to Stage 1 and Stage 2 prompts. The web can
reuse that stable contract by materializing text areas into its run-owned input
bundle; no new public YAML fields are necessary.

### Files

- `src/mudidi/web/forms.py`
- `src/mudidi/web/app.py`
- `src/mudidi/web/inputs.py`
- `src/mudidi/web/templates/home.html`
- `src/mudidi/web/templates/review.html`
- `src/mudidi/web/static/app.js`
- `tests/config/`
- `tests/web/test_run_form.py`
- `tests/web/test_production_routes.py`

### Tasks

1. Remove the web OCR hint control and form field. Assert web mappings always
   produce `input.ocr_text is None`; do not remove `InputConfig.ocr_text`.
2. Replace guide path controls with bounded multiline text areas and info
   buttons that explain the text is appended to the corresponding stage prompt.
   The preview route writes non-empty values atomically as UTF-8 files beneath
   `<run>/inputs/instructions/` and maps them to the existing
   `PipelineConfig.stage1_guides`/`stage2_guides` paths. Enforce the same length
   and encoded-byte limits in HTML, Pydantic, and materialization. Escape text
   in review pages. Existing YAML/CLI behavior is unchanged.
3. Add a web-strict numeric page-spec validator for dictionary pages,
   introduction pages, and representative MDF parsing guide pages. Accept only
   positive integers, commas, and ascending inclusive ranges; normalize
   whitespace; reject Roman numerals, zero, descending ranges, and malformed
   delimiters. Reuse engine parsing only after applying this web-strict grammar;
   do not tighten legacy CLI/YAML syntax as an incidental change.
4. Change the introduction placeholder to an Arabic-number example and map the
   managed introduction/alphabet uploads from Step 2 into `InputConfig`. Enforce
   exact cross-field rules: PDF page input requires dictionary pages and may use
   introduction-page numbers but not a separate introduction upload; image
   page input may use a separate introduction file/directory but not PDF page
   number fields.
5. Rename the existing JSON upload to **Existing MDF parsing guide** and the
   representative page control to **Representative MDF parsing guide pages**.
   Its info text explains that these pages are sampled by Stage 2 Pass 1 to
   infer dictionary-specific MDF markers and structure before review.
6. Treat an uploaded existing MDF parsing guide as untrusted input. Validate its
   schema, copy it to the run bundle, and feed it through the same review staging
   as a generated guide. It may prepopulate the review but must never construct
   `ApprovedParseRules` directly; only approval of the immutable run-bound
   snapshot authorizes Pass 2.
7. Add `mdf_manual_source: none | bundled | upload` to the web form:
   - `none` maps `input.toolbox_pdf` to `None`;
   - `bundled` copies the packaged manual into the run bundle and maps the
     durable copy;
   - `upload` requires the run-owned custom PDF path.

   Hide/disable upload input unless `upload` is selected. Show the 65-page token
   warning and download link with the bundled option. Recommend uploading only
   relevant manual pages for known marker sets.
8. Display the selected manual source and instruction presence—not full prompt
   contents—in the compact review summary. The detailed review may show escaped
   user text, never render it as HTML.
9. Ensure resolved configuration snapshots contain absolute managed instruction
   and manual paths but no original client path or browser fake path. The
   existing run manifest embeds loaded guide text as it does for CLI guide
   files; no duplicate public config field is added.

### Tests and verification

```bash
uv run pytest tests/config tests/web/test_run_form.py \
  tests/web/test_production_routes.py -q
uv run pytest tests/extraction tests/cli -q
```

Cover empty/maximum/over-limit and non-UTF-8 instructions, HTML escaping, all
manual modes, missing custom upload, inactive Stage 2 controls, numeric page
grammar, PDF/introduction conflicts, and no regressions for YAML guide paths.
For both generated and uploaded MDF parsing guides, test tampering before and
after review, approval replay across run IDs, and restart/resume at review.

### Exit criteria

- The dashboard exposes no OCR hint or guide-file path inputs.
- Direct additional instructions reach the correct stage prompts through
  durable run-owned files and the existing typed configuration paths.
- The MDF manual source maps deterministically and remains optional.
- Roman numeral page specifications fail with a useful field error.

---

## Step 5 — Complete the user-visible MDF terminology migration

Objective: consistently distinguish inferred MDF parsing guides from the
general MDF manual without breaking internal compatibility.
Branch: `web/mdf-language`
Depends on: Steps 2, 3, and 4
Rollback: revert wording/templates/docs only; internal contracts are unchanged.

### Context brief

The application currently exposes parse-rule wording in the review checkpoint,
active-run messages, history/detail views, artifact labels, route navigation,
and documentation. The user has explicitly limited the rename to visible
wording, so serialized formats and code identifiers must remain stable.

### Files

- `src/mudidi/web/templates/*.html`
- `src/mudidi/web/forms.py` summary labels only
- `src/mudidi/web/app.py` user-facing messages only
- `src/mudidi/web/static/app.js`
- `docs/design/web-app/*.md`
- relevant public docs under `docs/`
- `tests/web/`

### Tasks

1. Replace visible “parse rules”/“parse-rules” with **MDF parsing guide** across
   New Run, Review, Active Run, history/detail, output browsing, approval,
   validation/error strings, SSE/event presentation, page titles, aria labels,
   artifact display labels, empty states, help text, and documentation.
2. Replace visible “Toolbox reference PDF”/“MDF General Instruction” with **MDF
   manual** and clearly identify the built-in document as the general official
   reference rather than a dictionary-specific inferred guide.
3. Use verbs that preserve the workflow: **Infer MDF parsing guide**, **Review
   MDF parsing guide**, **Approve and continue MDF parsing**, and **Download MDF
   parsing guide**.
4. Preserve internal routes, status values, event names, database columns,
   Python symbols, JSON keys, artifact filenames, and query selectors unless a
   selector itself becomes user-visible. Treat existing selectors consumed by
   tests or JavaScript as compatibility contracts even when their names retain
   `parse_rules`.
5. Add a presentation helper if internal statuses leak into templates; map
   internal values to user-facing labels in one place instead of renaming stored
   state.
6. Repeat the Step 1 inventory. Every remaining match must be classified as an
   intentional internal compatibility term or historical migration note.

### Tests and verification

```bash
uv run pytest tests/web -q
rg -n -i "parse[- ]rules?|toolbox reference|MDF General Instruction" \
  src/mudidi/web/templates docs
```

### Exit criteria

- Users consistently see MDF parsing guide for inferred dictionary-specific
  instructions and MDF manual for the optional reference PDF.
- Existing bookmarked routes, stored runs, event streams, and artifacts remain
  compatible.

---

## Step 6 — Browser, packaging, security, and documentation release gate

Objective: prove the simplified dashboard is accessible, secure, package-safe,
restart-safe, and backward-compatible before release.
Branch: `web/dashboard-simplification-gate`
Depends on: Steps 1–5
Rollback: do not merge/release until all findings are resolved; individual
feature commits remain independently revertible.

### Context brief

This gate verifies behavior that unit tests cannot fully prove: dynamic controls,
keyboard tooltips, checkbox defaults, directory upload, hidden-field disabling,
manual download from an installed wheel, responsive layout, and unchanged CLI
advanced functionality.

### Files

- `tests/e2e/` or `tests/web/test_dashboard_e2e.py`
- `.github/workflows/docs.yml` or the existing CI workflow, if needed
- user documentation under `docs/`
- `README.md` only if its web instructions require the new terms

### Tasks

1. Add Playwright coverage for all three pipeline choices; Agentic No/Yes;
   stage checkbox defaults; help on mouse, keyboard, and touch; manual choices;
   custom uploads; direct instructions; validation recovery; and the Stage 2 MDF
   parsing guide approval checkpoint for both generated and uploaded guides.
2. Test a complete no-network run with the fake execution service and assert the
   resolved config contains fixed web defaults and no removed dashboard fields.
3. Build/install the wheel in a clean temporary environment, launch on loopback,
   render templates/static assets, download the packaged manual, and submit a
   preview using it.
4. Run security tests for upload traversal, type/size confusion, HTML injection
   in direct instructions, arbitrary download attempts, CSRF/Origin policy,
   cleanup, immutable approval replay/tampering, and secret redaction.
5. Confirm CLI/YAML smoke tests still accept expert OCR, OCR hint, column mode,
   guide files, and Stage 2 discovery-only commands.
6. Update web documentation with screenshots or semantic descriptions, manual
   licensing/source information, token-cost warning, the browser output-path
   limitation, and accessible explanations of Agentic and MDF parsing guide
   review.
7. Run an adversarial review focused on approval bypass, path handling,
   packaged-PDF redistribution, hidden-field trust, and terminology ambiguity.
8. Exercise preview → restart → review → approve → Pass 2, cancel → resume,
   terminal deletion, orphan cleanup, preset creation, source-run deletion, and
   preset preparation. Assert every config path points to the correct owner and
   no operation breaks another run or preset.

### Verification

```bash
uv run pytest tests/web tests/config tests/cli -q
uv run pytest -q
uv run mkdocs build --strict
uv run python scripts/generate_docs_reference.py --check
uv build
git diff --check
```

Run the project's dependency audit command if available; document why any
unresolved advisory is non-exploitable before release.

### Exit criteria

- All requested UI removals and renames are verified in a real browser.
- The installed application can use and download the MDF manual without the
  repository checkout.
- Stage 2 cannot proceed without approval of the exact reviewed MDF parsing
  guide.
- Full tests, strict docs, generated references, packaging, and security checks
  pass.

## Plan mutation protocol

When implementation discovers that an assumption is wrong:

1. Stop the affected step before broadening scope.
2. Record the current branch/SHA, observed evidence, affected invariants, and
   proposed replacement in a **Plan mutation** section of this file or its PR.
3. Re-run dependency analysis for downstream steps and update their context
   briefs, tests, and rollback notes.
4. Request user approval when the mutation changes visible behavior, stored
   formats, security boundaries, distribution rights, or CLI/YAML support.
5. Continue only after the mutation is explicit and reviewable. Never silently
   rename internal `parse_rules` contracts or weaken the approval boundary.

### Plan mutation — MDF manual redistribution provenance (2026-07-13)

Observed evidence:

- The local 65-page PDF identifies SIL Toolbox/MDF material and matches the
  recorded size and SHA-256, but it contains no extracted license statement.
- SIL's MDF page makes related manuals available for download, and SIL's general
  site terms say many software projects use open licenses; neither statement
  establishes the license of this exact extracted 65-page document.
- SIL's standard freeware agreement permits copying a complete SIL product but
  also says a Product Element may not be included in another product unless its
  individual terms allow it.

Impact: the technical implementation and package tests include the PDF, but the
branch must not be released or merged with that binary until the repository
owner confirms redistribution permission for this exact document. If permission
cannot be documented, replace the packaged asset with a user-provided/local
manual or an official-download/cache workflow, then update the built-in option,
tests, and documentation. This mutation does not affect the MDF parsing guide
workflow or custom-manual upload.

## Anti-pattern checklist

Reject an implementation that:

- deletes advanced OCR/column/guide support from shared config merely because
  the dashboard hides it;
- trusts hidden inputs or JavaScript to enforce agentic/stage combinations;
- serves a caller-provided path or exposes a generic asset download endpoint;
- references the bundled PDF through a repository-relative path;
- commits the manual without a redistribution decision;
- uses `title` alone for important help;
- injects additional-instruction text as trusted HTML;
- auto-approves an inferred MDF parsing guide;
- renames database/status/artifact contracts for cosmetic consistency;
- claims a browser can select an arbitrary output path without a native helper.
