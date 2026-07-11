# uv cheat sheet

This project is managed entirely with [`uv`](https://docs.astral.sh/uv/). Do not invoke `pip` or bare `python` for project work — console scripts and locked dependencies only resolve through the project virtualenv (`.venv/`).

**Python 3.11+** is required. On first `uv sync`, uv uses a suitable interpreter on your `PATH` or downloads one automatically.

## First time on a new machine

```bash
git clone <repo-url> && cd MUDIDI
uv sync                              # creates .venv/ and installs deps from uv.lock
cp .env.example .env                 # add LLM provider keys (see README)
```

Optional dependency groups:

```bash
uv sync --extra dev                  # pytest (for running tests)
uv sync --extra docs                 # MkDocs Material + mkdocstrings
uv sync --extra paddle               # paddlepaddle + paddleocr in main .venv (lightweight OCR only)
```

After `uv sync`, prefer:

```bash
uv run mudidi run --help             # no need to activate .venv
```

Or activate the venv:

```bash
source .venv/bin/activate
mudidi run --help
```

Always commit `uv.lock` alongside `pyproject.toml` so collaborators get an identical environment.

## CLI entry point

The public interface is the unified `mudidi` command.

| Command | Purpose |
| --- | --- |
| `mudidi run` | Production Stage 1 / Stage 2 inference |
| `mudidi benchmark run` | Benchmark extraction |
| `mudidi benchmark evaluate stage1` | Stage 1 evaluation |
| `mudidi benchmark evaluate stage2` | Stage 2 evaluation |
| `mudidi config validate` | Offline YAML validation |

```bash
uv run mudidi run --help
uv run mudidi benchmark run --help
uv run mudidi benchmark evaluate stage1 --help
```

See the [CLI migration guide](development/cli-migration.md) for removed command names.

### Inference (new dictionary)

```bash
uv run mudidi run \
  --pages my-dict/snippets \
  --output-dir my-dict/outputs \
  --dry-run
```

### Benchmark dataset (HF layout)

Download the gated dataset to `dataset/MUDIDI/`, then use the public examples:

```bash
bash examples/inference/run_directory_mode.sh
bash examples/evaluation/run_stage1_eval.sh
bash examples/evaluation/run_stage2_eval.sh
```

See the repository's `examples/README.md` for PDF mode, env overrides, and the full workflow.

## Ad-hoc scripts

Run repository scripts through `uv run` so they see the locked environment:

```bash
uv run python scripts/flatten_stage1_gold.py
uv run python scripts/generate_dictionary_languages_yaml.py --overwrite
uv run python scripts/ocr_to_stage1_flat.py --help
```

## Tests

```bash
uv sync --extra dev
uv run pytest
uv run pytest tests/path/to/test_file.py -v
```

Integration tests that call live LLM APIs require `MUDIDI_LLM_INTEGRATION=1`.

## Label Studio (separate venv)

Label Studio pins `openai` 1.x, which conflicts with the main project's `litellm>=1.87` (OpenAI SDK 2.x). Install it in an isolated venv:

```bash
uv venv .venv-label-studio
uv pip install -r label-studio/requirements.txt --python .venv-label-studio/bin/python
```

Run Label Studio with that interpreter:

```bash
uv run --python .venv-label-studio/bin/python label-studio --port 8080
uv run --python .venv-label-studio/bin/python python label-studio/setup.py --help
```

## Specialised VLM venvs

Document VLMs (MinerU, GLM-OCR, PaddleOCR-VL) ship heavy, mutually incompatible dependencies. Each backend runs in its own venv. The installer script lives in the local `examples-dev/helper/` tree (gitignored); run from a dev checkout:

```bash
uv sync
bash examples-dev/helper/install_models_venv.sh
bash examples-dev/helper/install_models_venv.sh mineru glmocr paddle
```

Default install creates symlinks under the project root:

| Venv | Backend |
| --- | --- |
| `.venv-mineru` | MinerU 2.5 Pro (transformers) |
| `.venv-mineru-vllm` | MinerU 2.5 Pro (vLLM in-process) |
| `.venv-glmocr` | GLM-OCR (transformers) |
| `.venv-glmocr-vllm` | GLM-OCR (vLLM server + client) |
| `.venv-paddleocr` | PaddleOCR-VL 1.5 |
| `.venv-paddle-vllm-server` | Paddle GenAI vLLM server |

Most new-dictionary workflows use `--strategy two_stage` with a general LLM and do **not** need these venvs.

## Managing dependencies

```bash
uv add some-package              # updates pyproject.toml + uv.lock
uv remove some-package
uv lock --upgrade-package some-package
uv lock --upgrade                # refresh entire lockfile
```

Do not edit `uv.lock` by hand.
