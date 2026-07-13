"""Safe materialization of browser inputs into durable run-owned bundles."""

from __future__ import annotations

import json
import hashlib
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from starlette.datastructures import UploadFile

from mudidi.schemas.field_cheatsheet import validate_marker_cheatsheet
from mudidi.config.yaml_config import InferenceConfig

_PAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_FILES = 5_000
_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
_TEXT_CHARS = 20_000


class InputMaterializer:
    """Own validated browser inputs beneath each durable run directory."""

    def __init__(
        self,
        *,
        data_dir: Path,
        max_total_bytes: int = _MAX_UPLOAD_BYTES,
    ) -> None:
        if max_total_bytes < 1:
            raise ValueError("upload byte limit must be positive")
        self.data_dir = data_dir.expanduser().resolve()
        self.runs_root = self.data_dir / "runs"
        self.presets_root = self.data_dir / "presets"
        self.max_total_bytes = max_total_bytes

    def bundle(self, run_id: str) -> Path:
        """Return the fixed managed input root for one run identifier."""

        _validate_owner_id(run_id)
        return self.runs_root / run_id / "inputs"

    async def materialize(self, run_id: str, uploads: list[UploadFile]) -> Path:
        """Backward-compatible alias for page materialization."""

        return await self.materialize_pages(run_id, uploads)

    async def materialize_pages(
        self,
        run_id: str,
        uploads: list[UploadFile],
        *,
        replace: bool = False,
    ) -> Path:
        """Store one source PDF or a flat collection of page images."""

        if not uploads:
            raise ValueError("select a PDF or page images")
        suffixes = _upload_suffixes(uploads, allow_relative=True)
        if ".pdf" in suffixes:
            if len(uploads) != 1 or suffixes != [".pdf"]:
                raise ValueError("a source PDF must be uploaded by itself")
            return await self._materialize_files(
                run_id,
                "pages",
                uploads,
                allowed={".pdf"},
                multiple=False,
                allow_relative=False,
                replace=replace,
            )
        return await self._materialize_files(
            run_id,
            "pages",
            uploads,
            allowed=_PAGE_SUFFIXES,
            multiple=True,
            allow_relative=True,
            replace=replace,
        )

    async def materialize_mdf_guide(
        self, run_id: str, upload: UploadFile, *, replace: bool = False
    ) -> Path:
        """Store one schema-valid existing MDF parsing guide."""

        return await self._materialize_files(
            run_id,
            "mdf_guide",
            [upload],
            allowed={".json"},
            multiple=False,
            allow_relative=False,
            replace=replace,
        )

    async def materialize_mdf_manual(
        self, run_id: str, upload: UploadFile, *, replace: bool = False
    ) -> Path:
        """Store one custom MDF manual PDF."""

        return await self._materialize_files(
            run_id,
            "mdf_manual",
            [upload],
            allowed={".pdf"},
            multiple=False,
            allow_relative=False,
            replace=replace,
        )

    def materialize_instruction(
        self,
        run_id: str,
        stage: str,
        text: str,
        *,
        replace: bool = False,
    ) -> Path:
        """Write bounded user instruction text as a run-owned UTF-8 guide file."""

        cleaned = text.strip()
        if not cleaned:
            raise ValueError("additional instructions must not be blank")
        if len(cleaned) > _TEXT_CHARS:
            raise ValueError(f"additional instructions must be at most {_TEXT_CHARS} characters")
        content = cleaned.encode("utf-8")
        destination = self.bundle(run_id) / "instructions"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{stage}.txt"
        if target.exists():
            if not replace:
                raise ValueError(f"{stage} additional instructions already exist")
            target.unlink()
        self._check_total(run_id, len(content))
        temporary = target.with_suffix(".txt.part")
        try:
            temporary.write_bytes(content)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def copy_run_to_preset(self, run_id: str, preset_id: str) -> Path:
        """Copy a run bundle into independently owned preset storage."""

        source = self.bundle(run_id)
        _validate_owner_id(preset_id)
        destination = self.presets_root / preset_id / "inputs"
        if destination.exists():
            raise ValueError("preset input bundle already exists")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
        return destination

    def copy_preset_to_run(self, preset_id: str, run_id: str) -> Path:
        """Clone preset inputs into a fresh run-owned bundle."""

        _validate_owner_id(preset_id)
        source = self.presets_root / preset_id / "inputs"
        destination = self.bundle(run_id)
        if destination.exists():
            raise ValueError("run input bundle already exists")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
        return destination

    def discard(self, run_id: str) -> None:
        """Remove an uncommitted run's managed inputs after validation failure."""

        run_root = self.bundle(run_id).parent
        shutil.rmtree(self.bundle(run_id), ignore_errors=True)
        try:
            run_root.rmdir()
        except OSError:
            pass

    def discard_run(self, run_id: str) -> None:
        """Remove all app-managed files for an already deleted terminal run."""

        _validate_owner_id(run_id)
        shutil.rmtree(self.runs_root / run_id, ignore_errors=True)

    def discard_preset(self, preset_id: str) -> None:
        """Remove an uncommitted preset-owned input bundle."""

        _validate_owner_id(preset_id)
        shutil.rmtree(self.presets_root / preset_id, ignore_errors=True)

    def reconcile(
        self,
        known_run_ids: set[str],
        *,
        grace_seconds: float = 3_600,
    ) -> set[str]:
        """Remove stale partial files and old run directories with no DB owner."""

        removed: set[str] = set()
        if not self.runs_root.exists():
            return removed
        cutoff = time.time() - grace_seconds
        for run_root in self.runs_root.iterdir():
            if not run_root.is_dir():
                continue
            for partial in run_root.rglob("*.part"):
                if partial.is_file() and not partial.is_symlink():
                    partial.unlink(missing_ok=True)
            if run_root.name in known_run_ids:
                continue
            try:
                modified = run_root.stat().st_mtime
            except OSError:
                continue
            if modified <= cutoff:
                shutil.rmtree(run_root, ignore_errors=True)
                removed.add(run_root.name)
        return removed

    async def _materialize_files(
        self,
        run_id: str,
        role: str,
        uploads: list[UploadFile],
        *,
        allowed: set[str],
        multiple: bool,
        allow_relative: bool,
        replace: bool = False,
    ) -> Path:
        if not uploads:
            raise ValueError(f"select a {role.replace('_', ' ')} file")
        if len(uploads) > _MAX_FILES:
            raise ValueError(f"no more than {_MAX_FILES} files may be uploaded")
        if not multiple and len(uploads) != 1:
            raise ValueError(f"select exactly one {role.replace('_', ' ')} file")
        names = [
            _validated_name(upload.filename, allow_relative=allow_relative)
            for upload in uploads
        ]
        if len(names) != len(set(names)):
            raise ValueError("uploaded filenames must be unique after flattening")
        suffixes = [Path(name).suffix.lower() for name in names]
        if any(suffix not in allowed for suffix in suffixes):
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"{role.replace('_', ' ')} files must use: {allowed_text}")

        destination = self._new_role_dir(run_id, role, replace=replace)
        try:
            for upload, name, suffix in zip(uploads, names, suffixes, strict=True):
                target = destination / name
                temporary = target.with_suffix(target.suffix + ".part")
                with temporary.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        self._check_total(run_id, len(chunk))
                        stream.write(chunk)
                _validate_content(temporary, suffix, role=role)
                temporary.replace(target)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            self._remove_empty_bundle(run_id)
            raise
        if role == "mdf_manual":
            _write_pdf_metadata(destination / names[0], source="upload")
        if multiple:
            return destination
        return destination / names[0]

    def _new_role_dir(self, run_id: str, role: str, *, replace: bool = False) -> Path:
        destination = self.bundle(run_id) / role
        if destination.exists():
            if not replace:
                raise ValueError(f"{role.replace('_', ' ')} input already exists")
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        return destination

    def _check_total(self, run_id: str, incoming: int) -> None:
        current = sum(
            path.stat().st_size
            for path in self.bundle(run_id).rglob("*")
            if path.is_file()
        )
        if current + incoming > self.max_total_bytes:
            raise ValueError("uploaded input is too large")

    def _remove_empty_bundle(self, run_id: str) -> None:
        bundle = self.bundle(run_id)
        try:
            bundle.rmdir()
            bundle.parent.rmdir()
        except OSError:
            pass


