# Task 2 report: instruction attachment form workflow

## Status

Implemented and committed Task 2 as `5938a3a` (`Add instruction attachment form workflow`). The reviewed Task 1 interface commits used as the base are `8179340` and `74006f6`.

## Changed files and symbols

- `src/mudidi/web/inputs.py`
  - Added managed Stage 1/Stage 2 instruction materialization under one stage directory.
  - Added `InputMaterializer.materialize_instruction_upload` for one TXT/Markdown/PDF upload, streaming byte-limit enforcement, shared text/PDF validation, SHA-256 metadata, resolved PDF pages, and atomic source/sidecar writes.
  - Updated typed `materialize_instruction` to use the same managed stage layout and sidecar format.
  - Added `_materialize_instruction_bytes`, `_new_instruction_dir`, `_write_instruction_metadata`, and validation helpers.
  - Added `read_managed_instruction_metadata` for safe sidecar reads.
  - Reused `mudidi.instructions.read_instruction_text` and `resolve_instruction_pdf_pages`.
- `src/mudidi/web/forms.py`
  - Added typed/file source fields, PDF page fields, explicit Stage 2 scope, and explicit keep-existing controls.
  - Added `instruction_review_summary` and metadata-only Stage 1/Stage 2 summaries.
  - Added active/inactive-stage, source-mixing, and page-selection validation.
  - Mapped normalized page selections and Stage 2 scope into `PipelineConfig` only for active stages.
- `src/mudidi/web/app.py`
  - Added shared preview processing for both instruction stages, including forged inactive-stage rejection, upload validation, replacement, explicit keep-existing, typed-blank clearing, and legacy cleanup.
  - Added managed instruction assets to preset state/link materialization; preset copy/rebase retains sidecars.
  - Made immediate and recovered review summaries use the same metadata-only summary shape.
- `src/mudidi/web/templates/review.html`
  - Replaced content-oriented instruction output with source, filename, PDF page, and Stage 2 scope metadata rows.
- `tests/web/test_inputs.py`
  - Added managed source/sidecar, typed layout, PDF page resolution, legacy sidecar-free, invalid content, and cleanup coverage.
- `tests/web/test_run_form.py`
  - Added source/scope defaults, normalized page mapping, and metadata-only summary coverage.
- `tests/web/test_production_routes.py`
  - Added upload/review, source-mixing, forged inactive Stage 2, immediate/recovered summary, preset sidecar keep/replace/clear, and legacy assertion updates.

## Storage and preset decisions

Each active instruction source is stored in:

- `inputs/instructions/stage1/<active-source>`
- `inputs/instructions/stage2/<active-source>`

The corresponding `metadata.json` is written atomically beside the active source. The sidecar records `source_mode`, `original_filename`, normalized `suffix`/`kind`, byte count, SHA-256, PDF page count, resolved unique selected pages, and Stage 2 scope. It does not contain instruction content or an absolute browser path. Config guide paths point at the active managed source. Typed sources use the same stage directory and sidecar shape. PDF blank selection resolves all pages while the config page field remains semantically blank/all.

Preset copy/rebase uses the existing bundle copy operations, so managed sources and sidecars move together. File-mode preset reuse requires explicit keep-existing state; a typed blank clears inherited instructions. Sidecar-free legacy TXT/DOCX guides remain typed during preset loading and summary generation.

## TDD evidence

Initial RED observations:

- `uv run pytest tests/web/test_inputs.py -q` failed at collection because `read_managed_instruction_metadata` was not yet implemented.
- `uv run pytest tests/web/test_run_form.py -q` reported 2 failures because the new browser fields were not yet present.
- The focused production route instruction cases initially reported 4 failures: upload preview returned 422 and source-mixing/forged-inactive cases were not rejected.

Focused GREEN checkpoints:

- `uv run pytest tests/web/test_inputs.py -q` — 13 passed.
- `uv run pytest tests/web/test_run_form.py -q` — 31 passed after the form implementation and assertions were updated.
- The focused production instruction/preset subset — 13 passed.
- Final exact Task 2 command:

  `uv run pytest tests/web/test_inputs.py tests/web/test_run_form.py tests/web/test_dashboard_simplification.py tests/web/test_production_routes.py -q`

  Result: **110 passed, 6 warnings**.

No project-wide tests, formatter, or linter were run.

## Self-review and concerns

- Final focused tests pass, including dashboard simplification compatibility.
- Review output contains metadata only; instruction bodies are asserted absent in immediate and recovered responses.
- Upload failures clean the stage directory and incomplete temporary files.
- Existing `additional_instructions_summary` remains as an unused compatibility helper in `forms.py`; no active route or summary path calls it. It can be removed in a later cleanup if the wider branch confirms no external import contract.
