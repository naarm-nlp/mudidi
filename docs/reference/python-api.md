# Python API

These pages are maintainer-facing interfaces, not a separately versioned third-party SDK.

::: mudidi.config.yaml_config
    options:
      members:
        - InferenceConfig
        - BenchmarkRunConfig
        - Stage1EvaluationConfig
        - Stage2EvaluationConfig
        - load_yaml_config
        - merge_explicit_overrides

::: mudidi.agentic.verifier_loop
    options:
      members:
        - AgenticLoopConfig
        - AgenticVerifierDecision
        - run_bounded_verifier_loop
