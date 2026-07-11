# YAML configuration reference

Generated from MUDIDI's strict Pydantic configuration models. Choose the
template matching your command's `kind`; unknown keys are rejected.

Each template is exhaustive: it shows optional fields and mutually exclusive
alternatives together so that every available key is discoverable. Remove keys
you do not use, especially one side of the sweep and evaluation alternatives.
Paths are resolved relative to the YAML file. API credentials belong in `.env`.

## How to read the templates

- Inline comments show each field's type, default, and numeric constraints.
- `null` means the field is optional and currently unset.
- `[]` and example mapping entries show the expected container shape.
- `source_config` is internal loader state and is intentionally omitted.

## Important validation rules

- `inference` requires `input.pages` and cannot use `stage1_source: gold`.
- `benchmark_run` requires one of `dataset_dir`, `samples_dir`, or `pages`.
- `vlm_ocr` and `mathpix_ocr` are Stage 1-only strategies.
- Evaluation uses either `predicted` + `gold` or `dataset_dir` + `pred_root`.
- A benchmark sweep uses exactly one of `axes` or `experiments`.

## `inference`

Production inference for a page directory or source PDF.

Run with `mudidi run --config CONFIG`.

```yaml
version: 1  # integer; default: 1
kind: "inference"  # string; default: "inference"
input:  # InputConfig; required
  pages: "path/to/pages"  # path | null; default: null
  dataset_dir: null  # path | null; default: null
  samples_dir: null  # path | null; default: null
  stage1_predictions_root: null  # path | null; default: null
  dictionary_pages: null  # string | null; default: null
  introduction: null  # path | null; default: null
  introduction_pages: null  # string | null; default: null
  alphabet: null  # path | null; default: null
  ocr_text: null  # path | null; default: null
  dictionary_languages: null  # path | null; default: null
  toolbox_pdf: null  # path | null; default: null
  languages: null  # list[string] | null; default: null
output:  # OutputConfig; required
  directory: "path/to/output"  # path; required
pipeline:  # PipelineConfig; optional
  stage: "all"  # one of "1", "2", "all", "2-pass-1", "2-pass-2"; default: "all"
  strategy: "two_stage"  # one of "two_stage", "vlm_ocr", "mathpix_ocr"; default: "two_stage"
  stage1_mode: "flat"  # one of "flat", "column"; default: "flat"
  stage1_input: "auto"  # one of "auto", "flat", "column"; default: "auto"
  stage1_source: "predictions"  # one of "gold", "predictions"; default: "predictions"
  stage1_typography: false  # boolean; default: false
  parse_rules_pages: []  # list[string]; optional
  parse_rules_file: null  # path | null; default: null
  parse_rules_gold: false  # boolean; default: false
  stage2_lexical_repair: false  # boolean; default: false
  stage1_guides: null  # path | null; default: null
  stage2_guides: null  # path | null; default: null
models:  # ModelsConfig; optional
  default: "gemini/gemini-3-flash-preview"  # string; default: "gemini/gemini-3-flash-preview"
  stage1: null  # string | null; default: null
  stage2_pass1: null  # string | null; default: null
  stage2_pass2: null  # string | null; default: null
  stage1_reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
  stage2_reasoning: "low"  # one of "low", "medium", "high"; default: "low"
  temperature: 0.1  # number; default: 0.1; >= 0.0
agentic:  # AgenticConfig; optional
  stage1: false  # boolean; default: false
  stage2: false  # boolean; default: false
  max_iterations: 2  # integer; default: 2; >= 0
  evaluator_model: null  # string | null; default: null
  rewriter_model: null  # string | null; default: null
  reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
  evaluator_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
  rewriter_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
  min_retry_confidence: 0.55  # number; default: 0.55; >= 0.0; <= 1.0
  verifier_patches: true  # boolean; default: true
  require_concrete_retry: true  # boolean; default: true
runtime:  # RuntimeConfig; optional
  batch_size: 1  # integer; default: 1; >= 1
  limit: null  # integer | null; default: null
  overwrite: false  # boolean; default: false
  prompt_cache: "auto"  # one of "auto", "off"; default: "auto"
  media_reference: "auto"  # one of "auto", "inline", "file-uri"; default: "auto"
  prompt_cache_key: null  # string | null; default: null
  experiment_name: "default"  # string; default: "default"
  stage2_experiment_name: null  # string | null; default: null
  stage1_output_subdir: "stage-1"  # string; default: "stage-1"
  one_page_per_entry: false  # boolean; default: false
  page_offset: 1  # integer; default: 1
  use_alphabet: false  # boolean; effective default: false
  use_ocr_hint: true  # boolean; default: true
  ocr_hint_experiment: null  # string | null; default: null
  use_introduction: true  # boolean; default: true
vlm:  # VlmConfig; optional
  model: null  # one of "mineru2.5-pro", "paddleocr-vl-1.5", "glm-ocr" | null; default: null
  dpi: 200  # integer; default: 200; >= 72
  mineru_batch_size: 8  # integer; default: 8; >= 1
  mineru_max_new_tokens: 1024  # integer; default: 1024; >= 1
  mineru_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
  paddle_rec_backend: "native"  # one of "native", "vllm-server"; default: "native"
  paddle_server_url: null  # string | null; default: null
  paddle_auto_server: true  # boolean; default: true
  paddle_server_port: 8765  # integer; default: 8765; >= 1; <= 65535
  paddle_server_python: null  # path | null; default: null
  glm_prompt: "Text Recognition:"  # string; default: "Text Recognition:"
  glm_max_new_tokens: 8192  # integer; default: 8192; >= 1
  glm_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
  glm_auto_server: true  # boolean; default: true
  glm_server_url: null  # string | null; default: null
  glm_server_port: 8081  # integer; default: 8081; >= 1; <= 65535
  glm_server_python: null  # path | null; default: null
mathpix:  # MathpixConfig; optional
  poll_interval_seconds: 3.0  # number; default: 3.0; > 0
  max_wait_seconds: 600.0  # number; default: 600.0; > 0
  request_timeout_seconds: 60.0  # number; default: 60.0; > 0
```

