# MUDIDI

**[Read the MUDIDI documentation](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/)**

MUDIDI digitizes scanned multilingual dictionaries with language models. It first creates a faithful page transcription and then converts that transcription into [SIL Toolbox MDF](https://software.sil.org/toolbox/) lexicon records.

```text
dictionary pages → Stage 1 OCR → Stage 2 parse rules → MDF records
```

## Installation

MUDIDI supports Linux, macOS, and Windows through WSL2. It requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Install `pdftk` only when processing a multi-page source PDF.

```bash
git clone https://github.com/DavidSamuell/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary.git
cd MUDIDI
uv sync
cp .env.example .env
```

For a browser-based production workflow with no YAML authoring:

```bash
uv sync --extra web
uv run mudidi web
```

The app opens on localhost and includes run monitoring plus a mandatory Stage 2
parse-rule review checkpoint. See the
**[local web application guide](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/production/local-web-app/)**.

## API setup

Add the key for the provider used by your model to `.env`:

```dotenv
GEMINI_API_KEY=replace-me
# OPENAI_API_KEY=replace-me
# ANTHROPIC_API_KEY=replace-me
# OPEN_ROUTER_API_KEY=replace-me
```

Never place credentials in YAML run configuration.

## Quick end-to-end inference

Process a directory containing page images or individual page PDFs:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary
```

Inspect the selected defaults without making API calls:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --dry-run
```

Enable bounded verifier-rewriter retries directly when needed:

```bash
uv run mudidi run \
  --pages path/to/dictionary-pages \
  --output-dir outputs/my-dictionary \
  --stage1-agentic \
  --stage2-agentic
```

Outputs are written under:

```text
outputs/my-dictionary/
├── resolved_config.json
├── mdf_parsing_guide.json
├── stage-1/page_N/page_N_stage1_flat.txt
└── stage-2/page_N/page_N.mdf.txt
```

For repeatable and advanced runs, use a validated YAML configuration:

```bash
uv run mudidi config validate examples/configs/production/directory-inference.yaml
uv run mudidi run --config examples/configs/production/directory-inference.yaml
```

## Documentation

The public documentation is available at
**[davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary](https://davidsamuell.github.io/MUDIDI-Pipeline-for-Digitization-of-Multilingual-Dictionary/)** and
separates two primary workflows:

- **Production Inference** — digitize your own PDF or page directory.
- **Benchmarking & Evaluation** — reproduce experiments against the MUDIDI dataset.

The site is rebuilt and deployed automatically from `main` with GitHub Pages.
Documentation sources are under [`docs/`](docs/), and canonical configurations
are under [`examples/configs/`](examples/configs/).

## Dataset and paper

The benchmark contains 30 public-domain multilingual dictionaries. The dataset is available through [Hugging Face](https://huggingface.co/datasets/DavidSamuell/mudidi).

```bibtex
@misc{mudidi2026,
  title         = {{MUDIDI: A Two-Stage Framework for Multilingual Dictionary Digitization with Language Models}},
  author        = {Setiawan, David and Khishigsuren, Temuulen and Agarwal, Milind and Pit, Pagnarith and Mahmudi, Aso and Vylomova, Ekaterina},
  year          = {2026},
  eprint        = {2606.09435},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  doi           = {10.48550/arXiv.2606.09435},
  url           = {https://arxiv.org/abs/2606.09435}
}
```
