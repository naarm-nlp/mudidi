"""``mudidi run`` command — inference and benchmark extraction."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Sequence, cast

from mudidi.config.run_config import RUN_STAGE_CHOICES, stage_from_cli
from mudidi.llm.prompt_store import configure_prompts, default_prompts_path
from mudidi.cli.model_args import forward_model_argv, register_model_arguments
from mudidi.utils.pdf_split import parse_page_spec

from mudidi.config.yaml_config import (
    BenchmarkRunConfig,
    BenchmarkSweepConfig,
    ConfigKind,
    InferenceConfig,
    MudidiConfig,
    Stage1EvaluationConfig,
    Stage2EvaluationConfig,
    load_yaml_config,
    merge_explicit_overrides,
    redacted_config_dict,
    validate_config_paths,
)


def register_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Register arguments for ``mudidi run``."""
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--benchmark",
        action="store_true",
        help="Benchmark mode: samples tree layout, gold defaults, no neighbor context.",
    )

    parser.add_argument(
        "--pages",
        dest="pages",
        help="Snippets directory or single source PDF (requires --dict-pages). "
        "Alias for --input-image.",
    )
    parser.add_argument(
        "--dict-pages",
        dest="dict_pages",
        help="When --pages is a PDF: 1-based dictionary pages to process "
        "(e.g. '1-10' or '1,3,5'). Required for PDF input; uses pdftk.",
    )
    parser.add_argument(
        "--input-image",
        dest="input_image",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Output directory. Inference: stage-1/ and stage-2/ subdirs. "
        "Alias for --output.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--samples-dir",
        dest="samples_dir",
        help="Benchmark: parent directory of per-language sample folders.",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=None,
        help="Benchmark: subset of language folders under --samples-dir.",
    )
    parser.add_argument(
        "--intro",
        help="Introduction directory or text/image file. Not used when --pages is a PDF "
        "(use --intro-pages instead).",
    )
    parser.add_argument(
        "--intro-pages",
        dest="intro_pages",
        help="When --pages is a PDF: 1-based introduction pages from that same PDF "
        "(e.g. '1-5'). Optional; uses pdftk.",
    )
    parser.add_argument(
        "--alphabet",
        help="Path to alphabet list (.txt or markdown) or alphabet image.",
    )
    parser.add_argument(
        "--ocr-text",
        dest="ocr_text",
        help="Directory of OCR hint files keyed by page stem (off by default).",
    )
    parser.add_argument(
        "--stage",
        choices=list(RUN_STAGE_CHOICES),
        default="all",
        help="Run Stage 1 only, Stage 2 (Pass 1 + Pass 2), both stages (all), "
        "Stage 2 Pass 1 only (2-pass-1), or Stage 2 Pass 2 only "
        "(2-pass-2; requires existing mdf_parsing_guide.json). Default: all.",
    )
    parser.add_argument(
        "--stage1-source",
        choices=["gold", "predictions"],
        default=None,
        dest="stage1_source",
        help="Stage 2 transcript source. Benchmark default: gold. Inference: predictions.",
    )
    parser.add_argument(
        "--parse-rules-page",
        action="append",
        dest="parse_rules_pages",
        help="1-based page number(s) for Stage 2 Pass 1 (same syntax as --dict-pages: "
        "e.g. '1', '1-4', '50,200'). Repeat the flag or comma-separate. "
        "Default: first page. Two+ pages use multi-sample Pass 1.",
    )
    parser.add_argument(
        "--parse-rules-file",
        type=str,
        dest="parse_rules_file",
        help="Load mdf_parsing_guide.json from PATH; skip Pass 1 LLM discovery. "
        "Always reads PATH (overrides any cached mdf_parsing_guide.json in --output-dir).",
    )
    parser.add_argument(
        "--dictionary-languages",
        type=str,
        dest="dictionary_languages",
        default=None,
        help="Optional path to dictionary_languages.yaml for Stage 2 Pass 1 "
        "(layout and source/target language hint). Inference: opt-in only. "
        "Benchmark: auto-loads per-entry YAML when omitted.",
    )
    parser.add_argument(
        "--prompt-cache",
        choices=["auto", "off"],
        default="auto",
        dest="prompt_cache",
        help="Prompt caching mode. auto uses litellm/provider prompt caching when "
        "supported; off sends uncached prompts (default: auto).",
    )
    parser.add_argument(
        "--media-reference",
        choices=["auto", "inline", "file-uri"],
        default="auto",
        dest="media_reference",
        help="How to attach reusable media such as toolbox PDFs. auto uses file "
        "parts/URIs when supported and falls back to inline data; inline always "
        "uses base64 data; file-uri prefers URI/file parts with inline fallback.",
    )
    parser.add_argument(
        "--prompt-cache-key",
        default=None,
        dest="prompt_cache_key",
        help="Optional stable cache key prefix for providers that accept cache "
        "routing hints (for example OpenAI prompt_cache_key).",
    )
    parser.add_argument(
        "--stage1-typography",
        action="store_true",
        dest="stage1_typography",
        help="Ask Stage 1 to annotate confident bold and italic text with <b>/<i> "
        "tags. By default Stage 1 emits plain OCR text.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        dest="batch_size",
        help="Concurrent page workers for two_stage LLM steps (default: 1). "
        "Thread pool over litellm.completion calls (no litellm batch API flag).",
    )
    parser.add_argument(
        "--stage1-agentic",
        action="store_true",
        dest="stage1_agentic",
        help="After each Stage 1 page output, run a bounded verifier-rewriter "
        "loop before saving the final Stage 1 artifact.",
    )
    parser.add_argument(
        "--stage2-agentic",
        action="store_true",
        dest="stage2_agentic",
        help="After each Stage 2 MDF page output, run a bounded verifier-rewriter "
        "loop before saving the final MDF artifact.",
    )
    parser.add_argument(
        "--agentic-max-iterations",
        type=int,
        default=2,
        dest="agentic_max_iterations",
        help="Maximum rewrite attempts for each enabled agentic stage after the "
        "initial stage output (default: 2).",
    )
    parser.add_argument(
        "--agentic-evaluator-model",
        default=None,
        dest="agentic_evaluator_model",
        help="Model for verifier calls. Defaults to the current stage model.",
    )
    parser.add_argument(
        "--agentic-rewriter-model",
        default=None,
        dest="agentic_rewriter_model",
        help="Model for correction calls. Defaults to the current stage model.",
    )
    parser.add_argument(
        "--agentic-reasoning",
        choices=["none", "low", "medium", "high"],
        default="low",
        dest="agentic_reasoning_effort",
        help="Reasoning effort for agentic verifier and rewriter calls "
        "(default: low).",
    )
    parser.add_argument(
        "--agentic-evaluator-reasoning",
        choices=["none", "low", "medium", "high"],
        default=None,
        dest="agentic_evaluator_reasoning_effort",
        help="Reasoning effort for agentic verifier/evaluator calls. Defaults "
        "to --agentic-reasoning when omitted.",
    )
    parser.add_argument(
        "--agentic-rewriter-reasoning",
        choices=["none", "low", "medium", "high"],
        default=None,
        dest="agentic_rewriter_reasoning_effort",
        help="Reasoning effort for agentic correction/rewrite calls. Defaults "
        "to --agentic-reasoning when omitted.",
    )
    parser.add_argument(
        "--agentic-min-retry-confidence",
        type=float,
        default=0.55,
        dest="agentic_min_retry_confidence",
        help="Minimum verifier confidence required before a retry can rewrite "
        "(default: 0.55).",
    )
    parser.add_argument(
        "--no-agentic-verifier-patches",
        action="store_true",
        dest="no_agentic_verifier_patches",
        help="Disable exact current_text→expected_text verifier patches before "
        "falling back to the correction model.",
    )
    parser.add_argument(
        "--no-agentic-concrete-retry-gate",
        action="store_true",
        dest="no_agentic_concrete_retry_gate",
        help="Allow retry decisions without localized evidence. Useful only for "
        "ablation; the default gate is safer.",
    )
    parser.add_argument(
        "--prompts-file",
        type=str,
        default=None,
        dest="prompts_file",
        help="Path to PROMPT.json (default: bundled assets).",
    )
    register_model_arguments(parser)