## `benchmark_run`

One benchmark extraction over a dataset, sample tree, or page input.

Run with `mudidi benchmark run --config CONFIG`.

```yaml
version: 1  # integer; default: 1
kind: "benchmark_run"  # string; default: "benchmark_run"
input:  # InputConfig; required
  pages: null  # path | null; default: null
  dataset_dir: "path/to/dataset"  # path | null; default: null
  samples_dir: null  # path | null; default: null
  stage1_predictions_root: null  # path | null; default: null
  dictionary_pages: null  # string | null; default: null
  introduction: null  # path | null; default: null
  introduction_pages: null  # string | null; default: null
  alphabet: null  # path | null; default: null
  ocr_text: null  # path | null; default: null
  dictionary_languages: null  # path | null; default: null
  toolbox_pdf: null  # path | null; default: null
  languages: null  # list[string] | null; default: null
output:  # OutputConfig; required
  directory: "path/to/output"  # path; required
pipeline:  # PipelineConfig; optional
  stage: "all"  # one of "1", "2", "all", "2-pass-1", "2-pass-2"; default: "all"
  strategy: "two_stage"  # one of "two_stage", "vlm_ocr", "mathpix_ocr"; default: "two_stage"
  stage1_mode: "flat"  # one of "flat", "column"; default: "flat"
  stage1_input: "auto"  # one of "auto", "flat", "column"; default: "auto"
  stage1_source: "gold"  # one of "gold", "predictions"; effective default: "gold"
  stage1_typography: false  # boolean; default: false
  parse_rules_pages: []  # list[string]; optional
  parse_rules_file: null  # path | null; default: null
  parse_rules_gold: false  # boolean; default: false
  stage2_lexical_repair: false  # boolean; default: false
  stage1_guides: null  # path | null; default: null
  stage2_guides: null  # path | null; default: null
models:  # ModelsConfig; optional
  default: "gemini/gemini-3-flash-preview"  # string; default: "gemini/gemini-3-flash-preview"
  stage1: null  # string | null; default: null
  stage2_pass1: null  # string | null; default: null
  stage2_pass2: null  # string | null; default: null
  stage1_reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
  stage2_reasoning: "low"  # one of "low", "medium", "high"; default: "low"
  temperature: 0.1  # number; default: 0.1; >= 0.0
agentic:  # AgenticConfig; optional
  stage1: false  # boolean; default: false
  stage2: false  # boolean; default: false
  max_iterations: 2  # integer; default: 2; >= 0
  evaluator_model: null  # string | null; default: null
  rewriter_model: null  # string | null; default: null
  reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
  evaluator_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
  rewriter_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
  min_retry_confidence: 0.55  # number; default: 0.55; >= 0.0; <= 1.0
  verifier_patches: true  # boolean; default: true
  require_concrete_retry: true  # boolean; default: true
runtime:  # RuntimeConfig; optional
  batch_size: 1  # integer; default: 1; >= 1
  limit: null  # integer | null; default: null
  overwrite: false  # boolean; default: false
  prompt_cache: "auto"  # one of "auto", "off"; default: "auto"
  media_reference: "auto"  # one of "auto", "inline", "file-uri"; default: "auto"
  prompt_cache_key: null  # string | null; default: null
  experiment_name: "default"  # string; default: "default"
  stage2_experiment_name: null  # string | null; default: null
  stage1_output_subdir: "stage-1"  # string; default: "stage-1"
  one_page_per_entry: false  # boolean; default: false
  page_offset: 1  # integer; default: 1
  use_alphabet: true  # boolean; default: true
  use_ocr_hint: true  # boolean; default: true
  ocr_hint_experiment: null  # string | null; default: null
  use_introduction: true  # boolean; default: true
vlm:  # VlmConfig; optional
  model: null  # one of "mineru2.5-pro", "paddleocr-vl-1.5", "glm-ocr" | null; default: null
  dpi: 200  # integer; default: 200; >= 72
  mineru_batch_size: 8  # integer; default: 8; >= 1
  mineru_max_new_tokens: 1024  # integer; default: 1024; >= 1
  mineru_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
  paddle_rec_backend: "native"  # one of "native", "vllm-server"; default: "native"
  paddle_server_url: null  # string | null; default: null
  paddle_auto_server: true  # boolean; default: true
  paddle_server_port: 8765  # integer; default: 8765; >= 1; <= 65535
  paddle_server_python: null  # path | null; default: null
  glm_prompt: "Text Recognition:"  # string; default: "Text Recognition:"
  glm_max_new_tokens: 8192  # integer; default: 8192; >= 1
  glm_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
  glm_auto_server: true  # boolean; default: true
  glm_server_url: null  # string | null; default: null
  glm_server_port: 8081  # integer; default: 8081; >= 1; <= 65535
  glm_server_python: null  # path | null; default: null
mathpix:  # MathpixConfig; optional
  poll_interval_seconds: 3.0  # number; default: 3.0; > 0
  max_wait_seconds: 600.0  # number; default: 600.0; > 0
  request_timeout_seconds: 60.0  # number; default: 60.0; > 0
```

