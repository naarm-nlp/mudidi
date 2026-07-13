"""SQLite-backed run state with mandatory Stage 2 authorization boundaries."""

from __future__ import annotations

import re
import sqlite3
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from mudidi.config.yaml_config import InferenceConfig

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RunStatus(StrEnum):
    """Durable lifecycle states for one local production run."""

    DRAFT = "draft"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING_STAGE1 = "running_stage1"
    DISCOVERING_PARSE_RULES = "discovering_parse_rules"
    AWAITING_PARSE_RULES_REVIEW = "awaiting_parse_rules_review"
    RUNNING_STAGE2 = "running_stage2"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    CREDENTIALS_REQUIRED = "credentials_required"


class InvalidRunTransition(ValueError):
    """Raised when a caller attempts an illegal lifecycle transition."""


class ActiveRunExistsError(RuntimeError):
    """Raised when a second inference worker would become active."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Non-secret persisted metadata for one web run."""

    run_id: str
    status: RunStatus
    provider: str | None
    resume_phase: str | None
    review_id: str | None
    approval_digest: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PresetRecord:
    """Reusable non-secret production configuration."""

    preset_id: str
    name: str
    provider: str
    config: InferenceConfig
    created_at: datetime


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.DRAFT: frozenset({RunStatus.VALIDATED, RunStatus.CANCELLED}),
    RunStatus.VALIDATED: frozenset(
        {RunStatus.QUEUED, RunStatus.CREDENTIALS_REQUIRED, RunStatus.CANCELLED}
    ),
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.AWAITING_PARSE_RULES_REVIEW,
            RunStatus.CREDENTIALS_REQUIRED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.RUNNING_STAGE1: frozenset(
        {
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.DISCOVERING_PARSE_RULES: frozenset(
        {
            RunStatus.AWAITING_PARSE_RULES_REVIEW,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.AWAITING_PARSE_RULES_REVIEW: frozenset(
        {RunStatus.CREDENTIALS_REQUIRED, RunStatus.CANCELLED}
    ),
    RunStatus.RUNNING_STAGE2: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.INTERRUPTED: frozenset(
        {
            RunStatus.QUEUED,
            RunStatus.AWAITING_PARSE_RULES_REVIEW,
            RunStatus.CREDENTIALS_REQUIRED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.CREDENTIALS_REQUIRED: frozenset(
        {
            RunStatus.QUEUED,
            RunStatus.AWAITING_PARSE_RULES_REVIEW,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class RunStore:
    """Small repository enforcing run transitions at the SQLite boundary."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    provider TEXT,
                    resume_phase TEXT,
                    review_id TEXT,
                    approval_digest TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run
                ON runs ((1))
                WHERE status IN (
                    'running_stage1',
                    'discovering_parse_rules',
                    'running_stage2'
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS parse_rule_reviews (
                    review_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    generated_path TEXT NOT NULL,
                    draft_path TEXT,
                    approved_snapshot_path TEXT,
                    approval_digest TEXT,
                    validation_error TEXT,
                    sample_pages_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS presets (
                    preset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (1, ?)",
                (_now().isoformat(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (3, ?)",
                (_now().isoformat(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (2, ?)",
                (_now().isoformat(),),
            )

    def create_run(self, run_id: str, *, provider: str | None = None) -> RunRecord:
        """Create one draft run without persisting provider credentials."""

        if not run_id.strip():
            raise ValueError("run_id must not be empty")
        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, status, provider, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, RunStatus.DRAFT.value, provider, now, now),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        """Load one persisted run or raise ``KeyError``."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _record_from_row(row)

    def delete_terminal(self, run_id: str) -> None:
        """Delete metadata only for a run that can no longer execute."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            status = RunStatus(row["status"])
            if status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                connection.rollback()
                raise InvalidRunTransition(
                    f"only completed, failed, or cancelled runs can be deleted; got {status.value}"
                )
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            connection.commit()

    def transition(self, run_id: str, target: RunStatus) -> RunRecord:
        """Apply a generic legal transition.

        Starting Pass 2 is deliberately absent and must use
        :meth:`authorize_pass2`.
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = RunStatus(row["status"])
            if target not in _ALLOWED_TRANSITIONS[current]:
                raise InvalidRunTransition(f"cannot transition {current} to {target}")
            try:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (target.value, _now().isoformat(), run_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return self.get_run(run_id)

    def transition_if_current(
        self,
        run_id: str,
        *,
        expected: RunStatus,
        target: RunStatus,
    ) -> bool:
        """Atomically transition only if the durable state still matches."""

        if target not in _ALLOWED_TRANSITIONS[expected]:
            raise InvalidRunTransition(f"cannot transition {expected} to {target}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if RunStatus(row["status"]) is not expected:
                connection.rollback()
                return False
            try:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (target.value, _now().isoformat(), run_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return True

    def authorize_pass2(
        self,
        run_id: str,
        *,
        review_id: str,
        approval_digest: str,
    ) -> RunRecord:
        """Record approval provenance and enter Pass 2 atomically."""

        if not review_id.strip() or not _SHA256_PATTERN.fullmatch(approval_digest):
            raise ValueError("valid review_id and approval digest are required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if RunStatus(row["status"]) is not RunStatus.AWAITING_PARSE_RULES_REVIEW:
                raise InvalidRunTransition(
                    "Pass 2 authorization requires awaiting parse-rules review"
                )
            try:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, resume_phase = ?, review_id = ?,
                        approval_digest = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        RunStatus.RUNNING_STAGE2.value,
                        "stage2_pass2",
                        review_id,
                        approval_digest,
                        _now().isoformat(),
                        run_id,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return self.get_run(run_id)

    def interrupt(self, run_id: str) -> RunRecord:
        """Persist an interruption and the safe phase to which it may resume."""

        run = self.get_run(run_id)
        resume_phase = {
            RunStatus.RUNNING_STAGE1: "stage1",
            RunStatus.DISCOVERING_PARSE_RULES: "parse_rule_review",
            RunStatus.RUNNING_STAGE2: "stage2_pass2",
        }.get(run.status)
        if resume_phase is None:
            raise InvalidRunTransition(f"cannot interrupt {run.status}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, resume_phase = ?, updated_at = ? "
                "WHERE run_id = ?",
                (
                    RunStatus.INTERRUPTED.value,
                    resume_phase,
                    _now().isoformat(),
                    run_id,
                ),
            )
        return self.get_run(run_id)

    def resume(self, run_id: str, *, credentials_available: bool) -> RunRecord:
        """Resume only to the phase justified by persisted provenance."""

        run = self.get_run(run_id)
        if run.status not in {RunStatus.INTERRUPTED, RunStatus.CREDENTIALS_REQUIRED}:
            raise InvalidRunTransition(f"cannot resume {run.status}")
        if not credentials_available:
            if run.status is RunStatus.CREDENTIALS_REQUIRED:
                return run
            return self.transition(run_id, RunStatus.CREDENTIALS_REQUIRED)
        if run.resume_phase == "parse_rule_review":
            return self.transition(run_id, RunStatus.AWAITING_PARSE_RULES_REVIEW)
        if run.resume_phase == "stage2_pass2" and not (
            run.review_id and run.approval_digest
        ):
            return self.transition(run_id, RunStatus.AWAITING_PARSE_RULES_REVIEW)
        return self.transition(run_id, RunStatus.QUEUED)

    def resume_pass2(self, run_id: str) -> RunRecord:
        """Resume approved Pass 2 without reopening the earlier pipeline."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            status = RunStatus(row["status"])
            if status not in {RunStatus.INTERRUPTED, RunStatus.CREDENTIALS_REQUIRED}:
                raise InvalidRunTransition(f"cannot resume Pass 2 from {status}")
            if row["resume_phase"] != "stage2_pass2" or not (
                row["review_id"] and row["approval_digest"]
            ):
                raise InvalidRunTransition("Pass 2 resume requires durable approval")
            try:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (RunStatus.RUNNING_STAGE2.value, _now().isoformat(), run_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return self.get_run(run_id)

    def start_uploaded_guide_stage2(self, run_id: str) -> RunRecord:
        """Start direct Stage 2 through the explicit user-guide boundary."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if RunStatus(row["status"]) is not RunStatus.QUEUED:
                raise InvalidRunTransition(
                    "uploaded-guide Stage 2 requires a queued run"
                )
            try:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (RunStatus.RUNNING_STAGE2.value, _now().isoformat(), run_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return self.get_run(run_id)

    def schema_columns(self, table: str) -> list[str]:
        """Return columns for an allowlisted application table."""

        if table not in {
            "runs",
            "run_events",
            "parse_rule_reviews",
            "presets",
            "schema_migrations",
        }:
            raise ValueError("unknown table")
        with self._connect() as connection:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row["name"]) for row in rows]

    def append_event(self, run_id: str, event: dict[str, object]) -> None:
        """Persist one validated, monotonically sequenced worker event."""

        if event.get("run_id") != run_id:
            raise ValueError("event run_id does not match its owning run")
        sequence = event.get("sequence")
        occurred_at = event.get("occurred_at")
        if not isinstance(sequence, int) or sequence < 1:
            raise ValueError("event sequence must be a positive integer")
        if not isinstance(occurred_at, str):
            raise ValueError("event occurred_at must be serialized")
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO run_events(run_id, sequence, event_json, occurred_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, sequence, serialized, occurred_at),
            )

    def list_events(self, run_id: str) -> list[dict[str, object]]:
        """Return persisted events in deterministic replay order."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM run_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [json.loads(str(row["event_json"])) for row in rows]

    def list_runs(self) -> list[RunRecord]:
        """Return newest runs first for history and startup reconciliation."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def list_active_runs(self) -> list[RunRecord]:
        """Return runs whose durable state claims a live worker."""

        active = {
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.RUNNING_STAGE2,
        }
        return [run for run in self.list_runs() if run.status in active]

    def create_preset(
        self,
        preset_id: str,
        *,
        name: str,
        provider: str,
        config: InferenceConfig,
    ) -> PresetRecord:
        """Persist one validated configuration, replacing the same saved name."""

        cleaned_name = name.strip()
        if not preset_id.strip() or not cleaned_name:
            raise ValueError("preset id and name must not be empty")
        if len(cleaned_name) > 80:
            raise ValueError("preset name must be at most 80 characters")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO presets(preset_id, name, provider, config_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "preset_id = excluded.preset_id, "
                "provider = excluded.provider, "
                "config_json = excluded.config_json, "
                "created_at = excluded.created_at",
                (
                    preset_id,
                    cleaned_name,
                    provider,
                    config.model_dump_json(),
                    _now().isoformat(),
                ),
            )
        return self.get_preset(preset_id)

    def get_preset_by_name(self, name: str) -> PresetRecord | None:
        """Return a preset by its normalized display name, if it exists."""

        cleaned_name = name.strip()
        if not cleaned_name:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM presets WHERE name = ?", (cleaned_name,)
            ).fetchone()
        return None if row is None else _preset_from_row(row)

    def get_preset(self, preset_id: str) -> PresetRecord:
        """Load one typed preset or raise ``KeyError``."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM presets WHERE preset_id = ?", (preset_id,)
            ).fetchone()
        if row is None:
            raise KeyError(preset_id)
        return _preset_from_row(row)

    def list_presets(self) -> list[PresetRecord]:
        """Return presets ordered by name for a stable picker."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM presets ORDER BY name, preset_id"
            ).fetchall()
        return [_preset_from_row(row) for row in rows]

    def create_parse_rule_review(
        self,
        *,
        review_id: str,
        run_id: str,
        generated_path: Path,
        sample_pages: tuple[str, ...],
        validation_error: str | None,
    ) -> dict[str, object]:
        """Persist generated rules and pause discovery in one DB transaction."""

        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if RunStatus(row["status"]) is not RunStatus.DISCOVERING_PARSE_RULES:
                raise InvalidRunTransition(
                    "parse-rule review requires discovery to be active"
                )
            connection.execute(
                """
                INSERT INTO parse_rule_reviews(
                    review_id, run_id, version, status, generated_path,
                    validation_error, sample_pages_json, created_at, updated_at
                ) VALUES (?, ?, 1, 'awaiting_review', ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    run_id,
                    str(generated_path),
                    validation_error,
                    json.dumps(sample_pages),
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE runs SET status = ?, resume_phase = ?, updated_at = ? "
                "WHERE run_id = ?",
                (
                    RunStatus.AWAITING_PARSE_RULES_REVIEW.value,
                    "parse_rule_review",
                    now,
                    run_id,
                ),
            )
            connection.commit()
        return self.get_parse_rule_review(run_id)

    def get_parse_rule_review(self, run_id: str) -> dict[str, object]:
        """Load review metadata without reading artifact contents."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM parse_rule_reviews WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def update_parse_rule_draft(
        self,
        run_id: str,
        *,
        draft_path: Path,
    ) -> dict[str, object]:
        """Record the latest schema-valid draft while remaining in review."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            if RunStatus(run["status"]) is not RunStatus.AWAITING_PARSE_RULES_REVIEW:
                raise InvalidRunTransition("draft save requires awaiting review")
            cursor = connection.execute(
                """
                UPDATE parse_rule_reviews
                SET draft_path = ?, validation_error = NULL, version = version + 1,
                    updated_at = ?
                WHERE run_id = ? AND status = 'awaiting_review'
                """,
                (str(draft_path), _now().isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                raise InvalidRunTransition("review is not editable")
            connection.commit()
        return self.get_parse_rule_review(run_id)

    def commit_parse_rule_approval(
        self,
        run_id: str,
        *,
        snapshot_path: Path,
        approval_digest: str,
    ) -> dict[str, object]:
        """Record immutable approval and authorize Pass 2 atomically."""

        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            review = connection.execute(
                "SELECT * FROM parse_rule_reviews WHERE run_id = ?", (run_id,)
            ).fetchone()
            run = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if review is None or run is None:
                raise KeyError(run_id)
            if review["status"] == "approved":
                if review["approval_digest"] != approval_digest:
                    raise InvalidRunTransition("approved review content cannot change")
                connection.rollback()
                return dict(review)
            if RunStatus(run["status"]) is not RunStatus.AWAITING_PARSE_RULES_REVIEW:
                raise InvalidRunTransition("approval requires awaiting review")
            connection.execute(
                """
                UPDATE parse_rule_reviews
                SET status = 'approved', approved_snapshot_path = ?,
                    approval_digest = ?, approved_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (str(snapshot_path), approval_digest, now, now, run_id),
            )
            try:
                connection.execute(
                    """
                    UPDATE runs SET status = ?, resume_phase = ?, review_id = ?,
                        approval_digest = ?, updated_at = ? WHERE run_id = ?
                    """,
                    (
                        RunStatus.RUNNING_STAGE2.value,
                        "stage2_pass2",
                        review["review_id"],
                        approval_digest,
                        now,
                        run_id,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ActiveRunExistsError(
                    "another inference worker is active"
                ) from exc
        return self.get_parse_rule_review(run_id)


def _record_from_row(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        status=RunStatus(row["status"]),
        provider=row["provider"],
        resume_phase=row["resume_phase"],
        review_id=row["review_id"],
        approval_digest=row["approval_digest"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _preset_from_row(row: sqlite3.Row) -> PresetRecord:
    return PresetRecord(
        preset_id=str(row["preset_id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),
        config=InferenceConfig.model_validate_json(str(row["config_json"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _now() -> datetime:
    return datetime.now(UTC)