def _upload_suffixes(
    uploads: list[UploadFile], *, allow_relative: bool
) -> list[str]:
    return [
        Path(_validated_name(upload.filename, allow_relative=allow_relative)).suffix.lower()
        for upload in uploads
    ]


def _validated_name(filename: str | None, *, allow_relative: bool) -> str:
    raw = filename or ""
    if not raw or "\\" in raw or "\0" in raw:
        raise ValueError("uploaded filenames must not contain unsafe path components")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("uploaded filenames must not contain unsafe path components")
    if not allow_relative and len(path.parts) != 1:
        raise ValueError("uploaded filenames must not contain path components")
    name = path.name
    if name in {"", ".", ".."}:
        raise ValueError("uploaded filename is invalid")
    return name


def _validate_owner_id(value: str) -> None:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in value):
        raise ValueError("managed input owner id is invalid")


def _validate_content(path: Path, suffix: str, *, role: str) -> None:
    if suffix == ".pdf":
        if not path.read_bytes()[:5] == b"%PDF-":
            raise ValueError("uploaded PDF signature is invalid")
        try:
            import fitz

            with fitz.open(path) as document:
                if document.page_count < 1:
                    raise ValueError("uploaded PDF contains no pages")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("uploaded PDF is unreadable") from exc
        return
    if suffix in _PAGE_SUFFIXES | {".gif"}:
        try:
            from PIL import Image

            with Image.open(path) as image:
                expected = {
                    ".png": "PNG",
                    ".jpg": "JPEG",
                    ".jpeg": "JPEG",
                    ".webp": "WEBP",
                    ".gif": "GIF",
                }[suffix]
                if image.format != expected:
                    raise ValueError("uploaded image type does not match its filename")
                image.verify()
        except Exception as exc:
            raise ValueError("uploaded image is unreadable") from exc
        return
    if suffix in {".txt", ".md"}:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("uploaded text must be valid UTF-8") from exc
        return
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("uploaded DOCX has no document body")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("uploaded DOCX is unreadable") from exc
        return
    if suffix == ".json" and role == "mdf_guide":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MDF parsing guide root must be an object")
            validate_marker_cheatsheet(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MDF parsing guide JSON is unreadable") from exc
        return
    raise ValueError(f"unsupported uploaded content: {suffix}")


def _write_pdf_metadata(path: Path, *, source: str) -> None:
    import fitz

    with fitz.open(path) as document:
        pages = document.page_count
    payload = {
        "filename": path.name,
        "pages": pages,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": source,
    }
    temporary = path.parent / "metadata.json.part"
    target = path.parent / "metadata.json"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def rebase_managed_config(
    config: InferenceConfig,
    *,
    source: Path,
    destination: Path,
) -> InferenceConfig:
    """Rewrite only paths owned by one managed bundle into another bundle."""

    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()

    def rebase(value: Any) -> Any:
        if isinstance(value, Path):
            resolved = value.expanduser().resolve()
            if resolved.is_relative_to(source):
                return destination / resolved.relative_to(source)
            return value
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, tuple):
            return tuple(rebase(item) for item in value)
        return value

    return InferenceConfig.model_validate(rebase(config.model_dump(mode="python")))
