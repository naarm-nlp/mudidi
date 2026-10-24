# MUDIDI

MUDIDI turns scanned multilingual dictionaries into faithful transcriptions
and structured SIL Toolbox MDF records.

## Demo

<video controls playsinline preload="metadata"
       poster="assets/demo-poster.jpg"
       width="1920" height="1080"
       style="display: block; width: 100%; height: auto;"
       aria-label="MUDIDI dashboard demo">
  <source src="assets/mudidi-demo.mp4" type="video/mp4">
  <a href="assets/mudidi-demo.mp4">Watch the MUDIDI demo (MP4).</a>
</video>

Watch the 90-second dashboard walkthrough, or
[open the video directly](assets/mudidi-demo.mp4).


## Pipeline

MUDIDI digitizes scanned multilingual dictionaries with a two-stage language-model pipeline:

1. **Stage 1** transcribes each page faithfully.
2. **Stage 2** discovers dictionary structure and emits SIL Toolbox MDF records.

## Installation and quickstart

Install MUDIDI with [uv](getting-started/installation.md), configure the API key
for your model provider, and then follow the [quickstart](getting-started/quickstart.md)
to run the complete pipeline. Use the [YAML configuration guide](getting-started/configuration.md)
when you need to override the defaults.

## Production inference

Use [Production Inference](production/index.md) to digitize your own dictionary
from a [directory of page images](production/index.md#directory-input) or a
[source PDF](production/index.md#pdf-input). The production guide also covers
[stage selection](production/index.md#pipeline-stages),
[agentic retries](production/index.md#agentic-retries), and the
[generated output layout](production/index.md#output-layout).

## Benchmarking and evaluation

Use [Benchmarking & Evaluation](benchmarking/index.md) for reproducible dataset
experiments, multi-configuration sweeps, and Stage 1 or Stage 2 evaluation.
Start with the [extraction workflows](benchmarking/index.md#benchmark-extraction),
then review the [evaluation guide](benchmarking/index.md#evaluation). Specialized OCR and VLM
backends are documented separately under [Advanced VLM backends](benchmarking/vlm.md).

## Configuration

MUDIDI uses strict, versioned YAML configurations for advanced workflows. See
the [configuration guide](getting-started/configuration.md) for practical usage
and the generated [YAML configuration reference](reference/config.md) for every
field and subfield.

## Reference

Consult the [CLI reference](reference/cli.md) for the complete command tree and
the [Python API reference](reference/python-api.md) for maintainer-facing
interfaces. Contributors can continue with the
[architecture overview](development/architecture.md) and
[CLI migration guide](development/cli-migration.md).

See the [release notes](whats-new.md) for version history and upgrade guidance.
