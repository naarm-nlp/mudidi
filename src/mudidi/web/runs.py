"""SQLite-backed run state with mandatory Stage 2 authorization boundaries."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

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


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.DRAFT: frozenset({RunStatus.VALIDATED, RunStatus.CANCELLED}),
    RunStatus.VALIDATED: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
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
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (1, ?)",
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
                raise ActiveRunExistsError("another inference worker is active") from exc
        return self.get_run(run_id)

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
                raise ActiveRunExistsError("another inference worker is active") from exc
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
        if run.status is not RunStatus.INTERRUPTED:
            raise InvalidRunTransition(f"cannot resume {run.status}")
        if not credentials_available:
            return self.transition(run_id, RunStatus.CREDENTIALS_REQUIRED)
        if run.resume_phase == "parse_rule_review":
            return self.transition(run_id, RunStatus.AWAITING_PARSE_RULES_REVIEW)
        if run.resume_phase == "stage2_pass2" and not (
            run.review_id and run.approval_digest
        ):
            return self.transition(run_id, RunStatus.AWAITING_PARSE_RULES_REVIEW)
        return self.transition(run_id, RunStatus.QUEUED)

    def schema_columns(self, table: str) -> list[str]:
        """Return columns for an allowlisted application table."""

        if table not in {"runs", "schema_migrations"}:
            raise ValueError("unknown table")
        with self._connect() as connection:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row["name"]) for row in rows]


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


def _now() -> datetime:
    return datetime.now(UTC)
