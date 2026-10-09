<!-- Updated: 2026-10-10 -->

# Frontend and UI Architecture

MUDIDI has two browser surfaces: the built-in local production dashboard and
the separately operated Label Studio annotation workflow.

## Local production dashboard

The dashboard is server-rendered Jinja HTML with a small JavaScript and CSS
layer. It runs on FastAPI without a Node.js frontend build.

```text
home.html (New Run)
  → POST /runs/preview
  → review.html
  → POST /runs/{run_id}/start
  → run detail, pages, guide review, logs, outputs, and usage
```

The New Run form accepts exactly one dictionary PDF. The PDF and dictionary
page specification are required. Page fields accept one page, a range,
comma-separated pages, or combinations; server validation checks them against
the PDF page count before rendering the review. Missing or invalid fields are
shown in red with associated error text.

| Path | Role |
|------|------|
| `src/mudidi/web/templates/_layout.html` | Shared shell: sidebar navigation, theme toggle, and stylesheets |
| `src/mudidi/web/templates/_workspace_tabs.html` | Run workspace tab bar shared by every run page |
| `src/mudidi/web/templates/home.html` | Six-step New Run wizard, help text, and inline validation |
| `src/mudidi/web/templates/review.html` | Validated pre-run summary |
| `src/mudidi/web/templates/run_detail.html` | Run overview and actions |
| `src/mudidi/web/templates/parse_rules.html` | MDF parsing-guide checkpoint |
| `src/mudidi/web/templates/pages.html` | Processed-page navigation |
| `src/mudidi/web/templates/page_detail.html` | Source and editable generated text |
| `src/mudidi/web/templates/logs.html` | Bounded, redacted diagnostics |
| `src/mudidi/web/templates/outputs.html` | Validated artifact downloads |
| `src/mudidi/web/templates/usage.html` | Token and cost reporting |
| `src/mudidi/web/static/app.js` | Form behavior, help, model controls, and live updates |
| `src/mudidi/web/static/app.css` | Responsive layout and validation states |
| `src/mudidi/web/static/modernist-ochre.css` | Modernist Ochre theme layer, loaded after `app.css` |
| `src/mudidi/web/static/theme.js` | Light/dark theme toggle |
| `src/mudidi/web/static/fonts/` | Self-hosted Archivo, IBM Plex Mono, and Inter |
| `src/mudidi/web/folder_picker.py` | Native output-folder dialog for non-container runs |

Page-image and directory source inputs are intentionally absent from the
dashboard. They remain CLI/YAML features.

## Label Studio annotation workflow

```text
Dictionary page images
  → Label Studio NER projects (annotation/label_studio/)
  → span_schema / language_span annotations
  → sync_from_label_studio.py → gold JSON on disk
  → evaluation consumes PageLanguageMap spans
```

| Path | Role |
|------|------|
| `annotation/label_studio/setup_ner_projects.py` | Create Label Studio projects |
| `annotation/label_studio/sync_from_label_studio.py` | Pull completed annotations into gold files |
| `annotation/label_studio/label_studio_ner.py` | NER task configuration helpers |
| `annotation/labelers/` | Script labeling and tier-2 recovery |
| `schemas/language_span.py` | `LanguageSpan` and `PageLanguageMap` contracts |

The legacy `label-studio/` root is retained for existing deployments; prefer
`annotation/` for current annotation work.
