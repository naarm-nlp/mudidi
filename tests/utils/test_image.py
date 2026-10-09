"""Tests for LLM image payload preparation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from mudidi.utils import image as image_module


def _write_noise_png(path: Path) -> None:
    Image.frombytes("RGB", (160, 160), os.urandom(160 * 160 * 3)).save(path)


@pytest.fixture
def small_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("MUDIDI_LLM_IMAGE_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(image_module, "_MAX_LLM_IMAGE_BYTES", 20_000)
    monkeypatch.setattr(image_module, "_llm_image_cache_pruned", set())
    return cache_dir


def test_small_image_is_sent_unchanged_and_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("MUDIDI_LLM_IMAGE_CACHE_DIR", str(cache_dir))
    page = tmp_path / "page.png"
    _write_noise_png(page)

    raw, mime = image_module._read_bytes_for_llm(page, mime_type="image/png")

    assert raw == page.read_bytes()
    assert mime == "image/png"
    assert not cache_dir.exists()


def test_oversized_image_is_compressed_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, small_limit: Path
) -> None:
    page = tmp_path / "page.png"
    _write_noise_png(page)
    first, first_mime = image_module._read_bytes_for_llm(page, mime_type="image/png")

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("image was compressed a second time")

    monkeypatch.setattr(Image, "open", fail_open)
    second, second_mime = image_module._read_bytes_for_llm(
        page, mime_type="image/png"
    )

    assert first_mime == second_mime == "image/jpeg"
    assert len(first) <= 20_000
    assert second == first
    assert len(list(small_limit.glob("*.jpg"))) == 1


def test_changed_source_image_is_compressed_again(
    tmp_path: Path, small_limit: Path
) -> None:
    page = tmp_path / "page.png"
    _write_noise_png(page)
    first, _ = image_module._read_bytes_for_llm(page, mime_type="image/png")

    _write_noise_png(page)
    stat = page.stat()
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second, _ = image_module._read_bytes_for_llm(page, mime_type="image/png")

    assert second != first
    assert len(list(small_limit.glob("*.jpg"))) == 2


def test_unwritable_cache_still_returns_the_compressed_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, small_limit: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("MUDIDI_LLM_IMAGE_CACHE_DIR", str(blocker / "cache"))
    page = tmp_path / "page.png"
    _write_noise_png(page)

    raw, mime = image_module._read_bytes_for_llm(page, mime_type="image/png")

    assert mime == "image/jpeg"
    assert len(raw) <= 20_000


def test_stale_cache_entries_are_pruned(tmp_path: Path, small_limit: Path) -> None:
    small_limit.mkdir(parents=True)
    stale = small_limit / "stale.jpg"
    stale.write_bytes(b"old")
    old = stale.stat().st_mtime - 8 * 24 * 3600
    os.utime(stale, (old, old))
    page = tmp_path / "page.png"
    _write_noise_png(page)

    image_module._read_bytes_for_llm(page, mime_type="image/png")

    assert not stale.exists()
