# CLI reference

Generated from the public argparse tree.

## `mudidi`

```text
usage: mudidi [-h] {run,benchmark,config,web} ...

Dictionary OCR and MDF extraction (inference and benchmark modes).

positional arguments:
  {run,benchmark,config,web}
    run                 Run production inference.
    benchmark           Benchmark workflows.
    config              Configuration utilities.
    web                 Run the local production website.

options:
  -h, --help            show this help message and exit
```

## `mudidi run`

```text
usage: mudidi run [-h] [--config CONFIG] [--pages PAGES]
                  [--dict-pages DICT_PAGES] [--intro INTRO]
                  [--intro-pages INTRO_PAGES] [--alphabet ALPHABET]
                  [--ocr-text OCR_TEXT] [--toolbox-pdf TOOLBOX_PDF]
                  [--output-dir OUTPUT_DIR]
                  [--stage {1,2,all,2-pass-1,2-pass-2}] [--model MODEL]
                  [--stage-1-model STAGE_1_MODEL]
                  [--stage-2-pass-1-model STAGE_2_PASS_1_MODEL]
                  [--stage-2-pass-2-model STAGE_2_PASS_2_MODEL] [--overwrite]
                  [--dry-run] [--stage1-agentic | --no-stage1-agentic]
                  [--stage2-agentic | --no-stage2-agentic]
                  [--agentic-max-iterations AGENTIC_MAX_ITERATIONS]
                  [--agentic-evaluator-model AGENTIC_EVALUATOR_MODEL]
                  [--agentic-rewriter-model AGENTIC_REWRITER_MODEL]
                  [--agentic-reasoning {none,low,medium,high}]
                  [--agentic-evaluator-reasoning {none,low,medium,high}]
                  [--agentic-rewriter-reasoning {none,low,medium,high}]
                  [--agentic-min-retry-confidence AGENTIC_MIN_RETRY_CONFIDENCE]
                  [--agentic-verifier-patches | --no-agentic-verifier-patches]
                  [--agentic-concrete-retry-gate | --no-agentic-concrete-retry-gate]

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --pages PAGES
  --dict-pages DICT_PAGES
  --intro INTRO
  --intro-pages INTRO_PAGES
  --alphabet ALPHABET
  --ocr-text OCR_TEXT
  --toolbox-pdf TOOLBOX_PDF
  --output-dir OUTPUT_DIR
  --stage {1,2,all,2-pass-1,2-pass-2}
  --model MODEL
  --stage-1-model STAGE_1_MODEL
  --stage-2-pass-1-model STAGE_2_PASS_1_MODEL
  --stage-2-pass-2-model STAGE_2_PASS_2_MODEL
  --overwrite
  --dry-run

agentic verifier-rewriter options:
  --stage1-agentic, --no-stage1-agentic
                        Enable or disable bounded Stage 1 verification and
                        rewriting.
  --stage2-agentic, --no-stage2-agentic
                        Enable or disable bounded Stage 2 verification and
                        rewriting.
  --agentic-max-iterations AGENTIC_MAX_ITERATIONS
                        Maximum rewrite attempts for each enabled agentic
                        stage.
  --agentic-evaluator-model AGENTIC_EVALUATOR_MODEL
                        Model used for verifier calls; defaults to the current
                        stage model.
  --agentic-rewriter-model AGENTIC_REWRITER_MODEL
                        Model used for correction calls; defaults to the
                        current stage model.
  --agentic-reasoning {none,low,medium,high}
                        Shared reasoning effort for verifier and rewriter
                        calls.
  --agentic-evaluator-reasoning {none,low,medium,high}
                        Verifier reasoning effort; overrides --agentic-
                        reasoning.
  --agentic-rewriter-reasoning {none,low,medium,high}
                        Rewriter reasoning effort; overrides --agentic-
                        reasoning.
  --agentic-min-retry-confidence AGENTIC_MIN_RETRY_CONFIDENCE
                        Minimum verifier confidence required before a rewrite.
  --agentic-verifier-patches, --no-agentic-verifier-patches
                        Enable or disable exact verifier patches before model
                        rewriting.
  --agentic-concrete-retry-gate, --no-agentic-concrete-retry-gate
                        Require or waive localized evidence before retrying.
```

## `mudidi benchmark`

```text
usage: mudidi benchmark [-h] {run,sweep,evaluate} ...

positional arguments:
  {run,sweep,evaluate}
    run                 Run benchmark extraction.
    sweep               Run a typed benchmark experiment sweep.
    evaluate            Evaluate predictions.

options:
  -h, --help            show this help message and exit
```

## `mudidi benchmark run`

