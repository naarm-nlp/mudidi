"""Versioned structured progress events for execution observers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ExecutionStage = Literal["stage1", "stage2_pass1", "stage2_pass2"]


class ExecutionEvent(BaseModel):
    """Fields present on every persisted execution event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    type: str
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    stage: ExecutionStage


class StageStarted(ExecutionEvent):
    """A pipeline stage has begun."""

    type: Literal["stage.started"] = "stage.started"
    total_pages: int | None = Field(default=None, ge=1)


class PageCompleted(ExecutionEvent):
    """One source page completed within a stage."""

    type: Literal["page.completed"] = "page.completed"
    page: int = Field(ge=1)


class RunCompleted(ExecutionEvent):
    """The requested run completed successfully."""

    type: Literal["run.completed"] = "run.completed"


class RunFailed(ExecutionEvent):
    """The requested run ended with a safe user-facing error."""

    type: Literal["run.failed"] = "run.failed"
    message: str = Field(min_length=1, max_length=500)


class ParseRulesGenerated(ExecutionEvent):
    """Stage 2 Pass 1 produced rules that require human review."""

    type: Literal["parse_rules.generated"] = "parse_rules.generated"
    artifact_path: Path


ExecutionEventUnion = Annotated[
    StageStarted | PageCompleted | RunCompleted | RunFailed | ParseRulesGenerated,
    Field(discriminator="type"),
]
_EVENT_ADAPTER = TypeAdapter(ExecutionEventUnion)


def parse_execution_event(payload: object) -> ExecutionEventUnion:
    """Validate serialized worker output as a known event schema."""

    return _EVENT_ADAPTER.validate_python(payload)