def _validate_pdf_page_args(run_args: argparse.Namespace) -> None:
    """Validate PDF page specs before delegating to the extract driver."""
    pages = run_args.pages or run_args.input_image
    pages_path = Path(pages) if pages else None
    is_source_pdf = bool(
        pages_path and pages_path.is_file() and pages_path.suffix.lower() == ".pdf"
    )

    if is_source_pdf:
        if not run_args.dict_pages:
            raise SystemExit(
                "--dict-pages is required when --pages is a PDF "
                "(e.g. '1-10' or '1,3,5')"
            )
        try:
            if not parse_page_spec(run_args.dict_pages):
                raise SystemExit("--dict-pages must list at least one page")
        except ValueError as exc:
            raise SystemExit(f"Invalid --dict-pages: {exc}") from exc
        if run_args.intro:
            raise SystemExit(
                "--intro cannot be used when --pages is a PDF; "
                "pass --intro-pages to select pages from the same PDF"
            )
        if run_args.intro_pages:
            try:
                if not parse_page_spec(run_args.intro_pages):
                    raise SystemExit("--intro-pages must list at least one page")
            except ValueError as exc:
                raise SystemExit(f"Invalid --intro-pages: {exc}") from exc
    else:
        if run_args.dict_pages:
            raise SystemExit(
                "--dict-pages is only valid when --pages is a single PDF file"
            )
        if run_args.intro_pages:
            raise SystemExit(
                "--intro-pages is only valid when --pages is a single PDF file"
            )


