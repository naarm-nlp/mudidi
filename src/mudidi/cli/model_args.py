"""CLI model selection for Stage 1 and Stage 2 (Pass 1 / Pass 2)."""

from __future__ import annotations

import argparse
import warnings
from dataclasses import dataclass

DEFAULT_MODEL = "gemini/gemini-3-flash-preview"


@dataclass(frozen=True)
class StageModels:
    """Resolved litellm model ids per pipeline step."""

    stage_1: str
    stage_2_pass_1: str
    stage_2_pass_2: str

    def summary(self) -> str:
        """One-line log label; collapses when all three match."""
        if self.stage_1 == self.stage_2_pass_1 == self.stage_2_pass_2:
            return self.stage_1
        return (
            f"stage-1={self.stage_1} | "
            f"stage-2-pass-1={self.stage_2_pass_1} | "
            f"stage-2-pass-2={self.stage_2_pass_2}"
        )


def register_model_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``--model`` and per-step model overrides."""
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help="Default model for all steps when step-specific flags are omitted "
        f"(default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--stage-1-model",
        dest="stage_1_model",
        default=None,
        help="Stage 1 transcription model (overrides --model for Stage 1 only).",
    )
    parser.add_argument(
        "--stage-2-pass-1-model",
        dest="stage_2_pass_1_model",
        default=None,
        help="Stage 2 Pass 1 parse-rules discovery model (overrides --model).",
    )
    parser.add_argument(
        "--stage-2-pass-2-model",
        dest="stage_2_pass_2_model",
        default=None,
        help="Stage 2 Pass 2 per-page MDF extraction model (overrides --model).",
    )
    parser.add_argument(
        "--structure-model",
        dest="structure_model",
        default=None,
        help=argparse.SUPPRESS,
    )


def stage_models_from_args(args: argparse.Namespace) -> StageModels:
    """Resolve final model ids from CLI args."""
    default = args.model
    legacy = getattr(args, "structure_model", None)
    if legacy is not None:
        warnings.warn(
            "--structure-model is deprecated; use --stage-2-pass-1-model and/or "
            "--stage-2-pass-2-model instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    stage_1 = getattr(args, "stage_1_model", None) or default
    pass_1 = (
        getattr(args, "stage_2_pass_1_model", None) or legacy or default
    )
    pass_2 = (
        getattr(args, "stage_2_pass_2_model", None) or legacy or default
    )
    return StageModels(stage_1=stage_1, stage_2_pass_1=pass_1, stage_2_pass_2=pass_2)


def attach_stage_models(args: argparse.Namespace) -> StageModels:
    """Resolve models and store on ``args.stage_models``."""
    models = stage_models_from_args(args)
    args.stage_models = models
    return models


def forward_model_argv(argv: list[str], args: argparse.Namespace) -> None:
    """Append model flags from a parsed namespace onto an argv list."""
    argv.extend(["--model", args.model])
    if args.stage_1_model:
        argv.extend(["--stage-1-model", args.stage_1_model])
    if args.stage_2_pass_1_model:
        argv.extend(["--stage-2-pass-1-model", args.stage_2_pass_1_model])
    if args.stage_2_pass_2_model:
        argv.extend(["--stage-2-pass-2-model", args.stage_2_pass_2_model])
    if getattr(args, "structure_model", None):
        argv.extend(["--structure-model", args.structure_model])
