# annotation/ — gold language span-map labeling

This workspace **produces** the per-page language span maps (`*_lang.json`,
`PageLanguageMap`) that the package evaluation consumes. The evaluation itself
lives in the package (`src/mudidi/evaluation/stage1/` +
`src/mudidi/schemas/language_span.py`), not here — `annotation/` is the labeling
process only. Dependency direction is **annotation → mudidi** (never the reverse).

## Layout

| Path | What |
|------|------|
| `labelers/` | The two-tier gold labelers (the deliverable). |
| `labelers/labeler_common.py` | Shared discovery, paths, and span helpers. |
| `labelers/script_check.py` | Unicode script classifier (`assign_char_script_labels`). |
| `labelers/script_labeler.py` | Deterministic script-only span maps (optional). |
| `labelers/tier2_recovery.py` | LLM tag-injection → `PageLanguageMap` recovery. |
| `labelers/tier2_labeler.py` | Default LLM labeler — Language-Script compound tags. |
| `label_studio/` | Label Studio NER bridge. |
| `label_studio/label_studio_ner.py` | `PageLanguageMap` ⇄ Label Studio NER task import/export. |
| `label_studio/span_schema.py` | Compat shim re-exporting `mudidi.schemas.language_span` for flat imports. |
| `examples/` | Runnable shell scripts (see below). |
| `examples/run_labeler.sh` | Run the script labeler over dictionaries → `outputs/`. |
| `examples/start_label_studio_local.sh` | Start the local Label Studio dashboard for NER review. |
| `examples/start_label_studio_public.sh` | Start Label Studio with an automatic public ngrok tunnel. |
| `outputs/` | Generated `*_lang.json` span maps, one subfolder per dictionary (matches the dataset folder names). |
| `spikes/` | Exploratory only — not part of the pipeline. |
| `spikes/lid_spike.py` | LID-vs-script-check coverage spike (needs `lingua`). |
| `docs/` | Write-ups and decisions (no code). |
| `docs/mapping.md` | Manual 30-dictionary script/language map. |
| `docs/routing.md` | Tier-1 vs Tier-2 routing decision (9 / 21). |
| `docs/SPIKE_FINDINGS.md` | LID spike conclusions. |
| `tests/` | Unit tests; `conftest.py` puts `labelers/` and `label_studio/` on `sys.path`. |

The labeler modules use **flat sibling imports** (`from script_check import ...`)
resolved via `sys.path.insert(parent)`, so co-dependent modules stay in the same
subfolder. Tests resolve the same flat names through `tests/conftest.py`.

## Common commands

```bash
# LLM Language-Script labeler (default)
uv run python annotation/labelers/tier2_labeler.py
uv run python annotation/labelers/tier2_labeler.py --dictionary Japanese-English --limit 1

# Deterministic script-only (no LLM)
uv run python annotation/labelers/script_labeler.py --dictionary Greek-English

# Tests (not in the default pytest testpaths — point pytest at this dir)
uv run pytest annotation/tests/
```