def _merge_extract_args(_run_args: argparse.Namespace, remaining: Sequence[str]) -> list[str]:
    """Forward unhandled flags to the legacy extract driver."""
    return list(remaining)


def run_from_args(run_args: argparse.Namespace, remaining: Sequence[str]) -> int:
    """Execute ``mudidi run`` by delegating to the extract driver."""
    pages = run_args.pages or run_args.input_image
    output = run_args.output_dir or run_args.output

    if run_args.benchmark:
        if not run_args.samples_dir and not pages:
            raise SystemExit("--benchmark requires --samples-dir or --pages with sample layout.")
    elif not pages or not output:
        raise SystemExit("Inference mode requires --pages and --output-dir.")

    _validate_pdf_page_args(run_args)

    if run_args.prompts_file:
        configure_prompts(run_args.prompts_file)
    else:
        configure_prompts(default_prompts_path())

    import sys

    from mudidi.cli import extract as extract_module

    argv = ["mudidi-run"]
    if run_args.benchmark:
        argv.append("--benchmark")
    if pages:
        argv.extend(["--pages", pages, "--input-image", pages])
    if output:
        argv.extend(["--output-dir", output, "--output", output])
    if run_args.samples_dir:
        argv.extend(["--samples-dir", run_args.samples_dir])
    if run_args.languages:
        argv.extend(["--languages", *run_args.languages])
    if run_args.intro:
        argv.extend(["--intro", run_args.intro])
    if run_args.intro_pages:
        argv.extend(["--intro-pages", run_args.intro_pages])
    if run_args.dict_pages:
        argv.extend(["--dict-pages", run_args.dict_pages])
    if run_args.alphabet:
        argv.extend(["--alphabet", run_args.alphabet])
    if run_args.ocr_text:
        argv.extend(["--ocr-text", run_args.ocr_text])
    stage = stage_from_cli(run_args.stage)
    argv.extend(["--stage", stage])
    if run_args.stage1_source:
        argv.extend(["--stage1-source", run_args.stage1_source])
    if run_args.parse_rules_pages:
        for stem in run_args.parse_rules_pages:
            argv.extend(["--parse-rules-page", stem])
    if run_args.parse_rules_file:
        argv.extend(["--parse-rules-file", run_args.parse_rules_file])
    if run_args.dictionary_languages:
        argv.extend(["--dictionary-languages", run_args.dictionary_languages])
    argv.extend(["--prompt-cache", run_args.prompt_cache])
    argv.extend(["--media-reference", run_args.media_reference])
    if run_args.prompt_cache_key:
        argv.extend(["--prompt-cache-key", run_args.prompt_cache_key])
    if run_args.stage1_typography:
        argv.append("--stage1-typography")
    if getattr(run_args, "batch_size", 1) != 1:
        argv.extend(["--batch-size", str(run_args.batch_size)])
    if run_args.stage1_agentic:
        argv.append("--stage1-agentic")
    if run_args.stage2_agentic:
        argv.append("--stage2-agentic")
    if run_args.agentic_max_iterations != 2:
        argv.extend(["--agentic-max-iterations", str(run_args.agentic_max_iterations)])
    if run_args.agentic_evaluator_model:
        argv.extend(["--agentic-evaluator-model", run_args.agentic_evaluator_model])
    if run_args.agentic_rewriter_model:
        argv.extend(["--agentic-rewriter-model", run_args.agentic_rewriter_model])
    if run_args.agentic_reasoning_effort != "low":
        argv.extend(["--agentic-reasoning", run_args.agentic_reasoning_effort])
    if run_args.agentic_evaluator_reasoning_effort is not None:
        argv.extend(
            [
                "--agentic-evaluator-reasoning",
                run_args.agentic_evaluator_reasoning_effort,
            ]
        )
    if run_args.agentic_rewriter_reasoning_effort is not None:
        argv.extend(
            [
                "--agentic-rewriter-reasoning",
                run_args.agentic_rewriter_reasoning_effort,
            ]
        )
    if run_args.agentic_min_retry_confidence != 0.55:
        argv.extend(
            [
                "--agentic-min-retry-confidence",
                str(run_args.agentic_min_retry_confidence),
            ]
        )
    if run_args.no_agentic_verifier_patches:
        argv.append("--no-agentic-verifier-patches")
    if run_args.no_agentic_concrete_retry_gate:
        argv.append("--no-agentic-concrete-retry-gate")
    if run_args.prompts_file:
        argv.extend(["--prompts-file", run_args.prompts_file])
    forward_model_argv(argv, run_args)
    argv.extend(_merge_extract_args(run_args, remaining))

    old_argv = sys.argv
    try:
        sys.argv = argv
        return extract_module.main()
    finally:
        sys.argv = old_argv


