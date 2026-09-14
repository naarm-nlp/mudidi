"""Tests for CLI model resolution."""

from __future__ import annotations

import argparse

from mudidi.cli.model_args import (
    DEFAULT_MODEL,
    register_model_arguments,
    stage_models_from_args,
)


def _args(**kwargs: object) -> argparse.Namespace:
    base = {
        "model": DEFAULT_MODEL,
        "stage_1_model": None,
        "stage_2_pass_1_model": None,
        "stage_2_pass_2_model": None,
        "structure_model": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_model_sets_all_steps() -> None:
    models = stage_models_from_args(_args(model="provider/all"))
    assert models.stage_1 == "provider/all"
    assert models.stage_2_pass_1 == "provider/all"
    assert models.stage_2_pass_2 == "provider/all"


def test_per_step_overrides() -> None:
    models = stage_models_from_args(
        _args(
            model="provider/default",
            stage_1_model="provider/s1",
            stage_2_pass_1_model="provider/s2p1",
            stage_2_pass_2_model="provider/s2p2",
        )
    )
    assert models.stage_1 == "provider/s1"
    assert models.stage_2_pass_1 == "provider/s2p1"
    assert models.stage_2_pass_2 == "provider/s2p2"


def test_legacy_structure_model_fills_stage_2() -> None:
    models = stage_models_from_args(
        _args(model="provider/default", structure_model="provider/legacy")
    )
    assert models.stage_1 == "provider/default"
    assert models.stage_2_pass_1 == "provider/legacy"
    assert models.stage_2_pass_2 == "provider/legacy"


def test_legacy_parser_marks_explicit_default_model() -> None:
    parser = argparse.ArgumentParser()
    register_model_arguments(parser)

    implicit = parser.parse_args([])
    explicit = parser.parse_args(["--model", DEFAULT_MODEL])

    assert implicit.model == DEFAULT_MODEL
    assert implicit._model_explicit is False
    assert explicit.model == DEFAULT_MODEL
    assert explicit._model_explicit is True
