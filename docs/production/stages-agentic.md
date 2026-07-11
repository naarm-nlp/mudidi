# Stages and agentic retries

Pipeline stage values are `1`, `2`, `all`, `2-pass-1`, and `2-pass-2`.

Stage 2 consists of parse-rule discovery followed by page-level MDF extraction. Supply representative `pipeline.parse_rules_pages`, or reuse a reviewed `pipeline.parse_rules_file`.

Agentic verification is opt-in:

```yaml
agentic:
  stage1: true
  stage2: true
  max_iterations: 2
  catastrophic_recovery: true
```

Stage 1 is grounded in the page image. Stage 2 is grounded in the Stage 1 transcript and parse rules. Catastrophic whole-page recovery applies only to Stage 1.