_RUN_OVERRIDE_PATHS = {
    "pages": "input.pages",
    "dict_pages": "input.dictionary_pages",
    "intro": "input.introduction",
    "intro_pages": "input.introduction_pages",
    "alphabet": "input.alphabet",
    "ocr_text": "input.ocr_text",
    "dictionary_languages": "input.dictionary_languages",
    "toolbox_pdf": "input.toolbox_pdf",
    "dataset_dir": "input.dataset_dir",
    "samples_dir": "input.samples_dir",
    "languages": "input.languages",
    "output_dir": "output.directory",
    "stage": "pipeline.stage",
    "model": "models.default",
    "stage_1_model": "models.stage1",
    "stage_2_pass_1_model": "models.stage2_pass1",
    "stage_2_pass_2_model": "models.stage2_pass2",
    "overwrite": "runtime.overwrite",
    "experiment_name": "runtime.experiment_name",
    "agentic_stage1": "agentic.stage1",
    "agentic_stage2": "agentic.stage2",
    "agentic_max_iterations": "agentic.max_iterations",
    "agentic_evaluator_model": "agentic.evaluator_model",
    "agentic_rewriter_model": "agentic.rewriter_model",
    "agentic_reasoning": "agentic.reasoning",
    "agentic_evaluator_reasoning": "agentic.evaluator_reasoning",
    "agentic_rewriter_reasoning": "agentic.rewriter_reasoning",
    "agentic_min_retry_confidence": "agentic.min_retry_confidence",
    "agentic_verifier_patches": "agentic.verifier_patches",
    "agentic_require_concrete_retry": "agentic.require_concrete_retry",
}

_EVALUATION_OVERRIDE_PATHS = {
    "predicted": "input.predicted",
    "gold": "input.gold",
    "dataset_dir": "input.dataset_dir",
    "pred_root": "input.pred_root",
    "languages": "input.languages",
    "output_dir": "output.directory",
    "experiment_name": "evaluation.experiment_names",
    "all_experiments": "evaluation.all_experiments",
    "experiment_name_contains": "evaluation.experiment_name_contains",
    "include_vlm_ocr": "evaluation.include_vlm_ocr",
    "stage1_output_subdir": "evaluation.stage1_output_subdir",
    "metrics": "evaluation.metrics",
    "alignment_threshold": "evaluation.alignment_threshold",
    "character_alignment": "evaluation.character_alignment",
    "per_language_script": "evaluation.per_language_script",
    "overwrite": "evaluation.overwrite",
    "workers": "evaluation.workers",
    "baseline_summary": "evaluation.baseline_summary",
    "baseline_experiment": "evaluation.baseline_experiment",
    "comparison_output": "evaluation.comparison_output",
    "record_threshold": "evaluation.record_threshold",
    "line_threshold": "evaluation.line_threshold",
    "marker_sub_list": "evaluation.marker_sub_list",
    "dictionary_languages": "evaluation.dictionary_languages",
}

_EVALUATION_PATH_OVERRIDES = {
    "predicted",
    "gold",
    "dataset_dir",
    "pred_root",
    "output_dir",
    "baseline_summary",
    "comparison_output",
    "marker_sub_list",
    "dictionary_languages",
}


def _namespace_values(args: argparse.Namespace) -> dict[str, Any]:
    return {key: value for key, value in vars(args).items() if not key.startswith("_")}


def _absolute_cli_path(value: Any) -> Any:
    if value is None or isinstance(value, Path):
        return value.resolve() if isinstance(value, Path) else None
    return Path(str(value)).expanduser().resolve()


