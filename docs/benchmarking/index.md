# Benchmarking and evaluation

Benchmark workflows use the MUDIDI dataset, named experiment slots, gold Stage 1/MDF artifacts, and independent pages without production neighbor context.

```bash
uv run mudidi benchmark sweep \
  --config examples/configs/benchmark/stage1-full-sweep.yaml \
  --dry-run
```
