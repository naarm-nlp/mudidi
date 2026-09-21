import json
import importlib.util
from pathlib import Path

import pytest

UTILS_PATH = Path(__file__).parents[2] / "tables" / "scripts" / "table_utils.py"
SPEC = importlib.util.spec_from_file_location("table_utils", UTILS_PATH)
assert SPEC is not None and SPEC.loader is not None
table_utils = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(table_utils)

TableDataError = table_utils.TableDataError
index_unique = table_utils.index_unique
read_delimited = table_utils.read_delimited
read_stage1_aggregate = table_utils.read_stage1_aggregate
write_text_atomic = table_utils.write_text_atomic


def test_read_delimited_reports_missing_columns(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("name,value\nalpha,1\n", encoding="utf-8")

    with pytest.raises(
        TableDataError, match=r"input\.csv.*missing required columns: condition"
    ):
        read_delimited(source, required_columns=("name", "condition"))


def test_index_unique_rejects_duplicate_keys_with_source_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.csv"
    rows = [
        {"experiment": "fixed", "language": "A"},
        {"experiment": "fixed", "language": "A"},
    ]

    with pytest.raises(TableDataError, match=r"input\.csv.*duplicate key.*fixed.*A"):
        index_unique(rows, ("experiment", "language"), source=source)


def test_write_text_atomic_writes_complete_utf8_file(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "table.tex"

    write_text_atomic(output, "α\nβ\n")

    assert output.read_bytes() == "α\nβ\n".encode()
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


def _stage1_aggregate_record() -> dict[str, object]:
    return {
        "page_id": "__aggregate__",
        "character_quality": {"TextEdit": 0.1, "GCER": 0.2, "WER": 0.3},
        "markup_quality": {"typography": {"f1": 0.4}},
        "read_order": {"ReadOrderEdit": 0.5},
    }


def test_read_stage1_aggregate_returns_pooled_report_metrics(tmp_path: Path) -> None:
    source = tmp_path / "report.json"
    source.write_text(
        json.dumps([{"page_id": "Dictionary/page_1"}, _stage1_aggregate_record()]),
        encoding="utf-8",
    )

    assert read_stage1_aggregate(source) == {
        "TextEdit": 0.1,
        "GCER": 0.2,
        "WER": 0.3,
        "typography_f1": 0.4,
        "ReadOrderEdit": 0.5,
    }


@pytest.mark.parametrize(
    "report",
    [
        [{"page_id": "Dictionary/page_1"}],
        [_stage1_aggregate_record(), {"page_id": "Dictionary/page_1"}],
        [_stage1_aggregate_record(), _stage1_aggregate_record()],
    ],
)
def test_read_stage1_aggregate_requires_one_terminal_record(
    tmp_path: Path,
    report: list[dict[str, object]],
) -> None:
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(
        TableDataError,
        match=r"expected exactly one terminal __aggregate__ record",
    ):
        read_stage1_aggregate(source)