def resolve_extraction_config(
    args: argparse.Namespace,
    *,
    kind: Literal["inference", "benchmark_run"],
) -> InferenceConfig | BenchmarkRunConfig:
    """Resolve defaults, YAML, and explicitly supplied CLI values."""

    values = _namespace_values(args)
    config_path = values.get("config")
    if config_path is not None:
        config = load_yaml_config(config_path, expected_kind=kind)
        if not isinstance(config, (InferenceConfig, BenchmarkRunConfig)):
            raise TypeError(f"{kind} is not an extraction configuration")
    else:
        input_data: dict[str, Any] = {}
        if "pages" in values:
            input_data["pages"] = _absolute_cli_path(values["pages"])
        if "dict_pages" in values:
            input_data["dictionary_pages"] = values["dict_pages"]
        for input_name in (
            "dataset_dir",
            "samples_dir",
            "intro",
            "alphabet",
            "ocr_text",
            "dictionary_languages",
            "toolbox_pdf",
        ):
            if input_name in values:
                field_name = "introduction" if input_name == "intro" else input_name
                input_data[field_name] = _absolute_cli_path(values[input_name])
        if "intro_pages" in values:
            input_data["introduction_pages"] = values["intro_pages"]
        if "languages" in values:
            input_data["languages"] = values["languages"]
        output = values.get("output_dir")
        if output is None:
            raise ValueError("--output-dir is required when --config is omitted")
        raw = {
            "version": 1,
            "kind": kind,
            "input": input_data,
            "output": {"directory": _absolute_cli_path(output)},
        }
        config_type = InferenceConfig if kind == "inference" else BenchmarkRunConfig
        config = config_type.model_validate(raw)

    overrides: dict[str, Any] = {}
    for cli_name, config_path_name in _RUN_OVERRIDE_PATHS.items():
        if cli_name not in values:
            continue
        value = values[cli_name]
        if cli_name in {
            "pages",
            "intro",
            "alphabet",
            "ocr_text",
            "dictionary_languages",
            "toolbox_pdf",
            "dataset_dir",
            "samples_dir",
            "output_dir",
        }:
            value = _absolute_cli_path(value)
        overrides[config_path_name] = value
    return cast(
        InferenceConfig | BenchmarkRunConfig,
        merge_explicit_overrides(config, overrides),
    )


