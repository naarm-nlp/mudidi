"""Isolated worker entry point for fake/offline web lifecycle tests."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

from mudidi.execution.events import (
    PageCompleted,
    RunCompleted,
    RunFailed,
    StageStarted,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the private worker protocol parser."""

    parser = argparse.ArgumentParser(prog="mudidi-web-worker")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fake", action="store_true", required=True)
    parser.add_argument("--page-count", type=int, required=True)
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--fail", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Emit versioned JSONL progress for a deterministic offline run."""

    args = build_parser().parse_args(argv)
    if args.page_count < 1 or args.delay_seconds < 0:
        raise ValueError("fake worker page count and delay must be non-negative")
    sequence = 1
    _emit(
        StageStarted(
            run_id=args.run_id,
            sequence=sequence,
            stage="stage1",
            occurred_at=datetime.now(UTC),
            total_pages=args.page_count,
        )
    )
    for page in range(1, args.page_count + 1):
        if args.delay_seconds:
            time.sleep(args.delay_seconds)
        sequence += 1
        _emit(
            PageCompleted(
                run_id=args.run_id,
                sequence=sequence,
                stage="stage1",
                page=page,
                occurred_at=datetime.now(UTC),
            )
        )
    sequence += 1
    if args.fail:
        _emit(
            RunFailed(
                run_id=args.run_id,
                sequence=sequence,
                stage="stage1",
                occurred_at=datetime.now(UTC),
                message="Offline worker failure requested",
            )
        )
        return 1
    _emit(
        RunCompleted(
            run_id=args.run_id,
            sequence=sequence,
            stage="stage1",
            occurred_at=datetime.now(UTC),
        )
    )
    return 0


def _emit(event: StageStarted | PageCompleted | RunCompleted | RunFailed) -> None:
    print(json.dumps(event.model_dump(mode="json"), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