## `benchmark_sweep`

A validated collection of benchmark runs expanded from axes or experiments.

Run with `mudidi benchmark sweep --config CONFIG`.

```yaml
version: 1  # integer; default: 1
kind: "benchmark_sweep"  # string; default: "benchmark_sweep"
name: "example-sweep"  # string; required; pattern: ^[A-Za-z0-9_.-]+$
base:  # BenchmarkRunConfig; required
  version: 1  # integer; default: 1
  kind: "benchmark_run"  # string; default: "benchmark_run"
  input:  # InputConfig; required
    pages: null  # path | null; default: null
    dataset_dir: "path/to/dataset"  # path | null; default: null
    samples_dir: null  # path | null; default: null
    stage1_predictions_root: null  # path | null; default: null
    dictionary_pages: null  # string | null; default: null
    introduction: null  # path | null; default: null
    introduction_pages: null  # string | null; default: null
    alphabet: null  # path | null; default: null
    ocr_text: null  # path | null; default: null
    dictionary_languages: null  # path | null; default: null
    toolbox_pdf: null  # path | null; default: null
    languages: null  # list[string] | null; default: null
  output:  # OutputConfig; required
    directory: "path/to/output"  # path; required
  pipeline:  # PipelineConfig; optional
    stage: "all"  # one of "1", "2", "all", "2-pass-1", "2-pass-2"; default: "all"
    strategy: "two_stage"  # one of "two_stage", "vlm_ocr", "mathpix_ocr"; default: "two_stage"
    stage1_mode: "flat"  # one of "flat", "column"; default: "flat"
    stage1_input: "auto"  # one of "auto", "flat", "column"; default: "auto"
    stage1_source: "gold"  # one of "gold", "predictions"; effective default: "gold"
    stage1_typography: false  # boolean; default: false
    parse_rules_pages: []  # list[string]; optional
    parse_rules_file: null  # path | null; default: null
    parse_rules_gold: false  # boolean; default: false
    stage2_lexical_repair: false  # boolean; default: false
    stage1_guides: null  # path | null; default: null
    stage2_guides: null  # path | null; default: null
  models:  # ModelsConfig; optional
    default: "gemini/gemini-3-flash-preview"  # string; default: "gemini/gemini-3-flash-preview"
    stage1: null  # string | null; default: null
    stage2_pass1: null  # string | null; default: null
    stage2_pass2: null  # string | null; default: null
    stage1_reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
    stage2_reasoning: "low"  # one of "low", "medium", "high"; default: "low"
    temperature: 0.1  # number; default: 0.1; >= 0.0
  agentic:  # AgenticConfig; optional
    stage1: false  # boolean; default: false
    stage2: false  # boolean; default: false
    max_iterations: 2  # integer; default: 2; >= 0
    evaluator_model: null  # string | null; default: null
    rewriter_model: null  # string | null; default: null
    reasoning: "low"  # one of "none", "low", "medium", "high"; default: "low"
    evaluator_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
    rewriter_reasoning: null  # one of "none", "low", "medium", "high" | null; default: null
    min_retry_confidence: 0.55  # number; default: 0.55; >= 0.0; <= 1.0
    verifier_patches: true  # boolean; default: true
    require_concrete_retry: true  # boolean; default: true
  runtime:  # RuntimeConfig; optional
    batch_size: 1  # integer; default: 1; >= 1
    limit: null  # integer | null; default: null
    overwrite: false  # boolean; default: false
    prompt_cache: "auto"  # one of "auto", "off"; default: "auto"
    media_reference: "auto"  # one of "auto", "inline", "file-uri"; default: "auto"
    prompt_cache_key: null  # string | null; default: null
    experiment_name: "default"  # string; default: "default"
    stage2_experiment_name: null  # string | null; default: null
    stage1_output_subdir: "stage-1"  # string; default: "stage-1"
    one_page_per_entry: false  # boolean; default: false
    page_offset: 1  # integer; default: 1
    use_alphabet: true  # boolean; default: true
    use_ocr_hint: true  # boolean; default: true
    ocr_hint_experiment: null  # string | null; default: null
    use_introduction: true  # boolean; default: true
  vlm:  # VlmConfig; optional
    model: null  # one of "mineru2.5-pro", "paddleocr-vl-1.5", "glm-ocr" | null; default: null
    dpi: 200  # integer; default: 200; >= 72
    mineru_batch_size: 8  # integer; default: 8; >= 1
    mineru_max_new_tokens: 1024  # integer; default: 1024; >= 1
    mineru_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
    paddle_rec_backend: "native"  # one of "native", "vllm-server"; default: "native"
    paddle_server_url: null  # string | null; default: null
    paddle_auto_server: true  # boolean; default: true
    paddle_server_port: 8765  # integer; default: 8765; >= 1; <= 65535
    paddle_server_python: null  # path | null; default: null
    glm_prompt: "Text Recognition:"  # string; default: "Text Recognition:"
    glm_max_new_tokens: 8192  # integer; default: 8192; >= 1
    glm_backend: "transformers"  # one of "transformers", "vllm"; default: "transformers"
    glm_auto_server: true  # boolean; default: true
    glm_server_url: null  # string | null; default: null
    glm_server_port: 8081  # integer; default: 8081; >= 1; <= 65535
    glm_server_python: null  # path | null; default: null
  mathpix:  # MathpixConfig; optional
    poll_interval_seconds: 3.0  # number; default: 3.0; > 0
    max_wait_seconds: 600.0  # number; default: 600.0; > 0
    request_timeout_seconds: 60.0  # number; default: 60.0; > 0
axes:  # mapping | null; default: null
  model:
    - id: "example-model"  # string; required; pattern: ^[A-Za-z0-9_.-]+$
      set:  # mapping; required
        models.stage1: "provider/model"
experiments:  # list[SweepChoice] | null; default: null
  - id: "value"  # string; required; pattern: ^[A-Za-z0-9_.-]+$
    set:  # mapping; required
      dotted.field.path: "value"
experiment_name: "{model}"  # string | null; default: null
name_field: "runtime.experiment_name"  # one of "runtime.experiment_name", "runtime.stage2_experiment_name"; default: "runtime.experiment_name"
exclude:  # list[mapping]; optional
  - example_key: "value"
sweep:  # SweepOptions; optional
  max_runs: 100  # integer; default: 100; >= 1
  failure_policy: "continue"  # one of "continue", "stop"; default: "continue"
```