def execution_namespace_from_config(
    config: InferenceConfig | BenchmarkRunConfig,
) -> argparse.Namespace:
    """Adapt a typed extraction config to the legacy orchestration namespace."""

    input_config = config.input
    pipeline = config.pipeline
    models = config.models
    agentic = config.agentic
    runtime = config.runtime
    vlm = config.vlm
    mathpix = config.mathpix
    pages = str(input_config.pages) if input_config.pages else None
    samples_dir = input_config.samples_dir or input_config.dataset_dir
    parse_rules_pages = list(pipeline.parse_rules_pages) or None
    return argparse.Namespace(
        resolved_config_snapshot=redacted_config_dict(config),
        input_image=pages,
        pages=pages,
        dict_pages=input_config.dictionary_pages,
        output=str(config.output.directory),
        output_dir=str(config.output.directory),
        samples_dir=str(samples_dir) if samples_dir else None,
        stage1_predictions_root=(
            str(input_config.stage1_predictions_root)
            if input_config.stage1_predictions_root
            else None
        ),
        languages=input_config.languages,
        intro=str(input_config.introduction) if input_config.introduction else None,
        intro_pages=input_config.introduction_pages,
        alphabet=str(input_config.alphabet) if input_config.alphabet else None,
        ocr_text=str(input_config.ocr_text) if input_config.ocr_text else None,
        dictionary_profile=input_config.dictionary_profile,
        dictionary_languages=(
            str(input_config.dictionary_languages)
            if input_config.dictionary_languages
            else None
        ),
        toolbox_pdf=input_config.toolbox_pdf,
        stage=pipeline.stage,
        strategy=pipeline.strategy,
        stage1_mode=pipeline.stage1_mode,
        stage1_input=pipeline.stage1_input,
        stage1_source=(
            pipeline.stage1_source if config.kind == "benchmark_run" else "predictions"
        ),
        stage1_typography=pipeline.stage1_typography,
        parse_rules_pages=parse_rules_pages,
        parse_rules_file=pipeline.parse_rules_file,
        parse_rules_gold=pipeline.parse_rules_gold,
        stage2_lexical_repair=pipeline.stage2_lexical_repair,
        stage1_guides_path=pipeline.stage1_guides,
        stage2_guides_path=pipeline.stage2_guides,
        model=models.default,
        stage_1_model=models.stage1,
        stage_2_pass_1_model=models.stage2_pass1,
        stage_2_pass_2_model=models.stage2_pass2,
        openrouter_provider=models.openrouter_provider,
        structure_model=None,
        stage1_reasoning_effort=models.stage1_reasoning,
        stage2_reasoning_effort=models.stage2_reasoning,
        stage2_pass1_reasoning_effort=(
            models.stage2_pass1_reasoning or models.stage2_reasoning
        ),
        stage2_pass2_reasoning_effort=(
            models.stage2_pass2_reasoning or models.stage2_reasoning
        ),
        temperature=models.temperature,
        stage1_agentic=agentic.stage1,
        stage2_agentic=agentic.stage2,
        agentic_max_iterations=agentic.max_iterations,
        agentic_evaluator_model=agentic.evaluator_model,
        agentic_rewriter_model=agentic.rewriter_model,
        agentic_reasoning_effort=agentic.reasoning,
        agentic_evaluator_reasoning_effort=agentic.evaluator_reasoning,
        agentic_rewriter_reasoning_effort=agentic.rewriter_reasoning,
        agentic_min_retry_confidence=agentic.min_retry_confidence,
        no_agentic_verifier_patches=not agentic.verifier_patches,
        no_agentic_concrete_retry_gate=not agentic.require_concrete_retry,
        batch_size=runtime.batch_size,
        limit=runtime.limit,
        overwrite=runtime.overwrite,
        prompt_cache=runtime.prompt_cache,
        media_reference=runtime.media_reference,
        prompt_cache_key=runtime.prompt_cache_key,
        experiment_names=[runtime.experiment_name],
        experiment_name=runtime.experiment_name,
        stage2_experiment_name=runtime.stage2_experiment_name,
        stage1_output_subdir=runtime.stage1_output_subdir,
        one_page_per_entry=runtime.one_page_per_entry,
        page_offset=runtime.page_offset,
        no_alphabet=not runtime.use_alphabet,
        no_ocr_hint=not runtime.use_ocr_hint,
        ocr_hint_experiment=runtime.ocr_hint_experiment,
        no_intro=not runtime.use_introduction,
        benchmark=config.kind == "benchmark_run",
        prompt_mode="benchmark" if config.kind == "benchmark_run" else "inference",
        prompts_file=None,
        compare_gold=None,
        vlm_model=vlm.model,
        vlm_dpi=vlm.dpi,
        mineru_batch_size=vlm.mineru_batch_size,
        mineru_max_new_tokens=vlm.mineru_max_new_tokens,
        vlm_backend=vlm.mineru_backend,
        paddle_vl_rec_backend=vlm.paddle_rec_backend,
        paddle_vl_rec_server_url=vlm.paddle_server_url,
        paddle_auto_vllm_server=vlm.paddle_auto_server,
        paddle_vl_server_port=vlm.paddle_server_port,
        paddle_vllm_server_python=vlm.paddle_server_python,
        glm_ocr_prompt=vlm.glm_prompt,
        glm_max_new_tokens=vlm.glm_max_new_tokens,
        glm_backend=vlm.glm_backend,
        glm_auto_vllm_server=vlm.glm_auto_server,
        glm_vllm_server_url=vlm.glm_server_url,
        glm_vllm_server_port=vlm.glm_server_port,
        glm_vllm_server_python=vlm.glm_server_python,
        mathpix_poll_interval_seconds=mathpix.poll_interval_seconds,
        mathpix_max_wait_seconds=mathpix.max_wait_seconds,
        mathpix_request_timeout_seconds=mathpix.request_timeout_seconds,
    )


def _write_resolved_config(config: MudidiConfig) -> None:
    output_dir = config.output.directory
    path = output_dir / "resolved_config.json"
    overwrite = getattr(config, "runtime", None)
    force = bool(overwrite and overwrite.overwrite)
    if path.exists() and not force:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redacted_config_dict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_PAGE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}


def _count_page_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(
        child.is_file() and child.suffix.lower() in _PAGE_SUFFIXES
        for child in path.iterdir()
    )


