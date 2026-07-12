"""Single-worker subprocess controller with persisted structured events."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, Thread

from pydantic import ValidationError

from mudidi.execution.events import RunCompleted, RunFailed, parse_execution_event
from mudidi.web.runs import RunStatus, RunStore


@dataclass(slots=True)
class _OwnedWorker:
    process: subprocess.Popen[str]
    command: tuple[str, ...]
    monitor: Thread | None = None


class JobController:
    """Own exactly one child worker and mirror its events into SQLite."""

    def __init__(self, *, store: RunStore, data_dir: Path) -> None:
        self.store = store
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._workers: dict[str, _OwnedWorker] = {}
        self._lock = RLock()

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
                self._transition_if(run_id, RunStatus.RUNNING_STAGE1, RunStatus.COMPLETED)
            elif isinstance(event, RunFailed):
                self._transition_if(run_id, RunStatus.RUNNING_STAGE1, RunStatus.FAILED)
        return_code = worker.process.wait()
        status = self.store.get_run(run_id).status
        if status is RunStatus.CANCELLED:
            return
        if protocol_failed or return_code != 0:
            self._fail_if_active(run_id)
        elif status is RunStatus.RUNNING_STAGE1:
            self._fail_if_active(run_id)

    def _fail_if_active(self, run_id: str) -> None:
        self._transition_if(run_id, RunStatus.RUNNING_STAGE1, RunStatus.FAILED)

    def _transition_if(
        self,
        run_id: str,
        expected: RunStatus,
        target: RunStatus,
    ) -> None:
        self.store.transition_if_current(run_id, expected=expected, target=target)
