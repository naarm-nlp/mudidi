"""Expansion and selection of typed benchmark sweep configurations."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable

from mudidi.config.yaml_config import (
    BenchmarkRunConfig,
    BenchmarkSweepConfig,
    SweepChoice,
    merge_explicit_overrides,
)


@dataclass(frozen=True)
class ExpandedSweepRun:
    """One named, fully validated run produced by a benchmark sweep."""

    name: str
    config: BenchmarkRunConfig
    choices: dict[str, str]


def _is_excluded(
    choices: dict[str, str],
    exclusions: Iterable[dict[str, str]],
) -> bool:
    return any(
        all(choices.get(axis) == choice_id for axis, choice_id in rule.items())
        for rule in exclusions
    )


def _expanded_config(
    sweep: BenchmarkSweepConfig,
    name: str,
    choices: Iterable[SweepChoice],
) -> BenchmarkRunConfig:
    overrides: dict[str, object] = {}
    for choice in choices:
        overrides.update(choice.values)
    overrides[sweep.name_field] = name
    config = merge_explicit_overrides(sweep.base, overrides)
    if not isinstance(config, BenchmarkRunConfig):
        raise TypeError("benchmark sweep expansion must produce BenchmarkRunConfig")
    return config.model_copy(update={"source_config": sweep.source_config})


def expand_benchmark_sweep(
    sweep: BenchmarkSweepConfig,
    *,
    experiments: set[str] | None = None,
    selectors: dict[str, set[str]] | None = None,
    max_runs: int | None = None,
) -> list[ExpandedSweepRun]:
    """Expand and validate a sweep, optionally filtering names and axes."""

    expanded: list[ExpandedSweepRun] = []
    if sweep.experiments is not None:
        if selectors:
            raise ValueError("--select is only valid for an axis sweep")
        for choice in sweep.experiments:
            if experiments and choice.id not in experiments:
                continue
            expanded.append(
                ExpandedSweepRun(
                    name=choice.id,
                    config=_expanded_config(sweep, choice.id, [choice]),
                    choices={"experiment": choice.id},
                )
            )
    else:
        assert sweep.axes is not None
        assert sweep.experiment_name is not None
        unknown_axes = set(selectors or {}) - set(sweep.axes)
        if unknown_axes:
            raise ValueError(f"unknown sweep axes: {sorted(unknown_axes)}")
        for axis, allowed in (selectors or {}).items():
            available = {choice.id for choice in sweep.axes[axis]}
            unknown_choices = allowed - available
            if unknown_choices:
                raise ValueError(
                    f"unknown choices for sweep axis {axis!r}: "
                    f"{sorted(unknown_choices)}"
                )
        axis_names = list(sweep.axes)
        axis_choices = [sweep.axes[name] for name in axis_names]
        for combination in product(*axis_choices):
            selected = dict(zip(axis_names, (choice.id for choice in combination)))
            if selectors and any(
                selected.get(axis) not in allowed
                for axis, allowed in selectors.items()
            ):
                continue
            if _is_excluded(selected, sweep.exclude):
                continue
            try:
                name = sweep.experiment_name.format(**selected)
            except KeyError as exc:
                raise ValueError(
                    f"experiment_name references unknown axis {exc.args[0]!r}"
                ) from exc
            if experiments and name not in experiments:
                continue
            expanded.append(
                ExpandedSweepRun(
                    name=name,
                    config=_expanded_config(sweep, name, combination),
                    choices=selected,
                )
            )

    names = [run.name for run in expanded]
    if len(names) != len(set(names)):
        raise ValueError("benchmark sweep expands to duplicate experiment names")
    limit = max_runs if max_runs is not None else sweep.sweep.max_runs
    if len(expanded) > limit:
        raise ValueError(
            f"benchmark sweep expands to {len(expanded)} runs, exceeding max_runs={limit}"
        )
    if experiments:
        missing = experiments - set(names)
        if missing:
            raise ValueError(f"unknown sweep experiments: {sorted(missing)}")
    if not expanded:
        raise ValueError("benchmark sweep selection produced no runs")
    return expanded
