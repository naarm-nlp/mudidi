<!-- Generated: 2026-07-11 | Files scanned: 255 | Token estimate: ~450 -->

# Annotation UI Architecture

> MUDIDI has no end-user web app. The only UI surface is **Label Studio** for gold annotation.

## Label Studio Workflow

```
Dictionary page images
  → Label Studio NER projects (annotation/label_studio/)
  → span_schema / language_span annotations
  → sync_from_label_studio.py → gold JSON on disk
  → evaluation consumes PageLanguageMap spans
```

## Annotation Package (`annotation/`)

| Path | Role |
|------|------|
| `label_studio/setup_ner_projects.py` | Create LS projects from page inventory |
| `label_studio/sync_from_label_studio.py` | Pull completed annotations → gold files |
| `label_studio/label_studio_ner.py` | NER task config helpers |
| `label_studio/span_schema.py` | Re-exports `schemas/language_span.py` |
| `labelers/script_labeler.py` | Script-detection labeling |
| `labelers/tier2_labeler.py` | Tier-2 recovery labeling |
| `labelers/tier2_recovery.py` | Recovery heuristics |
| `labelers/labeler_common.py` | Shared labeler utilities |

## Legacy Label Studio Root (`label-studio/`)

Parallel copy of setup/sync scripts with workspace configs under `workspaces/`, example configs under `examples/`.

Prefer `annotation/` for current work; `label-studio/` retained for existing deployments.

## Gold Export Scripts (`scripts/`)

| Script | Output |
|--------|--------|
| `export_label_studio_gold.py` | Stage 1 gold flat/TSV from LS exports |
| `export_google_sheet.py` | Sheet → gold conversion |
| `flatten_stage1_gold.py` | Normalize gold to flat format |

## Span Schema Contract

`schemas/language_span.py`:
- `LanguageSpan`, `PageLanguageMap` — per-character language/script labels
- Consumed by `evaluation/stage1/per_language_quality.py` for per-language metrics

## Component Hierarchy (Label Studio)

```
Project (per dictionary or task type)
  └─ Tasks (one per page image)
       └─ Annotations
            └─ Spans (language/script NER labels)
```

## No Frontend Stack

No React/Vue/HTML app in repo. All operator interaction is via:
1. Label Studio web UI (external install)
2. CLI commands (`mudidi run`, eval CLIs, annotation scripts)
