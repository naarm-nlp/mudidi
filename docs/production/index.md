# Production inference

`mudidi run` digitizes a dictionary supplied as a page directory or source PDF. Production mode uses Stage 1 predictions as Stage 2's authoritative text and can use neighboring pages from the same run for context.

Use the minimal CLI for a quick run or a `kind: inference` YAML file for model, agentic, cache, parse-rule, and runtime controls.
