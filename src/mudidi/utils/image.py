"""
Image loading and encoding helpers shared across OCR and extraction modules.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from urllib.parse import urlparse

from mudidi.config.prompt_cache import MediaReferenceMode

logger = logging.getLogger(__name__)

# Anthropic (via OpenRouter) rejects inline images above 5 MB.
_MAX_LLM_IMAGE_BYTES = 4_500_000


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
    raw = path.read_bytes()
    if mime_type == "application/pdf" or len(raw) <= _MAX_LLM_IMAGE_BYTES:
        return raw, mime_type

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
                return data, "image/jpeg"

    raise ValueError(
        f"Could not compress {path} below {_MAX_LLM_IMAGE_BYTES} bytes for LLM API"
    )


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
