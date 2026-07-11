<!-- Generated: 2026-07-11 | Files scanned: 255 | Token estimate: ~700 -->

# External Dependencies & Integrations

## Python Runtime

- **Python ≥3.11** (pyproject.toml)
- **Package manager**: [uv](https://docs.astral.sh/uv/) — `uv sync`, `uv run`
- **Build**: hatchling wheel from `src/mudidi`

## Core Python Packages

| Package | Usage |
|---------|-------|
| `litellm` | Unified LLM provider gateway (`llm/client.py`) |
| `pydantic` | Schemas, structured output validation |
| `python-dotenv` | `.env` API key loading |
| `pymupdf` | PDF render/split (fallback to pdftk) |
| `Pillow` | Image handling for LLM vision calls |
| `pandas` | Evaluation report DataFrames |
| `scikit-learn`, `scipy` | Metric computations |
| `python-Levenshtein`, `jiwer`, `sacrebleu`, `grapheme` | Text similarity & char metrics |
| `pyyaml` | dictionary_languages.yaml |
| `python-docx` | OCR hint docx parsing |

### Optional

| Extra | Packages | Purpose |
|-------|----------|---------|
| `dev` | pytest | Test runner |
| `paddle` | paddlepaddle, paddleocr | PaddleOCR-VL local inference |

## LLM Providers (via litellm)

Selected by model strings in `--model`, `--stage-1-model`, `--stage-2-pass-1-model`, and `--stage-2-pass-2-model`:

| Provider | Env Keys | Notes |
|----------|----------|-------|
| Google Gemini | `GEMINI_API_KEY` | Default; reasoning_effort → thinking_level |
| OpenRouter | `OPENROUTER_API_KEY` | Multi-provider routing; provider order via env |
| OpenAI | `OPENAI_API_KEY` | Direct or via OpenRouter |
| Anthropic | `ANTHROPIC_API_KEY` | Claude models |

Key env toggles: `OPENROUTER_PROVIDER_ORDER`, `GEMINI_MAX_RETRIES`, `LLM_RATE_LIMIT_REDUCE_CONCURRENCY`, `LITELLM_DEBUG`.

## OCR / VLM Backends

| Backend | Integration | CLI Key |
|---------|-------------|---------|
| Mathpix | `extraction/mathpix_ocr.py`, `ocr/mathpix_convert.py` | `pipeline.strategy: mathpix_ocr` |
| MinerU 2.5 Pro | `ocr/vlm/mineru.py` | `--vlm-model mineru2.5-pro` |
| PaddleOCR-VL 1.5 | `ocr/vlm/paddle_vl.py` | `--vlm-model paddleocr-vl-1.5` |
| GLM-OCR | `ocr/vlm/glm_ocr.py` | `--vlm-model glm-ocr` |

Local VLM servers: `paddle_genai_server.py`, `glm_vllm_server.py`.

## System Tools

| Tool | Required When | Install |
|------|---------------|---------|
| `pdftk` | `--pages` is a PDF | `pdftk-java` (apt/brew) |
| `git` | Clone repo | standard |
| Label Studio | Annotation workflow | external pip/docker install |

## External Services

| Service | Purpose | Config |
|---------|---------|--------|
| Mathpix API | OCR conversion | `MATHPIX_APP_ID`, `MATHPIX_APP_KEY` |
| Label Studio | Human annotation UI | `annotation/examples/setup_label_studio.sh` |
| Hugging Face Hub | VLM model weights | hf-cli / model IDs in registry |

## Shared Internal Libraries

No published sub-packages. All code lives in `src/mudidi/` with these import boundaries:

```
cli → config, extraction, evaluation, llm, ocr, schemas, utils
extraction → llm, schemas, agentic, evaluation.stage2.mdf_parser
evaluation → schemas, utils (no CLI imports)
ocr → schemas
```

## Test Dependencies

```bash
uv sync --extra dev
uv run pytest                    # unit tests
MUDIDI_LLM_INTEGRATION=1 uv run pytest -m integration  # live API
```

## Asset Bundling

`assets/PROMPT.json` force-included in wheel as `mudidi/assets/PROMPT.json` via hatch build config.