def _execution_preview(
    config: InferenceConfig | BenchmarkRunConfig,
) -> dict[str, Any]:
    """Resolve selected inputs and destinations without writes or API calls."""

    if isinstance(config, InferenceConfig):
        assert config.input.pages is not None
        return {
            "entry_count": 1,
            "page_count": _count_page_files(config.input.pages),
            "selected_input": str(config.input.pages),
            "derived_output": str(config.output.directory),
        }

    root = config.input.dataset_dir or config.input.samples_dir
    if root is None:
        assert config.input.pages is not None
        return {
            "entry_count": 1,
            "page_count": _count_page_files(config.input.pages),
            "selected_input": str(config.input.pages),
            "derived_output": str(config.output.directory),
        }
    requested = set(config.input.languages or [])
    entries = []
    skipped = []
    missing_prerequisites = []
    page_count = 0
    for entry in sorted(path for path in root.iterdir() if path.is_dir()):
        if requested and entry.name not in requested:
            continue
        pages = entry / "Dictionary pages"
        if not pages.is_dir():
            pages = entry / "snippets"
        if not pages.is_dir():
            skipped.append(entry.name)
            continue
        count = _count_page_files(pages)
        entries.append(
            {
                "name": entry.name,
                "input": str(pages),
                "page_count": count,
                "derived_output": str(config.output.directory / entry.name),
            }
        )
        if config.input.stage1_predictions_root is not None:
            prerequisite = (
                config.input.stage1_predictions_root
                / entry.name
                / config.runtime.stage1_output_subdir
                / config.runtime.experiment_name
            )
            if not prerequisite.is_dir():
                missing_prerequisites.append(str(prerequisite))
        page_count += count
    missing = requested - {entry["name"] for entry in entries}
    if missing:
        raise ValueError(f"input.languages has no runnable entries: {sorted(missing)}")
    if not entries:
        raise ValueError(f"no runnable benchmark entries found under {root}")
    if missing_prerequisites:
        raise ValueError(
            "missing Stage 1 prediction prerequisites: "
            + ", ".join(missing_prerequisites)
        )
    return {
        "entry_count": len(entries),
        "page_count": page_count,
        "entries": entries,
        "skipped_entries": skipped,
    }


