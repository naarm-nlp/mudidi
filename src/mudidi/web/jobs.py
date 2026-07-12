"""Single-worker subprocess controller with persisted structured events."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, Thread
from typing import TYPE_CHECKING

from pydantic import ValidationError

from mudidi.config.yaml_config import InferenceConfig
from mudidi.execution.approval import ApprovedParseRules
from mudidi.execution.events import (
    ParseRulesGenerated,
    RunCompleted,
    RunFailed,
    parse_execution_event,
)
from mudidi.web.credentials import (
    CredentialSource,
    ResolvedCredential,
    credential_environment_name,
)
from mudidi.web.inference_worker import InferencePhase
from mudidi.web.models import Provider
from mudidi.web.runs import RunStatus, RunStore

if TYPE_CHECKING:
    from mudidi.web.parse_rules import ParseRuleReviewService


@dataclass(slots=True)
class _OwnedWorker:
    process: subprocess.Popen[str]
    command: tuple[str, ...]
    monitor: Thread | None = None


class JobController:
    """Own exactly one child worker and mirror its events into SQLite."""

    def __init__(
        self,
        *,
        store: RunStore,
        data_dir: Path,
        parse_rule_reviews: ParseRuleReviewService | None = None,
    ) -> None:
        self.store = store
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.parse_rule_reviews = parse_rule_reviews
        self._workers: dict[str, _OwnedWorker] = {}
        self._lock = RLock()

    def config_path(self, run_id: str) -> Path:
        """Return the conventional managed config path for one run."""

        return self.data_dir / "runs" / run_id / "resolved_config.json"

    def log_path(self, run_id: str) -> Path:
        """Return the managed worker log path for an existing durable run."""

        self.store.get_run(run_id)
        return self.data_dir / "runs" / run_id / "worker.log"

    def prepare_inference(
        self,
        run_id: str,
        *,
        config: InferenceConfig,
        provider: Provider,
    ) -> None:
        """Persist a redacted typed config and create a validated durable run."""

        if config.pipeline.stage == "2-pass-2":
            raise ValueError("direct web Pass 2 preparation is forbidden")
        path = self.config_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        self.store.create_run(run_id, provider=provider.value)
        self.store.transition(run_id, RunStatus.VALIDATED)

    def load_inference_config(self, run_id: str) -> InferenceConfig:
        """Load and validate the managed non-secret config for one run."""

        return InferenceConfig.model_validate_json(
            self.config_path(run_id).read_text(encoding="utf-8")
        )

    def start_inference(
        self,
        run_id: str,
        *,
        credential: ResolvedCredential | None,
        offline_executor: bool = False,
    ) -> None:
        """Launch Stage 1 and/or Pass 1 while preserving the review pause."""

        config = self.load_inference_config(run_id)
        phase = _initial_phase(config)
        current = self.store.get_run(run_id).status
        if current in {RunStatus.VALIDATED, RunStatus.CREDENTIALS_REQUIRED}:
            self.store.transition(run_id, RunStatus.QUEUED)
        target = (
            RunStatus.RUNNING_STAGE1
            if phase in {InferencePhase.STAGE1, InferencePhase.STAGE1_THEN_PASS1}
            else RunStatus.DISCOVERING_PARSE_RULES
        )
        self.store.transition(run_id, target)
        command = self._production_command(
            run_id,
            phase=phase,
            offline_executor=offline_executor,
        )
        self._spawn(run_id, command, credential=credential)

    def start_pass2(
        self,
        run_id: str,
        *,
        approval: ApprovedParseRules,
        credential: ResolvedCredential | None,
        offline_executor: bool = False,
    ) -> None:
        """Launch Pass 2 only for the run-bound committed approval capability."""

        run = self.store.get_run(run_id)
        if run.status is not RunStatus.RUNNING_STAGE2:
            raise RuntimeError("Pass 2 requires committed approval authorization")
        if approval.run_id != run_id or approval.sha256 != run.approval_digest:
            raise RuntimeError("approval capability does not match the durable run")
        manifest_path = self.data_dir / "runs" / run_id / "approval.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "run_id": approval.run_id,
                    "review_id": approval.review_id,
                    "snapshot_path": str(approval.snapshot_path),
                    "sha256": approval.sha256,
                    "approved_at": approval.approved_at.isoformat(),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        command = self._production_command(
            run_id,
            phase=InferencePhase.PASS2,
            approval_manifest=manifest_path,
            offline_executor=offline_executor,
        )
        self._spawn(run_id, command, credential=credential)

    def start_fake(
        self,
        run_id: str,
        *,
        page_count: int,
        delay_seconds: float,
        fail: bool = False,
    ) -> None:
        """Start a deterministic no-network worker for UI and E2E execution."""

        if page_count < 1 or delay_seconds < 0:
            raise ValueError("page_count must be positive and delay non-negative")
        with self._lock:
            if any(worker.process.poll() is None for worker in self._workers.values()):
                raise RuntimeError("another inference worker is active")
            self.store.transition(run_id, RunStatus.RUNNING_STAGE1)
            command = [
                sys.executable,
                "-m",
                "mudidi.web.worker",
                "--fake",
                "--run-id",
                run_id,
                "--page-count",
                str(page_count),
                "--delay-seconds",
                str(delay_seconds),
            ]
            if fail:
                command.append("--fail")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except OSError:
                self.store.transition(run_id, RunStatus.FAILED)
                raise
            worker = _OwnedWorker(process=process, command=tuple(command))
            monitor = Thread(
                target=self._monitor,
                args=(run_id, worker),
                daemon=True,
                name=f"mudidi-worker-monitor-{run_id}",
            )
            worker.monitor = monitor
            self._workers[run_id] = worker
            monitor.start()

    def _production_command(
        self,
        run_id: str,
        *,
        phase: InferencePhase,
        approval_manifest: Path | None = None,
        offline_executor: bool,
    ) -> list[str]:
        events = self.store.list_events(run_id)
        sequence_start = max((int(event["sequence"]) for event in events), default=0)
        command = [
            sys.executable,
            "-m",
            "mudidi.web.production_worker",
            "--run-id",
            run_id,
            "--config",
            str(self.config_path(run_id)),
            "--phase",
            phase.value,
            "--sequence-start",
            str(sequence_start),
            "--log-file",
            str(self.log_path(run_id)),
        ]
        if approval_manifest is not None:
            command.extend(["--approval-manifest", str(approval_manifest)])
        if offline_executor:
            command.append("--offline-executor")
        return command

    def _spawn(
        self,
        run_id: str,
        command: list[str],
        *,
        credential: ResolvedCredential | None,
    ) -> None:
        with self._lock:
            if any(worker.process.poll() is None for worker in self._workers.values()):
                raise RuntimeError("another inference worker is active")
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            worker = _OwnedWorker(process=process, command=tuple(command))
            monitor = Thread(
                target=self._monitor,
                args=(run_id, worker),
                daemon=True,
                name=f"mudidi-worker-monitor-{run_id}",
            )
            worker.monitor = monitor
            self._workers[run_id] = worker
            credential_message = _credential_message(credential)
            if process.stdin is None:
                process.terminate()
                raise RuntimeError("worker credential pipe was not created")
            process.stdin.write(credential_message + "\n")
            process.stdin.close()
            monitor.start()

    def command_for(self, run_id: str) -> tuple[str, ...]:
        """Return the non-secret child command for diagnostics and tests."""

        with self._lock:
            return self._workers[run_id].command

    def wait(self, run_id: str, *, timeout: float) -> None:
        """Wait for monitor completion or raise ``TimeoutError``."""

        with self._lock:
            monitor = self._workers[run_id].monitor
        if monitor is None:
            raise RuntimeError("worker monitor was not initialized")
        monitor.join(timeout)
        if monitor.is_alive():
            raise TimeoutError(f"worker {run_id} did not finish in time")

    def cancel(self, run_id: str) -> None:
        """Terminate only the controller-owned worker and persist cancellation."""

        with self._lock:
            worker = self._workers.get(run_id)
            if worker is None or worker.process.poll() is not None:
                raise RuntimeError("run has no active owned worker")
            worker.process.terminate()
        current = self.store.get_run(run_id)
        if current.status in {
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.RUNNING_STAGE2,
        }:
            self.store.transition(run_id, RunStatus.CANCELLED)

    def reconcile_startup(self) -> list[str]:
        """Mark database-active runs interrupted when this process owns none."""

        reconciled: list[str] = []
        for run in self.store.list_active_runs():
            with self._lock:
                worker = self._workers.get(run.run_id)
                owned_live = worker is not None and worker.process.poll() is None
            if not owned_live:
                self.store.interrupt(run.run_id)
                reconciled.append(run.run_id)
        return reconciled

    def _monitor(self, run_id: str, worker: _OwnedWorker) -> None:
        stdout = worker.process.stdout
        if stdout is None:
            self._fail_if_active(run_id)
            return
        protocol_failed = False
        for line in stdout:
            try:
                payload = json.loads(line)
                event = parse_execution_event(payload)
            except (json.JSONDecodeError, ValidationError):
                protocol_failed = True
                break
            serialized = event.model_dump(mode="json")
            self.store.append_event(run_id, serialized)
            if isinstance(event, RunCompleted):
                self._complete_active(run_id)
            elif isinstance(event, RunFailed):
                self._fail_if_active(run_id)
            elif isinstance(event, ParseRulesGenerated):
                if self.parse_rule_reviews is None:
                    protocol_failed = True
                    break
                try:
                    self.store.transition_if_current(
                        run_id,
                        expected=RunStatus.RUNNING_STAGE1,
                        target=RunStatus.DISCOVERING_PARSE_RULES,
                    )
                    self.parse_rule_reviews.import_external(run_id, event.artifact_path)
                except (OSError, ValueError):
                    protocol_failed = True
                    break
        return_code = worker.process.wait()
        status = self.store.get_run(run_id).status
        if status is RunStatus.CANCELLED:
            return
        if protocol_failed or return_code != 0:
            self._fail_if_active(run_id)
        elif status is RunStatus.RUNNING_STAGE1:
            self._fail_if_active(run_id)

    def _fail_if_active(self, run_id: str) -> None:
        for expected in (
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.RUNNING_STAGE2,
        ):
            if self.store.transition_if_current(
                run_id,
                expected=expected,
                target=RunStatus.FAILED,
            ):
                return

    def _complete_active(self, run_id: str) -> None:
        for expected in (RunStatus.RUNNING_STAGE1, RunStatus.RUNNING_STAGE2):
            if self.store.transition_if_current(
                run_id,
                expected=expected,
                target=RunStatus.COMPLETED,
            ):
                return

    def _transition_if(
        self,
        run_id: str,
        expected: RunStatus,
        target: RunStatus,
    ) -> None:
        self.store.transition_if_current(run_id, expected=expected, target=target)


def _initial_phase(config: InferenceConfig) -> InferencePhase:
    if config.pipeline.stage == "1":
        return InferencePhase.STAGE1
    if config.pipeline.stage == "all":
        return InferencePhase.STAGE1_THEN_PASS1
    if config.pipeline.stage in {"2", "2-pass-1"}:
        return InferencePhase.PASS1
    raise ValueError("direct web Pass 2 execution is forbidden")


def _credential_message(credential: ResolvedCredential | None) -> str:
    if credential is None or credential.source is CredentialSource.ENVIRONMENT:
        return "{}"
    environment_name = credential_environment_name(credential.provider)
    if environment_name is None:
        return "{}"
    return json.dumps(
        {
            "environment_name": environment_name,
            "api_key": credential.get_secret_value(),
        },
        separators=(",", ":"),
    )
