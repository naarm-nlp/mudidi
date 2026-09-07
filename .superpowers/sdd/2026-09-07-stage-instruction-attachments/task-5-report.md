# Task 5 report

## Status

Implemented. Task 5 documentation, route/snapshot coverage, actual-worker regression smokes, generated-reference refresh, spec status update, and obsolete helper cleanup are complete.

## Coverage mapping

- Stage 1 TXT: existing `test_preview_materializes_instruction_uploads_and_review_metadata`.
- Stage 1 selected-page PDF: added `test_preview_materializes_stage1_selected_pdf_instruction`; checks prepared config and managed metadata.
- Stage 2 Pass 1-only Markdown: added `test_preview_materializes_stage2_markdown_for_pass1_only`; checks prepared config and managed metadata.
- Stage 2 Pass 2-only PDF: existing `test_uploaded_instruction_review_matches_recovered_review_metadata` and `test_pdf_preset_restores_pages_and_explicit_blank_keep_preserves_all_pages`.
- Stage 2 Both-pass PDF: existing `test_pdf_preset_restores_pages_and_explicit_blank_keep_preserves_all_pages` with the default/both scope and blank selection.
- Typed/file exclusion: existing `test_preview_rejects_instruction_source_mixing`.
- Preset keep and replacement: existing `test_instruction_preset_keep_replace_and_clear_preserves_sidecars`.
- JSON-safe selected PDF manifest: added `test_pdf_guide_manifest_is_json_safe_and_preserves_selected_artifact`.
- Actual worker coverage: added `tests/extraction/test_instruction_worker_smoke.py` with Stage 1 selected-PDF and Stage 2 split-model/scope smokes. Both run `execute_extraction_config`, prepare real contexts, build real strategies/prompts, write manifests and outputs, and stub only `_completion_with_retries` at the LLM client boundary.

## Changed files

- `docs/reference/cli.md`
- `docs/reference/config.md`
- `docs/superpowers/specs/2026-09-07-stage-instruction-attachments-design.md`
- `src/mudidi/web/forms.py`
- `tests/config/test_docs_reference.py`
- `tests/web/test_production_routes.py`
- `tests/extraction/test_resolved_config_snapshot.py`
- `tests/extraction/test_instruction_worker_smoke.py`

The approved spec now has status `Implemented` and records Tasks 1–4 as implemented through `e0d3f5c` inclusive. The unused `additional_instructions_summary` helper is removed.

## RED/GREEN evidence

- RED: `uv run pytest tests/extraction/test_instruction_worker_smoke.py -q` initially reported two assertion failures while calibrating expected agentic/media call counts and output paths; no source contract defect was exposed.
- GREEN: `uv run pytest tests/extraction/test_instruction_worker_smoke.py -q` → 2 passed.
- GREEN: `uv run pytest tests/web/test_production_routes.py -q -k 'selected_pdf_instruction or markdown_for_pass1_only'` → 2 passed.
- GREEN: `uv run pytest tests/web/test_production_routes.py tests/extraction/test_resolved_config_snapshot.py tests/extraction/test_instruction_worker_smoke.py tests/config/test_docs_reference.py -q` → 61 passed, 6 existing deprecation warnings.
- GREEN: `uv run python scripts/generate_docs_reference.py --check` → clean (exit 0).

## Generated-document proof

Ran `uv run python scripts/generate_docs_reference.py`. The generated CLI reference now includes both PDF page flags and Stage 2 scope. The generated config reference contains `stage1_guides_pages`, `stage2_guides_pages`, and `stage2_guides_scope` in every extraction template. The generator check passes.

## Commit

`97bf194` (`Document stage instruction attachments`).

## Self-review

- Worker smokes capture and inspect complete LLM message payloads, including reference labels, selected PDF file bytes, raster page URLs, stage/pass model routing, and agentic evaluator/rewriter calls.
- They assert selected/raster artifact reuse across multiple calls, pass-1 exclusion for `pass2` scope, output lifecycle artifacts, run manifests, usage, and JSON serialization.
- Route additions cover only matrix gaps; existing load-bearing cases were reused rather than duplicated.
- Full repository tests, linters/formatters, JavaScript checks, and browser smoke were intentionally not run; those are controller-owned final gates.

## Concerns

Only existing PyMuPDF/FastAPI deprecation warnings appeared in focused tests. No behavioral concerns remain within the assigned scope. Full-suite and browser verification remain pending controller execution.
