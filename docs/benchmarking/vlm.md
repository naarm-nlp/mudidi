# Advanced VLM backends

MinerU 2.5 Pro, PaddleOCR-VL 1.5, and GLM-OCR remain supported for Stage 1 benchmark extraction. Configure them through `pipeline.strategy: vlm_ocr` and the `vlm` YAML section.

These backends use separate environments and may start local inference servers. See `docs/uv.md` for environment setup. They are intentionally excluded from the production quickstart.

Mathpix is represented separately with `pipeline.strategy: mathpix_ocr`. It
requires `MATHPIX_APP_ID` and `MATHPIX_APP_KEY` in `.env`. The typed runner
writes the normal Stage 1 flat output and publishes Markdown under
`ocr-hints/<experiment>/`, allowing a later sweep experiment to reference it
through `runtime.ocr_hint_experiment`.
