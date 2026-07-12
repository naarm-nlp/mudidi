"""Constrained inspection of files beneath a run's validated output root."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mudidi.web.jobs import JobController

_TEXT_SUFFIXES = {".txt", ".tsv", ".mdf", ".json", ".jsonl", ".log"}
_MAX_PREVIEW_BYTES = 512_000


class ArtifactAccessError(ValueError):
    """Raised when a requested artifact is outside the allowed run root."""


@dataclass(frozen=True, slots=True)
class RunArtifact:
    """Safe metadata for one regular output file."""

    relative_path: Path
    absolute_path: Path
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PageArtifacts:
    """Primary Stage 1 and Stage 2 artifacts grouped by page directory."""

    page_id: str
    stage1: RunArtifact | None
    stage2: RunArtifact | None


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Aggregated non-secret token and cost totals."""

    total_tokens: int
    total_cost_usd: float | None
    files_scanned: int


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
            raise ArtifactAccessError("artifact does not exist under the output root") from exc
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
            if not path.is_file() or path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            artifacts.append(
                RunArtifact(
                    relative_path=relative,
                    absolute_path=resolved,
                    size_bytes=resolved.stat().st_size,
                )
            )
        return artifacts

    def list_pages(self, run_id: str) -> list[PageArtifacts]:
        """Group primary transcription and MDF files by ``page_*`` directory."""

        grouped: dict[str, dict[str, RunArtifact]] = {}
        for artifact in self.list_artifacts(run_id):
            page_id = next(
                (part for part in artifact.relative_path.parts if part.startswith("page_")),
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
            for page_id, values in sorted(grouped.items())
        ]

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

    def usage_summary(self, run_id: str) -> UsageSummary:
        """Aggregate page usage JSON without double-counting run summaries."""

        root = self.output_root(run_id)
        run_summary = root / "run_usage.json"
        if run_summary.is_file() and not run_summary.is_symlink():
            payload = _read_json_object(run_summary)
            return UsageSummary(
                total_tokens=int(payload.get("run_total_tokens", 0) or 0),
                total_cost_usd=_optional_float(payload.get("run_total_cost_usd")),
                files_scanned=1,
            )
        total_tokens = 0
        total_cost = 0.0
        cost_available = False
        files_scanned = 0
        if root.is_dir():
            for path in sorted(root.rglob("*_usage.json")):
                if path.is_symlink() or path.name == "parse-rules_usage.json":
                    continue
                payload = _read_json_object(path)
                total_tokens += int(payload.get("total_tokens", 0) or 0)
                cost = _optional_float(
                    payload.get("total_cost_usd", payload.get("cost_usd"))
                )
                if cost is not None:
                    total_cost += cost
                    cost_available = True
                files_scanned += 1
        return UsageSummary(
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 8) if cost_available else None,
            files_scanned=files_scanned,
        )


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAccessError(f"invalid usage artifact: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ArtifactAccessError(f"usage artifact is not an object: {path.name}")
    return payload


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
