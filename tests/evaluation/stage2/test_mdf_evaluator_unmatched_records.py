"""Regression tests for unmatched records in Stage 2 MDF metrics."""

from __future__ import annotations

from pathlib import Path

import pytest

from mudidi.evaluation.stage2.mdf_evaluator import MdfEvaluator


def _write_mdf(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_extra_record_lowers_all_stage2_headline_metrics(tmp_path: Path) -> None:
    gold = tmp_path / "gold.mdf.txt"
    pred = tmp_path / "pred.mdf.txt"
    _write_mdf(gold, "\\lx cat\n\\ge feline\n")
    _write_mdf(
        pred,
        "\\lx cat\n\\ge feline\n\n\\lx hallucinated\n\\ge extra entry\n",
    )

    metrics = MdfEvaluator().evaluate(pred, gold, page_id="test/page")

    assert metrics.record.tp == 1
    assert metrics.record.fp == 1
    assert metrics.record.fn == 0
    assert metrics.entry_f1 == pytest.approx(2 / 3)
    assert metrics.marker.tp == 2
    assert metrics.marker.fp == 2
    assert metrics.marker.fn == 0
    assert metrics.mdf_fields_f1 == pytest.approx(2 / 3)
    assert metrics.read_order.edit_distance == 1
    assert metrics.read_order.max_length == 2
    assert metrics.read_order.read_order_edit == pytest.approx(0.5)

    aggregate = MdfEvaluator._aggregate_dict([metrics])
    assert aggregate["Entry_F1"] == pytest.approx(2 / 3, abs=1e-6)
    assert aggregate["MDF_Fields_F1"] == pytest.approx(2 / 3, abs=1e-6)
    assert aggregate["read_order"]["ReadOrderEdit"] == pytest.approx(0.5)


def test_missing_record_lowers_all_stage2_headline_metrics(tmp_path: Path) -> None:
    gold = tmp_path / "gold.mdf.txt"
    pred = tmp_path / "pred.mdf.txt"
    _write_mdf(
        gold,
        "\\lx cat\n\\ge feline\n\n\\lx dog\n\\ge canine\n",
    )
    _write_mdf(pred, "\\lx cat\n\\ge feline\n")

    metrics = MdfEvaluator().evaluate(pred, gold, page_id="test/page")

    assert metrics.record.tp == 1
    assert metrics.record.fp == 0
    assert metrics.record.fn == 1
    assert metrics.entry_f1 == pytest.approx(2 / 3)
    assert metrics.marker.tp == 2
    assert metrics.marker.fp == 0
    assert metrics.marker.fn == 2
    assert metrics.mdf_fields_f1 == pytest.approx(2 / 3)
    assert metrics.read_order.edit_distance == 1
    assert metrics.read_order.max_length == 2
    assert metrics.read_order.read_order_edit == pytest.approx(0.5)


def test_extra_record_position_is_preserved_in_read_order(tmp_path: Path) -> None:
    gold = tmp_path / "gold.mdf.txt"
    pred = tmp_path / "pred.mdf.txt"
    _write_mdf(
        gold,
        "\\lx cat\n\\ge feline\n\n\\lx dog\n\\ge canine\n",
    )
    _write_mdf(
        pred,
        "\\lx cat\n\\ge feline\n\n"
        "\\lx hallucinated\n\\ge extra entry\n\n"
        "\\lx dog\n\\ge canine\n",
    )

    metrics = MdfEvaluator().evaluate(pred, gold, page_id="test/page")

    assert metrics.read_order.edit_distance == 1
    assert metrics.read_order.max_length == 3
    assert metrics.read_order.read_order_edit == pytest.approx(1 / 3)
