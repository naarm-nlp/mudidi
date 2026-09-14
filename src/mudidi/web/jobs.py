"""Single-worker subprocess controller with persisted structured events."""

from __future__ import annotations

import json
import os

# Dedicated worker processes use fixed argument vectors and never invoke a shell.
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, Thread
from typing import TYPE_CHECKING

from pydantic import ValidationError

from mudidi.config.yaml_config import InferenceConfig, redacted_config_dict
from mudidi.execution.approval import ApprovedParseRules
from mudidi.execution.events import (
    ParseRulesGenerated,
    RunCompleted,
    RunFailed,
    StageStarted,
    parse_execution_event,
)
from mudidi.llm.subscriptions import AuthMode
from mudidi.web.credentials import (
    CredentialSource,
    ResolvedCredential,
    credential_environment_name,
    subscription_store_path as managed_subscription_store_path,
)
from mudidi.web.inference_worker import InferencePhase
from mudidi.web.models import Provider
from mudidi.web.runs import RunRecord, RunStatus, RunStore


_SUBSCRIPTION_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "MUDIDI_GOOGLE_OAUTH_CLIENT_ID",
        "MUDIDI_AGENTIC_VERIFIER_MAX_TOKENS",
    }
)
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

    def subscription_store_path(self) -> Path:
        """Return the separate encrypted subscription store directory."""

        return managed_subscription_store_path(self.data_dir)

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
        snapshot = redacted_config_dict(config)
        if config.auth.mode is AuthMode.API_KEY:
            # API-key mode is the legacy default; do not persist a redundant
            # auth label in the web run snapshot.
            snapshot.pop("auth", None)
        temporary.write_text(
            json.dumps(snapshot, indent=2),
            encoding="utf-8",
        )
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
        credentials: tuple[ResolvedCredential, ...],
        offline_executor: bool = False,
    ) -> None:
        """Launch Stage 1 and/or Pass 1 while preserving the review pause."""

        config = self.load_inference_config(run_id)
        phase = _initial_phase(config)
        subscription = config.auth.mode is AuthMode.SUBSCRIPTION
        credential_message = _credential_handoff(config, credentials)
        current = self.store.get_run(run_id).status
        if current in {RunStatus.VALIDATED, RunStatus.CREDENTIALS_REQUIRED}:
            self.store.transition(run_id, RunStatus.QUEUED)
        target = (
            RunStatus.RUNNING_STAGE1
            if phase in {InferencePhase.STAGE1, InferencePhase.STAGE1_THEN_PASS1}
            or (phase is InferencePhase.USER_GUIDE and config.pipeline.stage == "all")
            else (
                RunStatus.RUNNING_STAGE2
                if phase is InferencePhase.USER_GUIDE
                else RunStatus.DISCOVERING_PARSE_RULES
            )
        )
        if target is RunStatus.RUNNING_STAGE2:
            self.store.start_uploaded_guide_stage2(run_id)
        else:
            self.store.transition(run_id, target)
        command = self._production_command(
            run_id,
            phase=phase,
            offline_executor=offline_executor,
            subscription=subscription,
        )
        self._spawn(
            run_id,
            command,
            credentials=credentials,
            credential_message=credential_message,
            subscription=subscription,
        )

    def start_pass2(
        self,
        run_id: str,
        *,
        approval: ApprovedParseRules,
        credentials: tuple[ResolvedCredential, ...],
        offline_executor: bool = False,
    ) -> None:
        """Launch Pass 2 only for the run-bound committed approval capability."""

        config = self.load_inference_config(run_id)
        subscription = config.auth.mode is AuthMode.SUBSCRIPTION
        credential_message = _credential_handoff(config, credentials)

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
            subscription=subscription,
        )
        self._spawn(
            run_id,
            command,
            credentials=credentials,
            credential_message=credential_message,
            subscription=subscription,
        )

    def prepare_failed_retry(self, run_id: str) -> RunRecord:
        """Persist inferred retry provenance for a legacy failed run."""

        run = self.store.get_run(run_id)
        if run.status is not RunStatus.FAILED:
            raise RuntimeError("only failed runs can be prepared for retry")
        if run.resume_phase is not None:
            return run
        phase = _failed_resume_phase(
            self.store.list_events(run_id),
            self.load_inference_config(run_id),
        )
        return self.store.set_failed_resume_phase(run_id, phase)

    def resume_inference(
        self,
        run_id: str,
        *,
        credentials: tuple[ResolvedCredential, ...],
        offline_executor: bool = False,
    ) -> None:
        """Resume the durable phase without bypassing parse-rule approval."""

        run = self.store.get_run(run_id)
        if run.status is RunStatus.FAILED:
            run = self.prepare_failed_retry(run_id)
        if run.status is RunStatus.CREDENTIALS_REQUIRED and run.resume_phase is None:
            self.start_inference(
                run_id,
                credentials=credentials,
                offline_executor=offline_executor,
            )
            return
        if run.resume_phase == "stage2_pass1":
            config = self.load_inference_config(run_id)
            subscription = config.auth.mode is AuthMode.SUBSCRIPTION
            credential_message = _credential_handoff(config, credentials)
            command = self._production_command(
                run_id,
                phase=InferencePhase.PASS1,
                offline_executor=offline_executor,
                subscription=subscription,
            )
            self.store.resume(run_id, credentials_available=True)
            self.store.transition(run_id, RunStatus.DISCOVERING_PARSE_RULES)
            self._spawn(
                run_id,
                command,
                credentials=credentials,
                credential_message=credential_message,
                subscription=subscription,
            )
            return
        if run.resume_phase == "parse_rule_review":
            self.store.resume(run_id, credentials_available=True)
            return
        if run.resume_phase == "stage2_pass2":
            config = self.load_inference_config(run_id)
            if config.pipeline.parse_rules_file is not None:
                self.store.transition(run_id, RunStatus.QUEUED)
                self.start_inference(
                    run_id,
                    credentials=credentials,
                    offline_executor=offline_executor,
                )
                return
            if self.parse_rule_reviews is None:
                raise RuntimeError("Pass 2 resume requires parse-rule review service")
            approval = self.parse_rule_reviews.approved_capability(run_id)
            self.store.resume_pass2(run_id)
            self.start_pass2(
                run_id,
                approval=approval,
                credentials=credentials,
                offline_executor=offline_executor,
            )
            return
        self.store.resume(run_id, credentials_available=True)
        self.start_inference(
            run_id,
            credentials=credentials,
            offline_executor=offline_executor,
        )

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
                # Command is a fixed Python module argv; user input is never executable.
                process = subprocess.Popen(  # nosec B603
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                    env=_worker_environment(),
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
        subscription: bool = False,
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
        if subscription:
            command.extend(
                ["--subscription-store", str(self.subscription_store_path())]
            )
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
        credentials: tuple[ResolvedCredential, ...],
        credential_message: str | None = None,
        subscription: bool = False,
    ) -> None:
        with self._lock:
            if any(worker.process.poll() is None for worker in self._workers.values()):
                raise RuntimeError("another inference worker is active")
            if subscription and credentials:
                raise ValueError("subscription workers cannot receive API credentials")
            # Command is a fixed Python module argv; user input is never executable.
            process = subprocess.Popen(  # nosec B603
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                env=_worker_environment(credentials, subscription=subscription),
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
            message = (
                credential_message
                if credential_message is not None
                else _credential_message(credentials)
            )
            if process.stdin is None:
                process.terminate()
                raise RuntimeError("worker credential pipe was not created")
            process.stdin.write(message + "\n")
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
            current = self.store.get_run(run_id)
            if current.status in {
                RunStatus.RUNNING_STAGE1,
                RunStatus.DISCOVERING_PARSE_RULES,
                RunStatus.RUNNING_STAGE2,
            }:
                self.store.transition(run_id, RunStatus.CANCELLED)
            worker.process.terminate()

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
            if isinstance(event, StageStarted) and event.stage == "stage2_pass1":
                self.store.transition_if_current(
                    run_id,
                    expected=RunStatus.RUNNING_STAGE1,
                    target=RunStatus.DISCOVERING_PARSE_RULES,
                )
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
        elif status in {
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.RUNNING_STAGE2,
        }:
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


def _failed_resume_phase(
    events: list[dict[str, object]],
    config: InferenceConfig,
) -> str:
    """Recover retry provenance for failed runs written before it was durable."""

    for event in reversed(events):
        stage = event.get("stage")
        if stage in {"stage1", "stage2_pass1", "stage2_pass2"}:
            return str(stage)
    initial = _initial_phase(config)
    if initial in {InferencePhase.STAGE1, InferencePhase.STAGE1_THEN_PASS1}:
        return "stage1"
    if initial is InferencePhase.PASS1:
        return "stage2_pass1"
    if initial is InferencePhase.USER_GUIDE:
        return "stage2_pass2"
    raise RuntimeError("failed run has no retryable phase")


def _initial_phase(config: InferenceConfig) -> InferencePhase:
    if config.pipeline.parse_rules_file is not None:
        if config.pipeline.stage == "1":
            raise ValueError("uploaded MDF guides require an MDF parsing pipeline")
        return InferencePhase.USER_GUIDE
    if config.pipeline.stage == "1":
        return InferencePhase.STAGE1
    if config.pipeline.stage == "all":
        return InferencePhase.STAGE1_THEN_PASS1
    if config.pipeline.stage in {"2", "2-pass-1"}:
        return InferencePhase.PASS1
    raise ValueError("direct web Pass 2 execution is forbidden")


def _credential_message(credentials: tuple[ResolvedCredential, ...]) -> str:
    entries: dict[str, str] = {}
    for credential in credentials:
        if credential.source is CredentialSource.ENVIRONMENT:
            continue
        environment_name = credential_environment_name(credential.provider)
        if environment_name is not None:
            entries[environment_name] = credential.get_secret_value()
    if not entries:
        return "{}"
    return json.dumps(
        {"auth_mode": "api_key", "credentials": entries},
        separators=(",", ":"),
    )


def _credential_handoff(
    config: InferenceConfig,
    credentials: tuple[ResolvedCredential, ...],
) -> str:
    """Serialize the mode-specific message accepted by the child worker."""

    if config.auth.mode is AuthMode.SUBSCRIPTION:
        if credentials:
            raise ValueError("subscription workers cannot receive API credentials")
        return json.dumps(
            {
                "auth_mode": "subscription",
                "providers": [provider.value for provider in config.auth.providers],
            },
            separators=(",", ":"),
        )
    return _credential_message(credentials)


def _worker_environment(
    credentials: tuple[ResolvedCredential, ...] = (),
    *,
    subscription: bool = False,
) -> dict[str, str]:
    """Build a mode-specific child environment."""

    if subscription:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in _SUBSCRIPTION_ENVIRONMENT_ALLOWLIST
        }
    else:
        environment = dict(os.environ)
    if not subscription:
        for credential in credentials:
            if credential.source is not CredentialSource.ENVIRONMENT:
                continue
            environment_name = credential_environment_name(credential.provider)
            if environment_name is not None:
                environment[environment_name] = credential.get_secret_value()
    return environment
