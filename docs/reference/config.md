# Configuration schema

Generated from the versioned Pydantic configuration union.

```json
{
  "$defs": {
    "AgenticConfig": {
      "additionalProperties": false,
      "description": "Optional verifier-rewriter controls.",
      "properties": {
        "catastrophic_recovery": {
          "default": false,
          "title": "Catastrophic Recovery",
          "type": "boolean"
        },
        "evaluator_model": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Evaluator Model"
        },
        "evaluator_reasoning": {
          "anyOf": [
            {
              "enum": [
                "none",
                "low",
                "medium",
                "high"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Evaluator Reasoning"
        },
        "max_iterations": {
          "default": 2,
          "minimum": 0,
          "title": "Max Iterations",
          "type": "integer"
        },
        "max_patches_per_attempt": {
          "default": 16,
          "minimum": 1,
          "title": "Max Patches Per Attempt",
          "type": "integer"
        },
        "min_retry_confidence": {
          "default": 0.55,
          "maximum": 1.0,
          "minimum": 0.0,
          "title": "Min Retry Confidence",
          "type": "number"
        },
        "reasoning": {
          "default": "low",
          "enum": [
            "none",
            "low",
            "medium",
            "high"
          ],
          "title": "Reasoning",
          "type": "string"
        },
        "require_concrete_retry": {
          "default": true,
          "title": "Require Concrete Retry",
          "type": "boolean"
        },
        "rewriter_model": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Rewriter Model"
        },
        "rewriter_reasoning": {
          "anyOf": [
            {
              "enum": [
                "none",
                "low",
                "medium",
                "high"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Rewriter Reasoning"
        },
        "stage1": {
          "default": false,
          "title": "Stage1",
          "type": "boolean"
        },
        "stage2": {
          "default": false,
          "title": "Stage2",
          "type": "boolean"
        },
        "verifier_patches": {
          "default": true,
          "title": "Verifier Patches",
          "type": "boolean"
        }
      },
      "title": "AgenticConfig",
      "type": "object"
    },
    "BenchmarkRunConfig": {
      "additionalProperties": false,
      "description": "Benchmark extraction configuration.",
      "properties": {
        "agentic": {
          "$ref": "#/$defs/AgenticConfig"
        },
        "input": {
          "$ref": "#/$defs/InputConfig"
        },
        "kind": {
          "const": "benchmark_run",
          "default": "benchmark_run",
          "title": "Kind",
          "type": "string"
        },
        "mathpix": {
          "$ref": "#/$defs/MathpixConfig"
        },
        "models": {
          "$ref": "#/$defs/ModelsConfig"
        },
        "output": {
          "$ref": "#/$defs/OutputConfig"
        },
        "pipeline": {
          "$ref": "#/$defs/PipelineConfig"
        },
        "runtime": {
          "$ref": "#/$defs/RuntimeConfig"
        },
        "source_config": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source Config"
        },
        "version": {
          "const": 1,
          "default": 1,
          "title": "Version",
          "type": "integer"
        },
        "vlm": {
          "$ref": "#/$defs/VlmConfig"
        }
      },
      "required": [
        "input",
        "output"
      ],
      "title": "BenchmarkRunConfig",
      "type": "object"
    },
    "BenchmarkSweepConfig": {
      "additionalProperties": false,
      "description": "A typed collection of benchmark runs expanded from axes or a list.",
      "properties": {
        "axes": {
          "anyOf": [
            {
              "additionalProperties": {
                "items": {
                  "$ref": "#/$defs/SweepChoice"
                },
                "type": "array"
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Axes"
        },
        "base": {
          "$ref": "#/$defs/BenchmarkRunConfig"
        },
        "exclude": {
          "items": {
            "additionalProperties": {
              "type": "string"
            },
            "type": "object"
          },
          "title": "Exclude",
          "type": "array"
        },
        "experiment_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Experiment Name"
        },
        "experiments": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/SweepChoice"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Experiments"
        },
        "kind": {
          "const": "benchmark_sweep",
          "default": "benchmark_sweep",
          "title": "Kind",
          "type": "string"
        },
        "name": {
          "pattern": "^[A-Za-z0-9_.-]+$",
          "title": "Name",
          "type": "string"
        },
        "name_field": {
          "default": "runtime.experiment_name",
          "enum": [
            "runtime.experiment_name",
            "runtime.stage2_experiment_name"
          ],
          "title": "Name Field",
          "type": "string"
        },
        "source_config": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source Config"
        },
        "sweep": {
          "$ref": "#/$defs/SweepOptions"
        },
        "version": {
          "const": 1,
          "default": 1,
          "title": "Version",
          "type": "integer"
        }
      },
      "required": [
        "name",
        "base"
      ],
      "title": "BenchmarkSweepConfig",
      "type": "object"
    },
    "EvaluationInputConfig": {
      "additionalProperties": false,
      "properties": {
        "dataset_dir": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dataset Dir"
        },
        "gold": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Gold"
        },
        "languages": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Languages"
        },
        "pred_root": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Pred Root"
        },
        "predicted": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Predicted"
        }
      },
      "title": "EvaluationInputConfig",
      "type": "object"
    },
    "EvaluationOptions": {
      "additionalProperties": false,
      "properties": {
        "alignment_threshold": {
          "default": 0.6,
          "maximum": 1.0,
          "minimum": 0.0,
          "title": "Alignment Threshold",
          "type": "number"
        },
        "all_experiments": {
          "default": false,
          "title": "All Experiments",
          "type": "boolean"
        },
        "baseline_experiment": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Baseline Experiment"
        },
        "baseline_summary": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Baseline Summary"
        },
        "character_alignment": {
          "default": "quick_match",
          "enum": [
            "collapsed",
            "quick_match"
          ],
          "title": "Character Alignment",
          "type": "string"
        },
        "comparison_output": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Comparison Output"
        },
        "dictionary_languages": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dictionary Languages"
        },
        "experiment_name_contains": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Experiment Name Contains"
        },
        "experiment_names": {
          "items": {
            "type": "string"
          },
          "title": "Experiment Names",
          "type": "array"
        },
        "include_vlm_ocr": {
          "default": false,
          "title": "Include Vlm Ocr",
          "type": "boolean"
        },
        "line_threshold": {
          "default": 0.7,
          "maximum": 1.0,
          "minimum": 0.0,
          "title": "Line Threshold",
          "type": "number"
        },
        "marker_sub_list": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Marker Sub List"
        },
        "metrics": {
          "default": "minimal",
          "enum": [
            "full",
            "minimal"
          ],
          "title": "Metrics",
          "type": "string"
        },
        "overwrite": {
          "default": false,
          "title": "Overwrite",
          "type": "boolean"
        },
        "per_language_script": {
          "default": false,
          "title": "Per Language Script",
          "type": "boolean"
        },
        "record_threshold": {
          "default": 0.6,
          "maximum": 1.0,
          "minimum": 0.0,
          "title": "Record Threshold",
          "type": "number"
        },
        "stage1_output_subdir": {
          "default": "stage-1",
          "title": "Stage1 Output Subdir",
          "type": "string"
        },
        "workers": {
          "default": 1,
          "minimum": 1,
          "title": "Workers",
          "type": "integer"
        }
      },
      "title": "EvaluationOptions",
      "type": "object"
    },
    "InferenceConfig": {
      "additionalProperties": false,
      "description": "Production inference configuration.",
      "properties": {
        "agentic": {
          "$ref": "#/$defs/AgenticConfig"
        },
        "input": {
          "$ref": "#/$defs/InputConfig"
        },
        "kind": {
          "const": "inference",
          "default": "inference",
          "title": "Kind",
          "type": "string"
        },
        "mathpix": {
          "$ref": "#/$defs/MathpixConfig"
        },
        "models": {
          "$ref": "#/$defs/ModelsConfig"
        },
        "output": {
          "$ref": "#/$defs/OutputConfig"
        },
        "pipeline": {
          "$ref": "#/$defs/PipelineConfig"
        },
        "runtime": {
          "$ref": "#/$defs/RuntimeConfig"
        },
        "source_config": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source Config"
        },
        "version": {
          "const": 1,
          "default": 1,
          "title": "Version",
          "type": "integer"
        },
        "vlm": {
          "$ref": "#/$defs/VlmConfig"
        }
      },
      "required": [
        "input",
        "output"
      ],
      "title": "InferenceConfig",
      "type": "object"
    },
    "InputConfig": {
      "additionalProperties": false,
      "description": "Input paths and page selection shared by extraction commands.",
      "properties": {
        "alphabet": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Alphabet"
        },
        "dataset_dir": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dataset Dir"
        },
        "dictionary_languages": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dictionary Languages"
        },
        "dictionary_pages": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dictionary Pages"
        },
        "introduction": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Introduction"
        },
        "introduction_pages": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Introduction Pages"
        },
        "languages": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Languages"
        },
        "ocr_text": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ocr Text"
        },
        "pages": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Pages"
        },
        "samples_dir": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Samples Dir"
        },
        "stage1_predictions_root": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage1 Predictions Root"
        },
        "toolbox_pdf": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Toolbox Pdf"
        }
      },
      "title": "InputConfig",
      "type": "object"
    },
    "MathpixConfig": {
      "additionalProperties": false,
      "description": "Mathpix Convert API polling and timeout controls.",
      "properties": {
        "max_wait_seconds": {
          "default": 600.0,
          "exclusiveMinimum": 0,
          "title": "Max Wait Seconds",
          "type": "number"
        },
        "poll_interval_seconds": {
          "default": 3.0,
          "exclusiveMinimum": 0,
          "title": "Poll Interval Seconds",
          "type": "number"
        },
        "request_timeout_seconds": {
          "default": 60.0,
          "exclusiveMinimum": 0,
          "title": "Request Timeout Seconds",
          "type": "number"
        }
      },
      "title": "MathpixConfig",
      "type": "object"
    },
    "ModelsConfig": {
      "additionalProperties": false,
      "description": "Model ids and reasoning controls for each pipeline step.",
      "properties": {
        "default": {
          "default": "gemini/gemini-3-flash-preview",
          "title": "Default",
          "type": "string"
        },
        "stage1": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage1"
        },
        "stage1_reasoning": {
          "default": "low",
          "enum": [
            "none",
            "low",
            "medium",
            "high"
          ],
          "title": "Stage1 Reasoning",
          "type": "string"
        },
        "stage2_pass1": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage2 Pass1"
        },
        "stage2_pass2": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage2 Pass2"
        },
        "stage2_reasoning": {
          "default": "low",
          "enum": [
            "low",
            "medium",
            "high"
          ],
          "title": "Stage2 Reasoning",
          "type": "string"
        },
        "temperature": {
          "default": 0.1,
          "minimum": 0.0,
          "title": "Temperature",
          "type": "number"
        }
      },
      "title": "ModelsConfig",
      "type": "object"
    },
    "OutputConfig": {
      "additionalProperties": false,
      "description": "Output root for one command invocation.",
      "properties": {
        "directory": {
          "format": "path",
          "title": "Directory",
          "type": "string"
        }
      },
      "required": [
        "directory"
      ],
      "title": "OutputConfig",
      "type": "object"
    },
    "PipelineConfig": {
      "additionalProperties": false,
      "description": "Stage selection and two-stage extraction behavior.",
      "properties": {
        "parse_rules_file": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Parse Rules File"
        },
        "parse_rules_gold": {
          "default": false,
          "title": "Parse Rules Gold",
          "type": "boolean"
        },
        "parse_rules_pages": {
          "items": {
            "type": "string"
          },
          "title": "Parse Rules Pages",
          "type": "array"
        },
        "stage": {
          "default": "all",
          "enum": [
            "1",
            "2",
            "all",
            "2-pass-1",
            "2-pass-2"
          ],
          "title": "Stage",
          "type": "string"
        },
        "stage1_guides": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage1 Guides"
        },
        "stage1_input": {
          "default": "auto",
          "enum": [
            "auto",
            "flat",
            "column"
          ],
          "title": "Stage1 Input",
          "type": "string"
        },
        "stage1_mode": {
          "default": "flat",
          "enum": [
            "flat",
            "column"
          ],
          "title": "Stage1 Mode",
          "type": "string"
        },
        "stage1_source": {
          "default": "predictions",
          "enum": [
            "gold",
            "predictions"
          ],
          "title": "Stage1 Source",
          "type": "string"
        },
        "stage1_typography": {
          "default": false,
          "title": "Stage1 Typography",
          "type": "boolean"
        },
        "stage2_guides": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage2 Guides"
        },
        "stage2_lexical_repair": {
          "default": false,
          "title": "Stage2 Lexical Repair",
          "type": "boolean"
        },
        "strategy": {
          "default": "two_stage",
          "enum": [
            "two_stage",
            "vlm_ocr",
            "mathpix_ocr"
          ],
          "title": "Strategy",
          "type": "string"
        }
      },
      "title": "PipelineConfig",
      "type": "object"
    },
    "RuntimeConfig": {
      "additionalProperties": false,
      "description": "Execution, caching, resume, and experiment settings.",
      "properties": {
        "batch_size": {
          "default": 1,
          "minimum": 1,
          "title": "Batch Size",
          "type": "integer"
        },
        "experiment_name": {
          "default": "default",
          "title": "Experiment Name",
          "type": "string"
        },
        "limit": {
          "anyOf": [
            {
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Limit"
        },
        "media_reference": {
          "default": "auto",
          "enum": [
            "auto",
            "inline",
            "file-uri"
          ],
          "title": "Media Reference",
          "type": "string"
        },
        "ocr_hint_experiment": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ocr Hint Experiment"
        },
        "one_page_per_entry": {
          "default": false,
          "title": "One Page Per Entry",
          "type": "boolean"
        },
        "overwrite": {
          "default": false,
          "title": "Overwrite",
          "type": "boolean"
        },
        "page_offset": {
          "default": 1,
          "title": "Page Offset",
          "type": "integer"
        },
        "prompt_cache": {
          "default": "auto",
          "enum": [
            "auto",
            "off"
          ],
          "title": "Prompt Cache",
          "type": "string"
        },
        "prompt_cache_key": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Prompt Cache Key"
        },
        "stage1_output_subdir": {
          "default": "stage-1",
          "title": "Stage1 Output Subdir",
          "type": "string"
        },
        "stage2_experiment_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Stage2 Experiment Name"
        },
        "use_alphabet": {
          "default": true,
          "title": "Use Alphabet",
          "type": "boolean"
        },
        "use_introduction": {
          "default": true,
          "title": "Use Introduction",
          "type": "boolean"
        },
        "use_ocr_hint": {
          "default": true,
          "title": "Use Ocr Hint",
          "type": "boolean"
        }
      },
      "title": "RuntimeConfig",
      "type": "object"
    },
    "Stage1EvaluationConfig": {
      "additionalProperties": false,
      "properties": {
        "evaluation": {
          "$ref": "#/$defs/EvaluationOptions"
        },
        "input": {
          "$ref": "#/$defs/EvaluationInputConfig"
        },
        "kind": {
          "const": "stage1_evaluation",
          "default": "stage1_evaluation",
          "title": "Kind",
          "type": "string"
        },
        "output": {
          "$ref": "#/$defs/OutputConfig"
        },
        "source_config": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source Config"
        },
        "version": {
          "const": 1,
          "default": 1,
          "title": "Version",
          "type": "integer"
        }
      },
      "required": [
        "input",
        "output"
      ],
      "title": "Stage1EvaluationConfig",
      "type": "object"
    },
    "Stage2EvaluationConfig": {
      "additionalProperties": false,
      "properties": {
        "evaluation": {
          "$ref": "#/$defs/EvaluationOptions"
        },
        "input": {
          "$ref": "#/$defs/EvaluationInputConfig"
        },
        "kind": {
          "const": "stage2_evaluation",
          "default": "stage2_evaluation",
          "title": "Kind",
          "type": "string"
        },
        "output": {
          "$ref": "#/$defs/OutputConfig"
        },
        "source_config": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source Config"
        },
        "version": {
          "const": 1,
          "default": 1,
          "title": "Version",
          "type": "integer"
        }
      },
      "required": [
        "input",
        "output"
      ],
      "title": "Stage2EvaluationConfig",
      "type": "object"
    },
    "SweepChoice": {
      "additionalProperties": false,
      "description": "One named axis choice or explicit benchmark experiment.",
      "properties": {
        "id": {
          "pattern": "^[A-Za-z0-9_.-]+$",
          "title": "Id",
          "type": "string"
        },
        "set": {
          "additionalProperties": true,
          "title": "Set",
          "type": "object"
        }
      },
      "required": [
        "id",
        "set"
      ],
      "title": "SweepChoice",
      "type": "object"
    },
    "SweepOptions": {
      "additionalProperties": false,
      "description": "Execution guards for an expanded benchmark sweep.",
      "properties": {
        "failure_policy": {
          "default": "continue",
          "enum": [
            "continue",
            "stop"
          ],
          "title": "Failure Policy",
          "type": "string"
        },
        "max_runs": {
          "default": 100,
          "minimum": 1,
          "title": "Max Runs",
          "type": "integer"
        }
      },
      "title": "SweepOptions",
      "type": "object"
    },
    "VlmConfig": {
      "additionalProperties": false,
      "description": "Advanced local OCR/VLM backend settings.",
      "properties": {
        "dpi": {
          "default": 200,
          "minimum": 72,
          "title": "Dpi",
          "type": "integer"
        },
        "glm_auto_server": {
          "default": true,
          "title": "Glm Auto Server",
          "type": "boolean"
        },
        "glm_backend": {
          "default": "transformers",
          "enum": [
            "transformers",
            "vllm"
          ],
          "title": "Glm Backend",
          "type": "string"
        },
        "glm_max_new_tokens": {
          "default": 8192,
          "minimum": 1,
          "title": "Glm Max New Tokens",
          "type": "integer"
        },
        "glm_prompt": {
          "default": "Text Recognition:",
          "title": "Glm Prompt",
          "type": "string"
        },
        "glm_server_port": {
          "default": 8081,
          "maximum": 65535,
          "minimum": 1,
          "title": "Glm Server Port",
          "type": "integer"
        },
        "glm_server_python": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Glm Server Python"
        },
        "glm_server_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Glm Server Url"
        },
        "mineru_backend": {
          "default": "transformers",
          "enum": [
            "transformers",
            "vllm"
          ],
          "title": "Mineru Backend",
          "type": "string"
        },
        "mineru_batch_size": {
          "default": 8,
          "minimum": 1,
          "title": "Mineru Batch Size",
          "type": "integer"
        },
        "mineru_max_new_tokens": {
          "default": 1024,
          "minimum": 1,
          "title": "Mineru Max New Tokens",
          "type": "integer"
        },
        "model": {
          "anyOf": [
            {
              "enum": [
                "mineru2.5-pro",
                "paddleocr-vl-1.5",
                "glm-ocr"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Model"
        },
        "paddle_auto_server": {
          "default": true,
          "title": "Paddle Auto Server",
          "type": "boolean"
        },
        "paddle_rec_backend": {
          "default": "native",
          "enum": [
            "native",
            "vllm-server"
          ],
          "title": "Paddle Rec Backend",
          "type": "string"
        },
        "paddle_server_port": {
          "default": 8765,
          "maximum": 65535,
          "minimum": 1,
          "title": "Paddle Server Port",
          "type": "integer"
        },
        "paddle_server_python": {
          "anyOf": [
            {
              "format": "path",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Paddle Server Python"
        },
        "paddle_server_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Paddle Server Url"
        }
      },
      "title": "VlmConfig",
      "type": "object"
    }
  },
  "discriminator": {
    "mapping": {
      "benchmark_run": "#/$defs/BenchmarkRunConfig",
      "benchmark_sweep": "#/$defs/BenchmarkSweepConfig",
      "inference": "#/$defs/InferenceConfig",
      "stage1_evaluation": "#/$defs/Stage1EvaluationConfig",
      "stage2_evaluation": "#/$defs/Stage2EvaluationConfig"
    },
    "propertyName": "kind"
  },
  "oneOf": [
    {
      "$ref": "#/$defs/InferenceConfig"
    },
    {
      "$ref": "#/$defs/BenchmarkRunConfig"
    },
    {
      "$ref": "#/$defs/BenchmarkSweepConfig"
    },
    {
      "$ref": "#/$defs/Stage1EvaluationConfig"
    },
    {
      "$ref": "#/$defs/Stage2EvaluationConfig"
    }
  ]
}
```
