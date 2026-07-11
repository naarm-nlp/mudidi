# Evaluation

```bash
uv run mudidi benchmark evaluate stage1 \
  --config examples/configs/benchmark/stage1-evaluation.yaml

uv run mudidi benchmark evaluate stage2 \
  --config examples/configs/benchmark/stage2-evaluation.yaml
```

Each evaluator supports a single predicted/gold pair or dataset/prediction-root discovery. Stage 1 reports character, word, typography, and optional language-script metrics. Stage 2 reports record alignment and MDF field-value quality.
