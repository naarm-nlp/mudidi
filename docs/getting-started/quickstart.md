# Quickstart

Run the complete production pipeline on a directory of page images:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary
```

Inspect the fully resolved defaults without calling a model:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --dry-run
```

Stage 1 output appears under `stage-1/`; MDF output appears under `stage-2/`.
