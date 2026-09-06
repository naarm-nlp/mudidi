"""Constrained inspection of files beneath a run's validated output root."""

from __future__ import annotations

import json
import re
import stat as stat_module
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from mudidi.paths import MDF_PARSING_GUIDE_USAGE_FILENAME
from mudidi.web.jobs import JobController

_TEXT_SUFFIXES = {".txt", ".tsv", ".mdf", ".json", ".jsonl", ".log"}
_MAX_PREVIEW_BYTES = 512_000
_MAX_EDIT_BYTES = 2_000_000
_PAGE_ID = re.compile(r"^page_[A-Za-z0-9_-]+$")
_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf"}


class ArtifactAccessError(ValueError):
    """Raised when a requested artifact is outside the allowed run root."""


@dataclass(frozen=True, slots=True)
class RunArtifact:
    """Safe metadata for one regular output file."""

    relative_path: Path
    absolute_path: Path
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True, slots=True)
class PageArtifacts:
    """Primary Stage 1 and Stage 2 artifacts grouped by page directory."""

    page_id: str
    stage1: RunArtifact | None
    stage2: RunArtifact | None


@dataclass(frozen=True, slots=True)
class UsageBreakdown:
    """Aggregated token and optional cost totals for one pipeline stage."""

    stage: str
    total_tokens: int
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Aggregated non-secret token and cost totals."""

    total_tokens: int
    total_cost_usd: float | None
    files_scanned: int
    breakdown: tuple[UsageBreakdown, ...] = ()


class ArtifactService:
    """Resolve and preview only regular files under a prepared run output."""

    def __init__(self, *, controller: JobController) -> None:
        self.controller = controller

    def output_root(self, run_id: str) -> Path:
        """Return the absolute output root from the run's typed config."""

        return self.controller.load_inference_config(run_id).output.directory.resolve()

    def resolve(self, run_id: str, relative_path: str | Path) -> Path:
        """Resolve a regular artifact while rejecting traversal and symlinks."""

        root = self.output_root(run_id)
        raw = str(relative_path).replace("\\", "/")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ArtifactAccessError("artifact path must be a safe relative path")
        candidate = root.joinpath(*pure.parts)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ArtifactAccessError("artifact path escapes the output root") from exc
        current = candidate
        while current != root:
            if current.is_symlink():
                raise ArtifactAccessError("symlink artifacts are not served")
            current = current.parent
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ArtifactAccessError(
                "artifact does not exist under the output root"
            ) from exc
        if not resolved.is_file():
            raise ArtifactAccessError("artifact is not a regular file")
        return resolved

    def list_artifacts(self, run_id: str) -> list[RunArtifact]:
        """List safe regular files in deterministic relative-path order."""

        root = self.output_root(run_id)
        if not root.is_dir():
            return []
        artifacts: list[RunArtifact] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(root)
                file_stat = resolved.stat()
            except (OSError, ValueError):
                continue
            if not stat_module.S_ISREG(file_stat.st_mode):
                continue
            artifacts.append(
                RunArtifact(
                    relative_path=relative,
                    absolute_path=resolved,
                    size_bytes=file_stat.st_size,
                    modified_at=datetime.fromtimestamp(file_stat.st_mtime, UTC),
                )
            )
        return artifacts

    def list_pages(self, run_id: str) -> list[PageArtifacts]:
        """Group primary transcription and MDF files by ``page_*`` directory."""

        grouped: dict[str, dict[str, RunArtifact]] = {}
        for artifact in self.list_artifacts(run_id):
            page_id = next(
                (
                    part
                    for part in artifact.relative_path.parts
                    if part.startswith("page_")
                ),
                None,
            )
            if page_id is None:
                continue
            stage = artifact.relative_path.parts[0]
            name = artifact.relative_path.name.lower()
            if stage == "stage-1" and name.endswith((".txt", ".tsv")):
                grouped.setdefault(page_id, {}).setdefault("stage1", artifact)
            elif stage == "stage-2" and ("mdf" in name or name.endswith(".txt")):
                grouped.setdefault(page_id, {}).setdefault("stage2", artifact)
        return [
            PageArtifacts(
                page_id=page_id,
                stage1=values.get("stage1"),
                stage2=values.get("stage2"),
            )
            for page_id, values in sorted(
                grouped.items(), key=lambda item: _page_sort_key(item[0])
            )
        ]

    def editable_text(self, run_id: str, artifact: RunArtifact) -> str:
        """Read a complete, bounded UTF-8 artifact for browser correction."""

        path = self.resolve(run_id, artifact.relative_path)
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            raise ArtifactAccessError("artifact is not editable text")
        if path.stat().st_size > _MAX_EDIT_BYTES:
            raise ArtifactAccessError("artifact is too large for browser editing")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactAccessError("artifact is not valid UTF-8 text") from exc

    def update_page_text(
        self,
        run_id: str,
        page_id: str,
        stage: str,
        text: str,
    ) -> None:
        """Replace one existing page output without permitting arbitrary paths."""

        if not _PAGE_ID.fullmatch(page_id):
            raise ArtifactAccessError("invalid page identifier")
        if stage not in {"stage1", "stage2"}:
            raise ArtifactAccessError("invalid editable stage")
        if len(text.encode("utf-8")) > _MAX_EDIT_BYTES:
            raise ArtifactAccessError("edited artifact is too large")
        page = next(
            (item for item in self.list_pages(run_id) if item.page_id == page_id),
            None,
        )
        artifact = getattr(page, stage, None) if page is not None else None
        if artifact is None:
            raise ArtifactAccessError("editable page artifact was not found")
        path = self.resolve(run_id, artifact.relative_path)
        path.write_text(text, encoding="utf-8")

    def preview_text(self, run_id: str, relative_path: Path) -> str:
        """Read a bounded text preview for a known textual artifact."""

        path = self.resolve(run_id, relative_path)
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            return "Binary artifact; use download to inspect it."
        with path.open("rb") as stream:
            raw = stream.read(_MAX_PREVIEW_BYTES + 1)
        truncated = len(raw) > _MAX_PREVIEW_BYTES
        text = raw[:_MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
        return text + ("\n… preview truncated …" if truncated else "")

    def source_page(self, run_id: str, page_id: str) -> Path:
        """Resolve one source page beneath the run's validated input directory."""

        if not _PAGE_ID.fullmatch(page_id):
            raise ArtifactAccessError("invalid source page identifier")
        pages = self.controller.load_inference_config(run_id).input.pages
        if pages is None or pages.is_symlink():
            raise ArtifactAccessError("source page input is unavailable")
        if pages.is_file():
            if pages.suffix.lower() != ".pdf":
                raise ArtifactAccessError("source page input is unavailable")
            if page_id not in {page.page_id for page in self.list_pages(run_id)}:
                raise ArtifactAccessError("source page was not found")
            return pages.resolve(strict=True)
        if not pages.is_dir():
            raise ArtifactAccessError("source page directory is unavailable")
        root = pages.resolve(strict=True)
        for candidate in root.iterdir():
            if (
                candidate.is_file()
                and not candidate.is_symlink()
                and candidate.stem == page_id
                and candidate.suffix.lower() in _SOURCE_SUFFIXES
            ):
                resolved = candidate.resolve(strict=True)
                try:
                    resolved.relative_to(root)
                except ValueError as exc:
                    raise ArtifactAccessError("source page escapes input root") from exc
                return resolved
        raise ArtifactAccessError("source page was not found")

    def usage_summary(self, run_id: str) -> UsageSummary:
        """Aggregate page usage JSON without double-counting run summaries."""

        root = self.output_root(run_id)
        run_summary = root / "run_usage.json"
        if run_summary.is_file() and not run_summary.is_symlink():
            payload = _read_json_object(run_summary)
            page_payloads = _run_page_payloads(payload)
            run_tokens = _optional_int(payload.get("run_total_tokens"))
            total_tokens = (
                run_tokens
                if run_tokens is not None
                else sum(_page_total_tokens(page) for page in page_payloads)
            )
            run_cost = _optional_float(payload.get("run_total_cost_usd"))
            if run_cost is None:
                page_costs = [
                    _page_total_cost(page)
                    for page in page_payloads
                ]
                run_cost = _sum_optional(page_costs)
            return UsageSummary(
                total_tokens=total_tokens,
                total_cost_usd=(
                    round(run_cost, 8) if run_cost is not None else None
                ),
                files_scanned=1,
                breakdown=_breakdown_from_payloads(page_payloads),
            )

        total_tokens = 0
        costs: list[float | None] = []
        payloads: list[dict[str, object]] = []
        files_scanned = 0
        if root.is_dir():
            for path in sorted(root.rglob("*_usage.json")):
                if path.is_symlink() or path.name == MDF_PARSING_GUIDE_USAGE_FILENAME:
                    continue
                payload = _read_json_object(path)
                payloads.append(payload)
                total_tokens += _page_total_tokens(payload)
                costs.append(_page_total_cost(payload))
                files_scanned += 1
        total_cost = _sum_optional(costs)
        return UsageSummary(
            total_tokens=total_tokens,
            total_cost_usd=(
                round(total_cost, 8) if total_cost is not None else None
            ),
            files_scanned=files_scanned,
            breakdown=_breakdown_from_payloads(payloads),
        )


_USAGE_STAGE_SPECS = (
    ("stage1", "Stage 1"),
    ("field_discovery", "Stage 2 · field discovery"),
    ("stage2", "Stage 2 · MDF extraction"),
)
_AGENTIC_STAGE_OWNERS = {
    "stage1_agentic": "stage1",
    "stage2_agentic": "stage2",
}
_GENERIC_AGENTIC_KEY = "agentic"
_GENERIC_AGENTIC_LABEL = "Agentic"


def _run_page_payloads(payload: dict[str, object]) -> list[dict[str, object]]:
    pages_value = payload.get("pages")
    pages = (
        [page for page in pages_value if isinstance(page, dict)]
        if isinstance(pages_value, list)
        else []
    )
    if pages:
        if isinstance(payload.get("field_discovery"), dict) and not any(
            bool(page.get("field_discovery")) for page in pages
        ):
            pages.append({"field_discovery": payload["field_discovery"]})
        return pages
    return [payload]


def _page_total_tokens(payload: dict[str, object]) -> int:
    direct = _optional_int(payload.get("total_tokens"))
    if direct is not None:
        return direct
    total = 0
    for key, _label in _USAGE_STAGE_SPECS:
        value = payload.get(key)
        if isinstance(value, dict):
            total += _optional_int(value.get("total_tokens")) or 0
    for key in _AGENTIC_STAGE_OWNERS:
        value = payload.get(key)
        if isinstance(value, dict):
            total += _optional_int(value.get("total_tokens")) or 0
    generic_agentic = payload.get(_GENERIC_AGENTIC_KEY)
    if isinstance(generic_agentic, dict):
        total += _optional_int(generic_agentic.get("total_tokens")) or 0
    return total


def _page_total_cost(payload: dict[str, object]) -> float | None:
    direct = _usage_cost(payload)
    if direct is not None:
        return direct
    costs: list[float | None] = []
    for key, _label in _USAGE_STAGE_SPECS:
        value = payload.get(key)
        if isinstance(value, dict):
            costs.append(_usage_cost(value))
    for key in _AGENTIC_STAGE_OWNERS:
        value = payload.get(key)
        if isinstance(value, dict):
            costs.append(_usage_cost(value))
    generic_agentic = payload.get(_GENERIC_AGENTIC_KEY)
    if isinstance(generic_agentic, dict):
        costs.append(_usage_cost(generic_agentic))
    return _sum_optional(costs)


def _breakdown_from_payloads(
    payloads: list[dict[str, object]],
) -> tuple[UsageBreakdown, ...]:
    totals: dict[str, list[object]] = {}

    def add(stage_key: str, value: dict[str, object]) -> None:
        bucket = totals.setdefault(stage_key, [False, 0, 0.0, False])
        bucket[0] = True
        bucket[1] = int(bucket[1]) + (_optional_int(value.get("total_tokens")) or 0)
        cost = _usage_cost(value)
        if cost is not None:
            bucket[2] = float(bucket[2]) + cost
            bucket[3] = True

    for payload in payloads:
        for stage_key, _label in _USAGE_STAGE_SPECS:
            value = payload.get(stage_key)
            if isinstance(value, dict):
                add(stage_key, value)
        for agentic_key, owner_key in _AGENTIC_STAGE_OWNERS.items():
            value = payload.get(agentic_key)
            if isinstance(value, dict):
                add(owner_key, value)
        generic_agentic = payload.get(_GENERIC_AGENTIC_KEY)
        if isinstance(generic_agentic, dict):
            add(_GENERIC_AGENTIC_KEY, generic_agentic)

    rows: list[UsageBreakdown] = []
    labels = dict(_USAGE_STAGE_SPECS)
    for stage_key, _label in _USAGE_STAGE_SPECS:
        bucket = totals.get(stage_key)
        if bucket is None or not bool(bucket[0]):
            continue
        rows.append(
            UsageBreakdown(
                stage=labels[stage_key],
                total_tokens=int(bucket[1]),
                cost_usd=(
                    round(float(bucket[2]), 8) if bool(bucket[3]) else None
                ),
            )
        )
    generic_bucket = totals.get(_GENERIC_AGENTIC_KEY)
    if generic_bucket is not None and bool(generic_bucket[0]):
        rows.append(
            UsageBreakdown(
                stage=_GENERIC_AGENTIC_LABEL,
                total_tokens=int(generic_bucket[1]),
                cost_usd=(
                    round(float(generic_bucket[2]), 8)
                    if bool(generic_bucket[3])
                    else None
                ),
            )
        )
    return tuple(rows)


def _usage_cost(payload: dict[str, object]) -> float | None:
    for key in ("total_cost_usd", "cost_usd"):
        if key in payload:
            cost = _optional_float(payload[key])
            if cost is not None:
                return cost
    return None


def _sum_optional(values: list[float | None]) -> float | None:
    total = 0.0
    available = False
    for value in values:
        if value is not None:
            total += value
            available = True
    return total if available else None


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAccessError(f"invalid usage artifact: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ArtifactAccessError(f"usage artifact is not an object: {path.name}")
    return payload


def _page_sort_key(page_id: str) -> tuple[tuple[int, object], ...]:
    """Sort numeric page identifiers naturally while retaining named pages."""

    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", page_id)
        if part
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