def run_resolved_command(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser,
    kind: Literal["inference", "benchmark_run"],
) -> int:
    """Resolve and execute an extraction command, or print a dry-run report."""

    try:
        config = resolve_extraction_config(args, kind=kind)
        preview = preview_extraction_config(config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if getattr(args, "dry_run", False):
        print(
            json.dumps(
                {
                    "resolved_config": redacted_config_dict(config),
                    "preview": preview,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return execute_extraction_config(config)


def preview_extraction_config(
    config: InferenceConfig | BenchmarkRunConfig,
) -> dict[str, Any]:
    """Validate and preview one extraction configuration without side effects."""

    validate_config_paths(config)
    return _execution_preview(config)


def execute_extraction_config(
    config: InferenceConfig | BenchmarkRunConfig,
    *,
    approved_parse_rules: object | None = None,
    progress_callback: Callable[[str, int, str], None] | None = None,
) -> int:
    """Execute one typed extraction configuration.

    ``approved_parse_rules`` is an already authenticated in-memory model used
    by the web approval path. It is intentionally excluded from resolved
    configuration serialization.
    """

    configure_prompts(default_prompts_path())
    namespace = execution_namespace_from_config(config)
    namespace.approved_parse_rules = approved_parse_rules
    namespace.progress_callback = progress_callback
    _write_resolved_config(config)
    from mudidi.cli import extract as extract_module

    with _openrouter_provider_environment(config.models.openrouter_provider):
        return extract_module.main(resolved_args=namespace)


@contextmanager
def _openrouter_provider_environment(provider: str | None):
    """Apply one resolved OpenRouter endpoint choice for the extraction process."""

    if provider is None:
        yield
        return
    name = "OPENROUTER_PROVIDER_ORDER"
    previous = os.environ.get(name)
    os.environ[name] = "" if provider == "auto" else provider
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _parse_sweep_selectors(values: list[str] | None) -> dict[str, set[str]]:
    selectors: dict[str, set[str]] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--select must use AXIS=CHOICE syntax: {value!r}")
        axis, choice = value.split("=", 1)
        if not axis or not choice:
            raise ValueError(f"--select must use AXIS=CHOICE syntax: {value!r}")
        selectors.setdefault(axis, set()).add(choice)
    return selectors


def _write_sweep_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_benchmark_sweep_command(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser,
) -> int:
    """Expand, preview, and sequentially execute a typed benchmark sweep."""

    from mudidi.config.benchmark_sweep import expand_benchmark_sweep

    try:
        loaded = load_yaml_config(args.config, expected_kind="benchmark_sweep")
        if not isinstance(loaded, BenchmarkSweepConfig):
            raise TypeError("benchmark sweep command requires BenchmarkSweepConfig")
        selectors = _parse_sweep_selectors(getattr(args, "select", None))
        runs = expand_benchmark_sweep(
            loaded,
            experiments=set(getattr(args, "experiment", None) or []),
            selectors=selectors,
            max_runs=getattr(args, "max_runs", None),
        )
        previews = [preview_extraction_config(run.config) for run in runs]
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    entry_run_count = sum(int(preview["entry_count"]) for preview in previews)
    if getattr(args, "dry_run", False):
        print(
            json.dumps(
                {
                    "version": loaded.version,
                    "kind": loaded.kind,
                    "name": loaded.name,
                    "run_count": len(runs),
                    "entry_run_count": entry_run_count,
                    "runs": [
                        {
                            "name": run.name,
                            "choices": run.choices,
                            "resolved_config": redacted_config_dict(run.config),
                            "preview": preview,
                        }
                        for run, preview in zip(runs, previews)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    manifest_path = (
        loaded.base.output.directory
        / "sweeps"
        / loaded.name
        / "sweep_manifest.json"
    )
    manifest: dict[str, Any] = {
        "version": loaded.version,
        "kind": loaded.kind,
        "name": loaded.name,
        "source_config": str(loaded.source_config) if loaded.source_config else None,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "running",
        "entry_run_count": entry_run_count,
        "runs": [
            {
                "name": run.name,
                "choices": run.choices,
                "status": "pending",
                "resolved_config": redacted_config_dict(run.config),
            }
            for run in runs
        ],
    }
    _write_sweep_manifest(manifest_path, manifest)
    any_failure = False
    for index, run in enumerate(runs):
        print(f"\n{'=' * 60}\nSweep experiment: {run.name}\n{'=' * 60}")
        rc = execute_extraction_config(run.config)
        status = "complete" if rc == 0 else "failed"
        manifest["runs"][index]["status"] = status
        manifest["runs"][index]["exit_code"] = rc
        _write_sweep_manifest(manifest_path, manifest)
        if rc != 0:
            any_failure = True
            if loaded.sweep.failure_policy == "stop":
                break
    manifest["status"] = "failed" if any_failure else "complete"
    manifest["completed_utc"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    _write_sweep_manifest(manifest_path, manifest)
    return 1 if any_failure else 0


def run_evaluation_command(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser,
    kind: str,
) -> int:
    """Resolve and execute a Stage 1 or Stage 2 evaluation command."""

    values = _namespace_values(args)
    config_path = values.get("config")
    if kind == "stage1_evaluation":
        from mudidi.cli.evaluate_stage1 import main as evaluate
    elif kind == "stage2_evaluation":
        from mudidi.cli.evaluate_stage2_mdf import main as evaluate
    else:
        parser.error(f"unsupported evaluation kind: {kind}")
    if config_path is not None:
        config = load_yaml_config(config_path, expected_kind=cast(ConfigKind, kind))
        if not isinstance(config, (Stage1EvaluationConfig, Stage2EvaluationConfig)):
            parser.error(f"unsupported evaluation config: {config.kind}")
        overrides: dict[str, Any] = {}
        for cli_name, config_path_name in _EVALUATION_OVERRIDE_PATHS.items():
            if cli_name not in values:
                continue
            value = values[cli_name]
            if cli_name in _EVALUATION_PATH_OVERRIDES:
                value = _absolute_cli_path(value)
            overrides[config_path_name] = value
        config = cast(
            Stage1EvaluationConfig | Stage2EvaluationConfig,
            merge_explicit_overrides(config, overrides),
        )
        return evaluate(config=config)

    argv: list[str] = []
    flag_names = {
        "predicted": "--predicted",
        "gold": "--gold",
        "dataset_dir": "--dataset-dir",
        "pred_root": "--pred-root",
        "samples_dir": "--samples-dir",
        "output_dir": "--output-dir",
        "experiment_name_contains": "--experiment-name-contains",
        "stage1_output_subdir": "--stage1-output-subdir",
        "metrics": "--metrics",
        "alignment_threshold": "--alignment-threshold",
        "character_alignment": "--character-alignment",
        "workers": "--workers",
        "baseline_summary": "--baseline-summary",
        "baseline_experiment": "--baseline-experiment",
        "comparison_output": "--comparison-output",
        "record_threshold": "--record-threshold",
        "line_threshold": "--line-threshold",
        "marker_sub_list": "--marker-sub-list",
        "dictionary_languages": "--dictionary-languages",
    }
    for name, flag in flag_names.items():
        if name in values:
            argv.extend([flag, str(values[name])])
    for name, flag in {
        "all_experiments": "--all-experiments",
        "include_vlm_ocr": "--include-vlm-ocr",
        "per_language_script": "--per-language-script",
        "overwrite": "--overwrite",
    }.items():
        if values.get(name):
            argv.append(flag)
    if "languages" in values:
        argv.extend(["--languages", *values["languages"]])
    for experiment in values.get("experiment_name", []):
        argv.extend(["--experiment-name", experiment])
    return evaluate(argv)