## `stage1_evaluation`

Stage 1 evaluation for one file pair or a discovered experiment tree.

Run with `mudidi benchmark evaluate stage1 --config CONFIG`.

```yaml
version: 1  # integer; default: 1
kind: "stage1_evaluation"  # string; default: "stage1_evaluation"
input:  # EvaluationInputConfig; required
  predicted: "path/to/predicted"  # path | null; default: null
  gold: "path/to/gold"  # path | null; default: null
  dataset_dir: null  # path | null; default: null
  pred_root: null  # path | null; default: null
  languages: null  # list[string] | null; default: null
output:  # OutputConfig; required
  directory: "path/to/output"  # path; required
evaluation:  # EvaluationOptions; optional
  experiment_names: []  # list[string]; optional
  all_experiments: false  # boolean; default: false
  workers: 1  # integer; default: 1; >= 1
  per_language_script: false  # boolean; default: false
  character_alignment: "quick_match"  # one of "collapsed", "quick_match"; default: "quick_match"
  record_threshold: 0.6  # number; default: 0.6; >= 0.0; <= 1.0
  line_threshold: 0.7  # number; default: 0.7; >= 0.0; <= 1.0
  baseline_summary: null  # path | null; default: null
  baseline_experiment: null  # string | null; default: null
  comparison_output: null  # path | null; default: null
  marker_sub_list: null  # path | null; default: null
  dictionary_languages: null  # path | null; default: null
  experiment_name_contains: null  # string | null; default: null
  include_vlm_ocr: false  # boolean; default: false
  stage1_output_subdir: "stage-1"  # string; default: "stage-1"
  metrics: "minimal"  # one of "full", "minimal"; default: "minimal"
  alignment_threshold: 0.6  # number; default: 0.6; >= 0.0; <= 1.0
  overwrite: false  # boolean; default: false
```