```text
usage: mudidi benchmark run [-h] [--config CONFIG] [--pages PAGES]
                            [--dict-pages DICT_PAGES] [--intro INTRO]
                            [--intro-pages INTRO_PAGES] [--alphabet ALPHABET]
                            [--ocr-text OCR_TEXT]
                            [--dictionary-languages DICTIONARY_LANGUAGES]
                            [--toolbox-pdf TOOLBOX_PDF]
                            [--output-dir OUTPUT_DIR]
                            [--stage {1,2,all,2-pass-1,2-pass-2}]
                            [--model MODEL] [--stage-1-model STAGE_1_MODEL]
                            [--stage-2-pass-1-model STAGE_2_PASS_1_MODEL]
                            [--stage-2-pass-2-model STAGE_2_PASS_2_MODEL]
                            [--overwrite] [--dry-run]
                            [--stage1-agentic | --no-stage1-agentic]
                            [--stage2-agentic | --no-stage2-agentic]
                            [--agentic-max-iterations AGENTIC_MAX_ITERATIONS]
                            [--agentic-evaluator-model AGENTIC_EVALUATOR_MODEL]
                            [--agentic-rewriter-model AGENTIC_REWRITER_MODEL]
                            [--agentic-reasoning {none,low,medium,high}]
                            [--agentic-evaluator-reasoning {none,low,medium,high}]
                            [--agentic-rewriter-reasoning {none,low,medium,high}]
                            [--agentic-min-retry-confidence AGENTIC_MIN_RETRY_CONFIDENCE]
                            [--agentic-verifier-patches | --no-agentic-verifier-patches]
                            [--agentic-concrete-retry-gate | --no-agentic-concrete-retry-gate]
                            [--dataset-dir DATASET_DIR]
                            [--samples-dir SAMPLES_DIR]
                            [--languages LANGUAGES [LANGUAGES ...]]
                            [--experiment-name EXPERIMENT_NAME]

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --pages PAGES
  --dict-pages DICT_PAGES
  --intro INTRO
  --intro-pages INTRO_PAGES
  --alphabet ALPHABET
  --ocr-text OCR_TEXT
  --dictionary-languages DICTIONARY_LANGUAGES
                        Legacy benchmark language metadata file.
  --toolbox-pdf TOOLBOX_PDF
  --output-dir OUTPUT_DIR
  --stage {1,2,all,2-pass-1,2-pass-2}
  --model MODEL
  --stage-1-model STAGE_1_MODEL
  --stage-2-pass-1-model STAGE_2_PASS_1_MODEL
  --stage-2-pass-2-model STAGE_2_PASS_2_MODEL
  --overwrite
  --dry-run
  --dataset-dir DATASET_DIR
  --samples-dir SAMPLES_DIR
  --languages LANGUAGES [LANGUAGES ...]
  --experiment-name EXPERIMENT_NAME

agentic verifier-rewriter options:
  --stage1-agentic, --no-stage1-agentic
                        Enable or disable bounded Stage 1 verification and
                        rewriting.
  --stage2-agentic, --no-stage2-agentic
                        Enable or disable bounded Stage 2 verification and
                        rewriting.
  --agentic-max-iterations AGENTIC_MAX_ITERATIONS
                        Maximum rewrite attempts for each enabled agentic
                        stage.
  --agentic-evaluator-model AGENTIC_EVALUATOR_MODEL
                        Model used for verifier calls; defaults to the current
                        stage model.
  --agentic-rewriter-model AGENTIC_REWRITER_MODEL
                        Model used for correction calls; defaults to the
                        current stage model.
  --agentic-reasoning {none,low,medium,high}
                        Shared reasoning effort for verifier and rewriter
                        calls.
  --agentic-evaluator-reasoning {none,low,medium,high}
                        Verifier reasoning effort; overrides --agentic-
                        reasoning.
  --agentic-rewriter-reasoning {none,low,medium,high}
                        Rewriter reasoning effort; overrides --agentic-
                        reasoning.
  --agentic-min-retry-confidence AGENTIC_MIN_RETRY_CONFIDENCE
                        Minimum verifier confidence required before a rewrite.
  --agentic-verifier-patches, --no-agentic-verifier-patches
                        Enable or disable exact verifier patches before model
                        rewriting.
  --agentic-concrete-retry-gate, --no-agentic-concrete-retry-gate
                        Require or waive localized evidence before retrying.
```

## `mudidi benchmark sweep`

```text
usage: mudidi benchmark sweep [-h] --config CONFIG [--experiment EXPERIMENT]
                              [--select SELECT] [--max-runs MAX_RUNS]
                              [--dry-run]

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --experiment EXPERIMENT
  --select SELECT
  --max-runs MAX_RUNS
  --dry-run
```

## `mudidi benchmark evaluate`

