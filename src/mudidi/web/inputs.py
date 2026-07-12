"""Safe materialization of browser uploads into run-owned local storage."""

from __future__ import annotations

import shutil
from pathlib import Path

from starlette.datastructures import UploadFile

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
_SUPPORTED_SUFFIXES = _IMAGE_SUFFIXES | {".pdf"}
_MAX_FILES = 5_000


class InputMaterializer:
    """Copy validated PDF or page-image uploads beneath the app data root."""

    def __init__(self, *, data_dir: Path) -> None:
        self.root = data_dir.expanduser().resolve() / "uploads"

    async def materialize(self, run_id: str, uploads: list[UploadFile]) -> Path:
        """Return a managed PDF or image directory for one run."""

        if not uploads:
            raise ValueError("select uploaded files or enter a local input path")
        if len(uploads) > _MAX_FILES:
            raise ValueError(f"no more than {_MAX_FILES} page files may be uploaded")
        names = [_validated_name(upload.filename) for upload in uploads]
        if len(names) != len(set(names)):
            raise ValueError("uploaded filenames must be unique")
        suffixes = [Path(name).suffix.lower() for name in names]
        if any(suffix not in _SUPPORTED_SUFFIXES for suffix in suffixes):
            raise ValueError("uploads must be PDF, PNG, JPEG, or TIFF files")
        if ".pdf" in suffixes and (len(uploads) != 1 or suffixes[0] != ".pdf"):
            raise ValueError("a PDF must be uploaded by itself")

        run_root = self.root / run_id
        if run_root.exists():
            raise ValueError("managed upload directory already exists")
        destination = run_root if suffixes[0] == ".pdf" else run_root / "pages"
        destination.mkdir(parents=True)
        try:
            for upload, name in zip(uploads, names, strict=True):
                target = destination / name
                temporary = target.with_suffix(target.suffix + ".part")
                with temporary.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        stream.write(chunk)
                    stream.flush()
                temporary.replace(target)
        except Exception:
            shutil.rmtree(run_root, ignore_errors=True)
            raise
        return destination / names[0] if suffixes[0] == ".pdf" else destination

    def discard(self, run_id: str) -> None:
        """Remove an uncommitted run's managed uploads after validation failure."""

        shutil.rmtree(self.root / run_id, ignore_errors=True)


def _validated_name(filename: str | None) -> str:
    name = filename or ""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("uploaded filenames must not contain path components")
    return name
