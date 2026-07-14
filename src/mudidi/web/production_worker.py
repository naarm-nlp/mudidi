"""Private subprocess entry point for staged production inference."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from mudidi.cli.run import execute_extraction_config
from mudidi.config.yaml_config import InferenceConfig
from mudidi.execution.approval import (
    load_approved_parse_rules,
    mint_approved_parse_rules,
)
from mudidi.execution.events import (
    ParseRulesGenerated,
    PageCompleted,
    PageStarted,
    RunCompleted,
    RunFailed,
    StageStarted,
)
from mudidi.paths import MDF_PARSING_GUIDE_FILENAME
from mudidi.schemas.field_cheatsheet import validate_marker_cheatsheet
from mudidi.utils.pdf_split import parse_page_spec
from mudidi.web.inference_worker import (
    InferencePhase,
    apply_credential_message,
    run_inference_phase,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the private production worker protocol parser."""

    parser = argparse.ArgumentParser(prog="mudidi-production-worker")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=[phase.value for phase in InferencePhase], required=True
    )
    parser.add_argument("--sequence-start", type=int, default=0)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--approval-manifest", type=Path)
    parser.add_argument("--offline-executor", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read one secret message, execute stages, and reserve stdout for events."""

    args = build_parser().parse_args(argv)
    protocol_stream = sys.stdout
    credential_message = sys.stdin.readline().strip()
    # Empty redaction sentinel; populated only from the private credential message.
    secret_value = ""  # nosec B105
    sequence = args.sequence_start
    try:
        if credential_message and credential_message != "{}":
            parsed = json.loads(credential_message)
            secret_value = (
                str(parsed.get("api_key", "")) if isinstance(parsed, dict) else ""
            )
            apply_credential_message(credential_message)
        config = InferenceConfig.model_validate_json(
            args.config.read_text(encoding="utf-8")
        )
        phase = InferencePhase(args.phase)
        approved_rules = (
            _load_approval(args.approval_manifest)
            if phase is InferencePhase.PASS2
            else None
        )
        sequence_lock = Lock()

        def emit_stage(stage_name: str) -> None:
            nonlocal sequence
            with sequence_lock:
                sequence += 1
                _emit(
                    StageStarted(
                        run_id=args.run_id,
                        sequence=sequence,
                        stage=_event_stage_name(stage_name),
                        occurred_at=datetime.now(UTC),
                        total_pages=_page_count(
                            config.input.pages,
                            config.input.dictionary_pages,
                        ),
                    ),
                    stream=protocol_stream,
                )

        def emit_page(status: str, page: int, stage_name: str) -> None:
            nonlocal sequence
            event_class = PageStarted if status == "started" else PageCompleted
            with sequence_lock:
                sequence += 1
                _emit(
                    event_class(
                        run_id=args.run_id,
                        sequence=sequence,
                        stage=_event_stage_name(stage_name),
                        page=page,
                        occurred_at=datetime.now(UTC),
                    ),
                    stream=protocol_stream,
                )

        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        executor = (
            _offline_execute if args.offline_executor else execute_extraction_config
        )

        def execute_with_progress(
            phase_config: InferenceConfig,
            *,
            approved_parse_rules: object | None = None,
        ) -> int:
            return executor(
                phase_config,
                approved_parse_rules=approved_parse_rules,
                progress_callback=emit_page,
            )

        with args.log_file.open("a", encoding="utf-8") as log_stream:
            with (
                contextlib.redirect_stdout(log_stream),
                contextlib.redirect_stderr(log_stream),
            ):
                result = run_inference_phase(
                    config,
                    phase,
                    execute=execute_with_progress,
                    approved_rules=approved_rules,
                    on_stage_started=emit_stage,
                )
        if result.return_code != 0:
            raise RuntimeError(f"extraction returned {result.return_code}")
        sequence += 1
        if result.parse_rules_path is not None:
            if not result.parse_rules_path.is_file():
                raise FileNotFoundError(
                    f"Pass 1 did not produce {MDF_PARSING_GUIDE_FILENAME}"
                )
            _emit(
                ParseRulesGenerated(
                    run_id=args.run_id,
                    sequence=sequence,
                    stage="stage2_pass1",
                    occurred_at=datetime.now(UTC),
                    artifact_path=result.parse_rules_path,
                ),
                stream=protocol_stream,
            )
        else:
            _emit(
                RunCompleted(
                    run_id=args.run_id,
                    sequence=sequence,
                    stage=_event_stage(phase),
                    occurred_at=datetime.now(UTC),
                )
            )
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if secret_value:
            message = message.replace(secret_value, "[REDACTED]")
        sequence += 1
        _emit(
            RunFailed(
                run_id=args.run_id,
                sequence=max(1, sequence),
                stage=_event_stage(InferencePhase(args.phase)),
                occurred_at=datetime.now(UTC),
                message=message[:500],
            ),
            stream=protocol_stream,
        )
        return 1


def _load_approval(path: Path | None) -> object:
    if path is None:
        raise ValueError("Pass 2 approval manifest is required")
    payload = json.loads(path.read_text(encoding="utf-8"))
    approval = mint_approved_parse_rules(
        run_id=str(payload["run_id"]),
        review_id=str(payload["review_id"]),
        snapshot_path=Path(str(payload["snapshot_path"])),
        sha256=str(payload["sha256"]),
        approved_at=datetime.fromisoformat(str(payload["approved_at"])),
    )
    return load_approved_parse_rules(approval).rules


def _event_stage(phase: InferencePhase) -> str:
    if phase is InferencePhase.PASS1:
        return "stage2_pass1"
    if phase is InferencePhase.PASS2:
        return "stage2_pass2"
    return "stage1"


def _event_stage_name(stage: str) -> str:
    """Map extract's stage notation onto the durable event protocol."""

    if stage == "1":
        return "stage1"
    if stage == "2-pass-1":
        return "stage2_pass1"
    return "stage2_pass2"


def _page_count(
    pages: Path | None,
    dictionary_pages: str | None = None,
) -> int | None:
    if pages is None:
        return None
    if pages.suffix.lower() == ".pdf":
        selected_pages = parse_page_spec(dictionary_pages or "")
        return len(selected_pages) or None
    if not pages.is_dir():
        return None
    count = sum(
        path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        for path in pages.iterdir()
        if path.is_file()
    )
    return count or None


def _offline_execute(
    config: InferenceConfig,
    *,
    approved_parse_rules: object | None = None,
    progress_callback: Callable[[str, int, str], None] | None = None,
) -> int:
    """Produce deterministic local artifacts through the production protocol."""

    stage = config.pipeline.stage
    if stage == "1":
        if progress_callback is not None:
            progress_callback("started", 1, stage)
        path = config.output.directory / "stage-1/page_1/page_1_stage1_flat.txt"
        content = "offline transcription\n"
    elif stage == "2-pass-1":
        path = config.output.directory / MDF_PARSING_GUIDE_FILENAME
        content = json.dumps(
            {
                "markers": [
                    {"marker": "lx", "description": "Headword"},
                    {"marker": "ge", "description": "English gloss"},
                ],
                "rules": ["Preserve entry order."],
                "abbreviations": {},
            },
            indent=2,
        )
    elif stage == "2-pass-2":
        if approved_parse_rules is None:
            raise ValueError("offline Pass 2 still requires approved rules")
        if progress_callback is not None:
            progress_callback("started", 1, stage)
        path = config.output.directory / "stage-2/page_1/page_1_mdf.txt"
        content = "\\lx offline\n\\ge result\n"
    elif stage == "2":
        guide = config.pipeline.parse_rules_file
        if guide is None:
            raise ValueError("offline direct Stage 2 requires an uploaded MDF guide")
        validate_marker_cheatsheet(json.loads(guide.read_text(encoding="utf-8")))
        if progress_callback is not None:
            progress_callback("started", 1, stage)
        path = config.output.directory / "stage-2/page_1/page_1_mdf.txt"
        content = "\\lx offline\n\\ge result\n"
    else:
        raise ValueError(f"unsafe offline web stage: {stage}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if progress_callback is not None and stage != "2-pass-1":
        progress_callback("completed", 1, stage)
    return 0


def _emit(event: Any, *, stream: Any = None) -> None:
    print(
        json.dumps(event.model_dump(mode="json"), separators=(",", ":")),
        file=stream or sys.stdout,
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
