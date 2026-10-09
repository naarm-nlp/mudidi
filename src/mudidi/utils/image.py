"""
Image loading and encoding helpers shared across OCR and extraction modules.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from mudidi.config.prompt_cache import MediaReferenceMode

logger = logging.getLogger(__name__)

# Anthropic (via OpenRouter) rejects inline images above 5 MB.
_MAX_LLM_IMAGE_BYTES = 4_500_000

# Oversized page images are compressed once and reused by every later call
# (Stage 1, Stage 2, verifier, rewriter, resumed runs).
_LLM_IMAGE_CACHE_ENV = "MUDIDI_LLM_IMAGE_CACHE_DIR"
_LLM_IMAGE_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
_llm_image_cache_pruned: set[Path] = set()
_llm_image_cache_lock = threading.Lock()


def image_data_url(image_path: str, mime_type: str = "image/png") -> str:
    """
    Build a data URL from an image file.

    Large raster images are re-encoded as JPEG so provider limits (e.g. Anthropic
    5 MB) are respected.

    Args:
        image_path: Path to the image file.
        mime_type: MIME type for the data URL (default: 'image/png').

    Returns:
        Data URL string of the form 'data:<mime>;base64,<data>'.
    """
    raw, out_mime = _read_bytes_for_llm(Path(image_path), mime_type=mime_type)
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{out_mime};base64,{encoded}"


def is_remote_file_reference(value: str) -> bool:
    """Return True when ``value`` is a provider-readable file URI or URL."""
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "gs", "s3"}


def model_supports_pdf_input(model: str) -> bool:
    """Best-effort check for PDF file-part support through litellm."""
    model_lower = model.lower()
    if "gemini" in model_lower or model_lower.startswith(("google/", "vertex_ai/")):
        return True
    try:
        from litellm.utils import supports_pdf_input

        try:
            return bool(supports_pdf_input(model, None))
        except TypeError:
            return bool(supports_pdf_input(model))
    except Exception as exc:
        logger.warning(
            "Could not determine PDF input support for model %r: %s",
            model,
            exc,
        )
        return False


def file_content_part(
    path_or_uri: str,
    *,
    mime_type: str,
    media_reference: MediaReferenceMode = "auto",
) -> dict:
    """
    Build a litellm file content block for a PDF or remote file reference.

    Remote references use ``file_id`` so the provider can fetch the file without
    re-uploading bytes. Local files fall back to a base64 ``file_data`` payload,
    which preserves existing behavior while using litellm's document block shape.
    """
    if media_reference != "inline" and is_remote_file_reference(path_or_uri):
        return {
            "type": "file",
            "file": {"file_id": path_or_uri, "format": mime_type},
        }
    return {
        "type": "file",
        "file": {
            "file_data": image_data_url(path_or_uri, mime_type),
            "format": mime_type,
        },
    }


def _read_bytes_for_llm(
    path: Path,
    *,
    mime_type: str = "image/png",
) -> tuple[bytes, str]:
    """Return payload bytes and MIME type, compressing rasters when needed."""
    stat = path.stat()
    if mime_type == "application/pdf" or stat.st_size <= _MAX_LLM_IMAGE_BYTES:
        return path.read_bytes(), mime_type

    cache_path = _llm_image_cache_path(path, stat)
    cached = _read_cached_llm_image(cache_path)
    if cached is not None:
        return cached, "image/jpeg"

    raw = path.read_bytes()

    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    original_size = len(raw)
    for scale in (1.0, 0.85, 0.7, 0.55, 0.45):
        candidate = img
        if scale < 1.0:
            width, height = img.size
            candidate = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            candidate.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _MAX_LLM_IMAGE_BYTES:
                logger.info(
                    "Compressed %s for LLM API: %d -> %d bytes (JPEG q=%d scale=%.2f)",
                    path.name,
                    original_size,
                    len(data),
                    quality,
                    scale,
                )
                _write_cached_llm_image(cache_path, data)
                return data, "image/jpeg"

    raise ValueError(
        f"Could not compress {path} below {_MAX_LLM_IMAGE_BYTES} bytes for LLM API"
    )


def _llm_image_cache_dir() -> Path:
    """Return the per-user directory holding compressed copies of large images."""
    override = os.environ.get(_LLM_IMAGE_CACHE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "mudidi" / "llm-images"


def _llm_image_cache_path(path: Path, stat: os.stat_result) -> Path:
    """Key the compressed copy on the source file's identity and the size limit."""
    identity = (
        f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{_MAX_LLM_IMAGE_BYTES}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return _llm_image_cache_dir() / f"{digest}.jpg"


def _read_cached_llm_image(cache_path: Path) -> bytes | None:
    try:
        data = cache_path.read_bytes()
        os.utime(cache_path)
    except OSError:
        return None
    return data or None


def _write_cached_llm_image(cache_path: Path, data: bytes) -> None:
    """Store a compressed copy; a cache that cannot be written is not an error."""
    cache_dir = cache_path.parent
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _prune_llm_image_cache(cache_dir)
        temporary = cache_path.with_name(
            f"{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_bytes(data)
        os.replace(temporary, cache_path)
    except OSError as exc:
        logger.debug("Could not cache compressed image %s: %s", cache_path, exc)


def _prune_llm_image_cache(cache_dir: Path) -> None:
    """Delete compressed copies unused for a week, once per process."""
    with _llm_image_cache_lock:
        if cache_dir in _llm_image_cache_pruned:
            return
        _llm_image_cache_pruned.add(cache_dir)
    cutoff = time.time() - _LLM_IMAGE_CACHE_MAX_AGE_SECONDS
    for entry in cache_dir.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def mime_type_for_path(image_path: str) -> str:
    """
    Infer the MIME type from the file extension.

    Args:
        image_path: Path to the image file.

    Returns:
        MIME type string.
    """
    ext = Path(image_path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }
    return mime_map.get(ext, "image/png")
