# Production inference

## Overview

`mudidi run` digitizes a dictionary supplied as a page directory or source PDF. Production mode uses Stage 1 predictions as Stage 2's authoritative text. Each page is parsed on its own, with the end of the previous page's transcript as context, and the per-page MDF is joined into one dictionary file.

Use the minimal CLI for a quick run or a `kind: inference` YAML file for model,
agentic, cache, MDF parsing guide, and runtime controls. The YAML compatibility
keys retain the internal `parse_rules` name.

If you prefer a graphical workflow, use the
[local web application](local-web-app.md). Its responsive six-step wizard
supports dictionary PDFs, typed or uploaded stage instructions, model and
agentic settings, saved presets, and explicit MDF parsing-guide review before
Stage 2 extraction.

## Quick run

Run the complete production pipeline on a directory of page images:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary
```

Add `--dry-run` to inspect the resolved inputs, models, stages, and output paths
without calling a model or writing inference outputs.

## Input formats

### Directory input

Place one image or PDF per page in a directory. Numeric page stems determine
ordering, then run the canonical directory configuration:

```bash
uv run mudidi run --config examples/configs/production/directory-inference.yaml
```

```yaml
--8<-- "examples/configs/production/directory-inference.yaml"
```

### PDF input

PDF mode selects 1-based dictionary and introduction pages from one source scan.
PyMuPDF extracts the selected pages automatically.

```bash
uv run mudidi run --config examples/configs/production/pdf-inference.yaml
```

```yaml
--8<-- "examples/configs/production/pdf-inference.yaml"
```

## Pipeline stages

Pipeline stage values are `1`, `2`, `all`, `2-pass-1`, and `2-pass-2`.

Stage 2 consists of MDF parsing guide inference followed by page-level MDF
extraction. Supply representative `pipeline.parse_rules_pages`, or reuse a
reviewed `pipeline.parse_rules_file`; these internal YAML names are preserved for
compatibility.

## Agentic retries

Agentic loop is opt-in and can be enabled directly from the CLI:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --stage1-agentic \
  --stage2-agentic \
  --agentic-max-iterations 2
```

The same settings can be stored in YAML for repeatable runs:

```yaml
agentic:
  stage1: true
  stage2: true
  max_iterations: 2
```

Every Boolean agentic option has an explicit negative form. For example,
`--no-stage1-agentic` and `--no-agentic-verifier-patches` can override values
enabled in YAML. Model, reasoning, and retry-confidence options are listed
under the agentic group in the
[CLI reference](../reference/cli.md#mudidi-run).

After each page, the evaluator model chooses one action:

| Action | Meaning |
| --- | --- |
| `accept` | The output is good enough; the loop ends. |
| `targeted_edits` | Specific lines are wrong. The evaluator lists each edit: the line, the exact current text, the replacement, and a reason. |
| `full_redo` | The page is from the wrong page, largely hallucinated, or wrong on most lines. Stage 1 only: the page is re-transcribed from the image. |
| `reject` | Correction is unsafe; the current output is kept. |

Edits are never applied by code. For `targeted_edits`, the rewriter model
receives the proposed edits, verifies each one against the source, applies the
ones it confirms, and skips the rest. The evaluator then checks the result
again. Each correction uses one `max_iterations` slot.

The loop keeps the current output and stops early when the evaluator's
confidence is below `min_retry_confidence`, when it proposes no usable edit
(`invalid_decision`), when it repeats the same edits (`repeated_issue`), or
when the rewriter confirms none of them (`unchanged`). The stop reason is
recorded in `agentic/<stage>/final_decision.json` under each page directory.

Stage 1 is grounded in the page image. Stage 2 is grounded in the Stage 1
transcript and reviewed MDF parsing guide. The `agentic.verifier_patches` and
`agentic.require_concrete_retry` settings, and their CLI flags, are deprecated
and ignored.

## Output layout

```text
output/
├── resolved_config.json
├── mdf_parsing_guide.json
├── dictionary.mdf.txt
├── dictionary.mdf.report.json
├── run_usage.json
├── stage-1/page_N/
│   ├── page_N_stage1_flat.txt
│   └── page_N_usage.json
└── stage-2/page_N/
    ├── page_N.mdf.txt
    └── page_N_usage.json
```

### Entries that cross a page break

Stage 2 parses each page locally: a page's MDF holds exactly the lines printed
on that page. When an entry runs over a page break, the first page's file ends
mid-entry and the next page's file opens with the remaining fields, with no
`\lx` line above them. The model sees the last 1,500 characters of the
previous page's Stage 1 transcript, as text only, so it can give those opening
lines the right markers. It never sees the next page.

`dictionary.mdf.txt` appends the page files in page order, which re-attaches
those opening fields to the entry they belong to. `dictionary.mdf.report.json`
lists every page that starts mid-entry (`pages_starting_mid_entry`) so the
joins can be spot-checked. If the previous page is missing from the run, the
fields are kept as a separate block and `joined_to_previous_page` is `false`.
Both files are rebuilt at the end of every Stage 2 run and whenever a page's
MDF is edited in the dashboard.

Existing stage-level `run_config.json` manifests retain their resume semantics.
`resolved_config.json` records the redacted configuration used to start the
invocation.