## `stage2_evaluation`

Stage 2 MDF evaluation for one file pair or a discovered experiment tree.

Run with `mudidi benchmark evaluate stage2 --config CONFIG`.

```yaml
version: 1  # integer; default: 1
kind: "stage2_evaluation"  # string; default: "stage2_evaluation"
input:  # EvaluationInputConfig; required
  predicted: "path/to/predicted"  # path | null; default: null
  gold: "path/to/gold"  # path | null; default: null
  dataset_dir: null  # path | null; default: null
  pred_root: null  # path | null; default: null
  languages: null  # list[string] | null; default: null
output:  # OutputConfig; required
  directory: "path/to/output"  # path; required
evaluation:  # EvaluationOptions; optional
  experiment_names: []  # list[string]; optional
  all_experiments: false  # boolean; default: false
  workers: 1  # integer; default: 1; >= 1
  per_language_script: false  # boolean; default: false
  character_alignment: "quick_match"  # one of "collapsed", "quick_match"; default: "quick_match"
  record_threshold: 0.6  # number; default: 0.6; >= 0.0; <= 1.0
  line_threshold: 0.7  # number; default: 0.7; >= 0.0; <= 1.0
  baseline_summary: null  # path | null; default: null
  baseline_experiment: null  # string | null; default: null
  comparison_output: null  # path | null; default: null
  marker_sub_list: null  # path | null; default: null
  dictionary_languages: null  # path | null; default: null
  experiment_name_contains: null  # string | null; default: null
  include_vlm_ocr: false  # boolean; default: false
  stage1_output_subdir: "stage-1"  # string; default: "stage-1"
  metrics: "minimal"  # one of "full", "minimal"; default: "minimal"
  alignment_threshold: 0.6  # number; default: 0.6; >= 0.0; <= 1.0
  overwrite: false  # boolean; default: false
```
