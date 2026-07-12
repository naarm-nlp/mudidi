# Production inference

## Overview

`mudidi run` digitizes a dictionary supplied as a page directory or source PDF. Production mode uses Stage 1 predictions as Stage 2's authoritative text and can use neighboring pages from the same run for context.

Use the minimal CLI for a quick run or a `kind: inference` YAML file for model,
agentic, cache, MDF parsing guide, and runtime controls. The YAML compatibility
keys retain the internal `parse_rules` name.

If you prefer a graphical workflow, use the [local web application](local-web-app.md).
It exposes common production settings directly and pauses for explicit MDF
parsing guide review before Stage 2 extraction.

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
Install `pdftk` before processing a multi-page PDF.

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

Agentic verification is opt-in and can be enabled directly from the CLI:

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

When a verifier decision mixes exact patches with issues that require model
rewriting, MUDIDI applies the unambiguous patches first and passes the patched
output plus only the unresolved issues to the rewriter. The combined correction
uses one `max_iterations` slot. Decisions containing only successful patches do
not call the rewriter. There is no per-attempt patch-count limit; every
unambiguous patch in the verifier decision is attempted.

Stage 1 is grounded in the page image. Stage 2 is grounded in the Stage 1
transcript and reviewed MDF parsing guide. Stage 1 catastrophic whole-page recovery is always
available when its verifier identifies a wrong-page, hallucinated, or broadly
corrupted transcript; it does not require a separate option.

## Output layout

```text
output/
├── resolved_config.json
├── parse-rules.json
├── run_usage.json
├── stage-1/page_N/
│   ├── page_N_stage1_flat.txt
│   └── page_N_usage.json
└── stage-2/page_N/
    ├── page_N.mdf.txt
    └── page_N_usage.json
```

Existing stage-level `run_config.json` manifests retain their resume semantics.
`resolved_config.json` records the redacted configuration used to start the
invocation.