```text
usage: mudidi benchmark evaluate [-h] {stage1,stage2} ...

positional arguments:
  {stage1,stage2}

options:
  -h, --help       show this help message and exit
```

## `mudidi benchmark evaluate stage1`

```text
usage: mudidi benchmark evaluate stage1 [-h] [--config CONFIG]
                                        [--predicted PREDICTED] [--gold GOLD]
                                        [--dataset-dir DATASET_DIR]
                                        [--pred-root PRED_ROOT]
                                        [--samples-dir SAMPLES_DIR]
                                        [--output-dir OUTPUT_DIR]
                                        [--languages LANGUAGES [LANGUAGES ...]]
                                        [--experiment-name EXPERIMENT_NAME]
                                        [--all-experiments]
                                        [--experiment-name-contains EXPERIMENT_NAME_CONTAINS]
                                        [--include-vlm-ocr]
                                        [--stage1-output-subdir STAGE1_OUTPUT_SUBDIR]
                                        [--metrics {full,minimal}]
                                        [--alignment-threshold ALIGNMENT_THRESHOLD]
                                        [--character-alignment {collapsed,quick_match}]
                                        [--per-language-script] [--overwrite]
                                        [--workers WORKERS]

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --predicted PREDICTED, -p PREDICTED
  --gold GOLD, -g GOLD
  --dataset-dir DATASET_DIR
  --pred-root PRED_ROOT
  --samples-dir SAMPLES_DIR
  --output-dir OUTPUT_DIR, -o OUTPUT_DIR
  --languages LANGUAGES [LANGUAGES ...]
  --experiment-name EXPERIMENT_NAME
  --all-experiments
  --experiment-name-contains EXPERIMENT_NAME_CONTAINS
  --include-vlm-ocr
  --stage1-output-subdir STAGE1_OUTPUT_SUBDIR
  --metrics {full,minimal}
  --alignment-threshold ALIGNMENT_THRESHOLD
  --character-alignment {collapsed,quick_match}
  --per-language-script
  --overwrite
  --workers WORKERS
```

## `mudidi benchmark evaluate stage2`

```text
usage: mudidi benchmark evaluate stage2 [-h] [--config CONFIG]
                                        [--predicted PREDICTED] [--gold GOLD]
                                        [--dataset-dir DATASET_DIR]
                                        [--pred-root PRED_ROOT]
                                        [--samples-dir SAMPLES_DIR]
                                        [--output-dir OUTPUT_DIR]
                                        [--languages LANGUAGES [LANGUAGES ...]]
                                        [--experiment-name EXPERIMENT_NAME]
                                        [--all-experiments]
                                        [--baseline-summary BASELINE_SUMMARY]
                                        [--baseline-experiment BASELINE_EXPERIMENT]
                                        [--comparison-output COMPARISON_OUTPUT]
                                        [--record-threshold RECORD_THRESHOLD]
                                        [--line-threshold LINE_THRESHOLD]
                                        [--marker-sub-list MARKER_SUB_LIST]
                                        [--dictionary-languages DICTIONARY_LANGUAGES]

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --predicted PREDICTED, -p PREDICTED
  --gold GOLD, -g GOLD
  --dataset-dir DATASET_DIR
  --pred-root PRED_ROOT
  --samples-dir SAMPLES_DIR
  --output-dir OUTPUT_DIR, -o OUTPUT_DIR
  --languages LANGUAGES [LANGUAGES ...]
  --experiment-name EXPERIMENT_NAME
  --all-experiments
  --baseline-summary BASELINE_SUMMARY
  --baseline-experiment BASELINE_EXPERIMENT
  --comparison-output COMPARISON_OUTPUT
  --record-threshold RECORD_THRESHOLD
  --line-threshold LINE_THRESHOLD
  --marker-sub-list MARKER_SUB_LIST
  --dictionary-languages DICTIONARY_LANGUAGES
```

## `mudidi config`

```text
usage: mudidi config [-h] {validate} ...

positional arguments:
  {validate}
    validate  Validate a YAML config.

options:
  -h, --help  show this help message and exit
```

## `mudidi config validate`

```text
usage: mudidi config validate [-h] config

positional arguments:
  config

options:
  -h, --help  show this help message and exit
```

## `mudidi web`

```text
usage: mudidi web [-h] [--host {127.0.0.1,localhost}] [--port PORT]
                  [--data-dir DATA_DIR] [--container] [--no-browser]

options:
  -h, --help            show this help message and exit
  --host {127.0.0.1,localhost}
                        Loopback interface to bind (default: 127.0.0.1).
  --port PORT
  --data-dir DATA_DIR
  --container           Bind to the container network interface. Use only
                        inside a container whose published port is restricted
                        to host loopback.
  --no-browser          Do not open the website in the default browser.
```
