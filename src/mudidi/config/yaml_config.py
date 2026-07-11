"""Versioned YAML configuration for MUDIDI commands.

The models in this module are the public configuration boundary. Runtime
credentials deliberately remain outside these models and are loaded from the
environment by the LLM client.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from mudidi.cli.model_args import DEFAULT_MODEL
from mudidi.config.run_config import RunStage
from mudidi.utils.pdf_split import parse_page_spec

ConfigKind = Literal[
    "inference",
    "benchmark_run",
    "benchmark_sweep",
    "stage1_evaluation",
    "stage2_evaluation",
]
ReasoningEffort = Literal["none", "low", "medium", "high"]

_PATH_KEYS = {
    "pages",
    "dataset_dir",
    "samples_dir",
    "stage1_predictions_root",
    "introduction",
    "alphabet",
    "ocr_text",
    "dictionary_languages",
    "parse_rules_file",
    "stage1_guides",
    "stage2_guides",
    "toolbox_pdf",
    "directory",
    "predicted",
    "gold",
    "pred_root",
    "baseline_summary",
    "comparison_output",
    "marker_sub_list",
    "paddle_server_python",
    "glm_server_python",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputConfig(_StrictModel):
    """Input paths and page selection shared by extraction commands."""

    pages: Path | None = None
    dataset_dir: Path | None = None
    samples_dir: Path | None = None
    stage1_predictions_root: Path | None = None
    dictionary_pages: str | None = None
    introduction: Path | None = None
    introduction_pages: str | None = None
    alphabet: Path | None = None
    ocr_text: Path | None = None
    dictionary_languages: Path | None = None
    toolbox_pdf: Path | None = None
    languages: list[str] | None = None


class OutputConfig(_StrictModel):
    """Output root for one command invocation."""

    directory: Path


class PipelineConfig(_StrictModel):
    """Stage selection and two-stage extraction behavior."""

    stage: RunStage = "all"
    strategy: Literal["two_stage", "vlm_ocr", "mathpix_ocr"] = "two_stage"
    stage1_mode: Literal["flat", "column"] = "flat"
    stage1_input: Literal["auto", "flat", "column"] = "auto"
    stage1_source: Literal["gold", "predictions"] = "predictions"
    stage1_typography: bool = False
    parse_rules_pages: list[str] = Field(default_factory=list)
    parse_rules_file: Path | None = None
    parse_rules_gold: bool = False
    stage2_lexical_repair: bool = False
    stage1_guides: Path | None = None
    stage2_guides: Path | None = None

    @model_validator(mode="after")
    def validate_strategy_stage(self) -> PipelineConfig:
        if self.strategy == "vlm_ocr" and self.stage != "1":
            raise ValueError("vlm_ocr requires pipeline.stage: '1'")
        if self.strategy == "vlm_ocr" and self.stage1_mode != "flat":
            raise ValueError("vlm_ocr requires pipeline.stage1_mode: flat")
        if self.strategy == "mathpix_ocr" and self.stage != "1":
            raise ValueError("mathpix_ocr requires pipeline.stage: '1'")
        if self.strategy == "mathpix_ocr" and self.stage1_mode != "flat":
            raise ValueError("mathpix_ocr requires pipeline.stage1_mode: flat")
        return self


class ModelsConfig(_StrictModel):
    """Model ids and reasoning controls for each pipeline step."""

    default: str = DEFAULT_MODEL
    stage1: str | None = None
    stage2_pass1: str | None = None
    stage2_pass2: str | None = None
    stage1_reasoning: ReasoningEffort = "low"
    stage2_reasoning: Literal["low", "medium", "high"] = "low"
    temperature: float = Field(default=0.1, ge=0.0)


class AgenticConfig(_StrictModel):
    """Optional verifier-rewriter controls."""

    stage1: bool = False
    stage2: bool = False
    max_iterations: int = Field(default=2, ge=0)
    evaluator_model: str | None = None
    rewriter_model: str | None = None
    reasoning: ReasoningEffort = "low"
    evaluator_reasoning: ReasoningEffort | None = None
    rewriter_reasoning: ReasoningEffort | None = None
    min_retry_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    verifier_patches: bool = True
    require_concrete_retry: bool = True


class RuntimeConfig(_StrictModel):
    """Execution, caching, resume, and experiment settings."""

    batch_size: int = Field(default=1, ge=1)
    limit: int | None = Field(default=None, ge=1)
    overwrite: bool = False
    prompt_cache: Literal["auto", "off"] = "auto"
    media_reference: Literal["auto", "inline", "file-uri"] = "auto"
    prompt_cache_key: str | None = None
    experiment_name: str = "default"
    stage2_experiment_name: str | None = None
    stage1_output_subdir: str = "stage-1"
    one_page_per_entry: bool = False
    page_offset: int = 1
    use_alphabet: bool = True
    use_ocr_hint: bool = True
    ocr_hint_experiment: str | None = None
    use_introduction: bool = True


class VlmConfig(_StrictModel):
    """Advanced local OCR/VLM backend settings."""

    model: Literal["mineru2.5-pro", "paddleocr-vl-1.5", "glm-ocr"] | None = None
    dpi: int = Field(default=200, ge=72)
    mineru_batch_size: int = Field(default=8, ge=1)
    mineru_max_new_tokens: int = Field(default=1024, ge=1)
    mineru_backend: Literal["transformers", "vllm"] = "transformers"
    paddle_rec_backend: Literal["native", "vllm-server"] = "native"
    paddle_server_url: str | None = None
    paddle_auto_server: bool = True
    paddle_server_port: int = Field(default=8765, ge=1, le=65535)
    paddle_server_python: Path | None = None
    glm_prompt: str = "Text Recognition:"
    glm_max_new_tokens: int = Field(default=8192, ge=1)
    glm_backend: Literal["transformers", "vllm"] = "transformers"
    glm_auto_server: bool = True
    glm_server_url: str | None = None
    glm_server_port: int = Field(default=8081, ge=1, le=65535)
    glm_server_python: Path | None = None


class MathpixConfig(_StrictModel):
    """Mathpix Convert API polling and timeout controls."""

    poll_interval_seconds: float = Field(default=3.0, gt=0)
    max_wait_seconds: float = Field(default=600.0, gt=0)
    request_timeout_seconds: float = Field(default=60.0, gt=0)


class _ExtractionConfig(_StrictModel):
    version: Literal[1] = 1
    input: InputConfig
    output: OutputConfig
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    agentic: AgenticConfig = Field(default_factory=AgenticConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    vlm: VlmConfig = Field(default_factory=VlmConfig)
    mathpix: MathpixConfig = Field(default_factory=MathpixConfig)
    source_config: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_vlm(self) -> _ExtractionConfig:
        if self.pipeline.strategy == "vlm_ocr" and self.vlm.model is None:
            raise ValueError("vlm.model is required for strategy: vlm_ocr")
        if self.pipeline.strategy != "vlm_ocr" and self.vlm.model is not None:
            raise ValueError("vlm.model is only valid for strategy: vlm_ocr")
        return self


class InferenceConfig(_ExtractionConfig):
    """Production inference configuration."""

    kind: Literal["inference"] = "inference"

    @model_validator(mode="before")
    @classmethod
    def apply_inference_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = deepcopy(value)
        runtime = data.setdefault("runtime", {})
        if isinstance(runtime, dict):
            runtime.setdefault("use_alphabet", False)
        return data

    @model_validator(mode="after")
    def require_pages(self) -> InferenceConfig:
        if self.input.pages is None:
            raise ValueError("input.pages is required for inference")
        if self.pipeline.stage1_source == "gold":
            raise ValueError("inference does not support stage1_source: gold")
        return self


class BenchmarkRunConfig(_ExtractionConfig):
    """Benchmark extraction configuration."""

    kind: Literal["benchmark_run"] = "benchmark_run"

    @model_validator(mode="before")
    @classmethod
    def apply_benchmark_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = deepcopy(value)
        pipeline = data.setdefault("pipeline", {})
        if isinstance(pipeline, dict):
            pipeline.setdefault("stage1_source", "gold")
        return data

    @model_validator(mode="after")
    def require_benchmark_input(self) -> BenchmarkRunConfig:
        if not any((self.input.dataset_dir, self.input.samples_dir, self.input.pages)):
            raise ValueError(
                "benchmark_run requires input.dataset_dir, input.samples_dir, or input.pages"
            )
        if self.input.stage1_predictions_root is not None and (
            self.pipeline.stage1_source != "predictions"
            or self.pipeline.stage not in ("2", "2-pass-1", "2-pass-2")
        ):
            raise ValueError(
                "input.stage1_predictions_root requires a Stage 2-only run "
                "with pipeline.stage1_source: predictions"
            )
        return self


class EvaluationInputConfig(_StrictModel):
    predicted: Path | None = None
    gold: Path | None = None
    dataset_dir: Path | None = None
    pred_root: Path | None = None
    languages: list[str] | None = None


class EvaluationOptions(_StrictModel):
    experiment_names: list[str] = Field(default_factory=list)
    all_experiments: bool = False
    workers: int = Field(default=1, ge=1)
    per_language_script: bool = False
    character_alignment: Literal["collapsed", "quick_match"] = "quick_match"
    record_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    line_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    baseline_summary: Path | None = None
    baseline_experiment: str | None = None
    comparison_output: Path | None = None
    marker_sub_list: Path | None = None
    dictionary_languages: Path | None = None
    experiment_name_contains: str | None = None
    include_vlm_ocr: bool = False
    stage1_output_subdir: str = "stage-1"
    metrics: Literal["full", "minimal"] = "minimal"
    alignment_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    overwrite: bool = False


class _EvaluationConfig(_StrictModel):
    version: Literal[1] = 1
    input: EvaluationInputConfig
    output: OutputConfig
    evaluation: EvaluationOptions = Field(default_factory=EvaluationOptions)
    source_config: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def require_input_pair_or_batch(self) -> _EvaluationConfig:
        has_pair = self.input.predicted is not None and self.input.gold is not None
        has_batch = self.input.dataset_dir is not None and self.input.pred_root is not None
        if has_pair == has_batch:
            raise ValueError(
                "evaluation requires either predicted+gold or dataset_dir+pred_root"
            )
        pair_partial = (self.input.predicted is None) != (self.input.gold is None)
        batch_partial = (self.input.dataset_dir is None) != (self.input.pred_root is None)
        if pair_partial or batch_partial:
            raise ValueError("evaluation input pairs must be supplied together")
        return self


class Stage1EvaluationConfig(_EvaluationConfig):
    kind: Literal["stage1_evaluation"] = "stage1_evaluation"


class Stage2EvaluationConfig(_EvaluationConfig):
    kind: Literal["stage2_evaluation"] = "stage2_evaluation"


class SweepChoice(_StrictModel):
    """One named axis choice or explicit benchmark experiment."""

    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    values: dict[str, Any] = Field(alias="set")


class SweepOptions(_StrictModel):
    """Execution guards for an expanded benchmark sweep."""

    max_runs: int = Field(default=100, ge=1)
    failure_policy: Literal["continue", "stop"] = "continue"


class BenchmarkSweepConfig(_StrictModel):
    """A typed collection of benchmark runs expanded from axes or a list."""

    version: Literal[1] = 1
    kind: Literal["benchmark_sweep"] = "benchmark_sweep"
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    base: BenchmarkRunConfig
    axes: dict[str, list[SweepChoice]] | None = None
    experiments: list[SweepChoice] | None = None
    experiment_name: str | None = None
    name_field: Literal[
        "runtime.experiment_name", "runtime.stage2_experiment_name"
    ] = "runtime.experiment_name"
    exclude: list[dict[str, str]] = Field(default_factory=list)
    sweep: SweepOptions = Field(default_factory=SweepOptions)
    source_config: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_definition(self) -> BenchmarkSweepConfig:
        if (self.axes is None) == (self.experiments is None):
            raise ValueError("benchmark_sweep requires exactly one of axes or experiments")
        if self.axes is not None:
            if not self.axes:
                raise ValueError("benchmark_sweep axes cannot be empty")
            if not self.experiment_name:
                raise ValueError("experiment_name is required for an axis sweep")
            for axis, choices in self.axes.items():
                if not choices:
                    raise ValueError(f"sweep axis {axis!r} cannot be empty")
                ids = [choice.id for choice in choices]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"sweep axis {axis!r} has duplicate choice ids")
        if self.experiments is not None:
            ids = [choice.id for choice in self.experiments]
            if not ids:
                raise ValueError("benchmark_sweep experiments cannot be empty")
            if len(ids) != len(set(ids)):
                raise ValueError("benchmark_sweep has duplicate experiment ids")
            if self.exclude:
                raise ValueError("exclude is only valid with axes")
        return self


MudidiConfig: TypeAlias = Annotated[
    InferenceConfig
    | BenchmarkRunConfig
    | BenchmarkSweepConfig
    | Stage1EvaluationConfig
    | Stage2EvaluationConfig,
    Field(discriminator="kind"),
]
_CONFIG_ADAPTER = TypeAdapter(MudidiConfig)


def _resolve_path_string(value: str, base_dir: Path) -> str:
    if "://" in value:
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _resolve_paths(value: Any, base_dir: Path, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            child_key: (
                child
                if child_key == "exclude"
                else _resolve_paths(child, base_dir, child_key)
            )
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_resolve_paths(child, base_dir, key) for child in value]
    path_key = key.rsplit(".", 1)[-1] if key else None
    if isinstance(value, str) and path_key in _PATH_KEYS:
        return _resolve_path_string(value, base_dir)
    return value


def load_yaml_config(
    path: str | Path,
    *,
    expected_kind: ConfigKind | None = None,
) -> MudidiConfig:
    """Load and validate a MUDIDI YAML configuration file."""

    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a YAML mapping")
    resolved = _resolve_paths(raw, config_path.parent)
    config = _CONFIG_ADAPTER.validate_python(resolved)
    if expected_kind is not None and config.kind != expected_kind:
        raise ValueError(
            f"this command requires kind {expected_kind!r}; got {config.kind!r}"
        )
    return config.model_copy(update={"source_config": config_path})


def merge_explicit_overrides(
    config: MudidiConfig,
    overrides: dict[str, Any],
) -> MudidiConfig:
    """Return ``config`` with sparse dotted-path CLI overrides applied."""

    data = config.model_dump(mode="python", exclude={"source_config"})
    merged = deepcopy(data)
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        target = merged
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = value
    updated = type(config).model_validate(merged)
    return updated.model_copy(update={"source_config": config.source_config})


def redacted_config_dict(config: MudidiConfig) -> dict[str, Any]:
    """Return a JSON-compatible resolved configuration without credentials."""

    data = config.model_dump(mode="json", exclude={"source_config"})
    if config.source_config is not None:
        data["source_config"] = str(config.source_config)
    return data


def validate_config_paths(config: MudidiConfig) -> None:
    """Validate referenced inputs and page specifications without API access."""

    paths: list[tuple[str, Path | None]]
    page_specs: list[tuple[str, str | None]] = []
    if isinstance(config, BenchmarkSweepConfig):
        from mudidi.config.benchmark_sweep import expand_benchmark_sweep

        for run in expand_benchmark_sweep(config):
            validate_config_paths(run.config)
        return
    if isinstance(config, (InferenceConfig, BenchmarkRunConfig)):
        paths = [
            ("input.pages", config.input.pages),
            ("input.dataset_dir", config.input.dataset_dir),
            ("input.samples_dir", config.input.samples_dir),
            ("input.stage1_predictions_root", config.input.stage1_predictions_root),
            ("input.introduction", config.input.introduction),
            ("input.alphabet", config.input.alphabet),
            ("input.ocr_text", config.input.ocr_text),
            ("input.dictionary_languages", config.input.dictionary_languages),
            ("input.toolbox_pdf", config.input.toolbox_pdf),
            ("pipeline.parse_rules_file", config.pipeline.parse_rules_file),
            ("pipeline.stage1_guides", config.pipeline.stage1_guides),
            ("pipeline.stage2_guides", config.pipeline.stage2_guides),
            ("vlm.paddle_server_python", config.vlm.paddle_server_python),
            ("vlm.glm_server_python", config.vlm.glm_server_python),
        ]
        page_specs = [
            ("input.dictionary_pages", config.input.dictionary_pages),
            ("input.introduction_pages", config.input.introduction_pages),
        ]
        pages = config.input.pages
        if pages is not None:
            is_pdf = pages.is_file() and pages.suffix.lower() == ".pdf"
            if is_pdf and not config.input.dictionary_pages:
                raise ValueError(
                    "input.dictionary_pages is required when input.pages is a PDF"
                )
            if is_pdf and config.input.introduction is not None:
                raise ValueError(
                    "input.introduction cannot be combined with PDF input.pages; "
                    "use input.introduction_pages"
                )
            if not is_pdf and config.input.dictionary_pages is not None:
                raise ValueError(
                    "input.dictionary_pages is only valid when input.pages is a PDF"
                )
            if not is_pdf and config.input.introduction_pages is not None:
                raise ValueError(
                    "input.introduction_pages is only valid when input.pages is a PDF"
                )
    else:
        paths = [
            ("input.predicted", config.input.predicted),
            ("input.gold", config.input.gold),
            ("input.dataset_dir", config.input.dataset_dir),
            ("input.pred_root", config.input.pred_root),
            ("evaluation.baseline_summary", config.evaluation.baseline_summary),
            ("evaluation.marker_sub_list", config.evaluation.marker_sub_list),
            (
                "evaluation.dictionary_languages",
                config.evaluation.dictionary_languages,
            ),
        ]

    missing = [label for label, path in paths if path is not None and not path.exists()]
    if missing:
        raise ValueError(f"{', '.join(missing)} does not exist")
    for label, spec in page_specs:
        if spec is None:
            continue
        try:
            parse_page_spec(spec)
        except ValueError as exc:
            raise ValueError(f"invalid {label}: {exc}") from exc
