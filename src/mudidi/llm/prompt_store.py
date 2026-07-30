"""
Load LLM prompt templates from ``mudidi/assets/PROMPT.json``.

Each entry maps a prompt id to ``prompt`` text, an optional human-readable
``description``, and a ``variables`` list describing placeholders (Python
``str.format`` keys and optional XML wrapper tags). Edit the JSON file to
customize prompts at inference time; the store reloads when the file
modification time changes.
"""

from __future__ import annotations

import json
import logging
import tempfile
from importlib import resources
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_configured_path: Optional[Path] = None
_bundled_prompts_cache: Optional[Path] = None


class PromptVariable(BaseModel):
    """Describes one injectable placeholder in a prompt template."""

    name: str
    tag: str | None = None
    description: str


class PromptDefinition(BaseModel):
    """One named prompt template and its placeholder metadata."""

    description: str = ""
    prompt: str
    variables: list[PromptVariable] = Field(default_factory=list)


def package_root() -> Path:
    """Installed package root (``mudidi/``)."""
    return Path(__file__).resolve().parents[1]


def _materialize_zip_resource_prompts() -> Path:
    """Copy wheel-bundled PROMPT.json to a stable cache path on disk."""
    global _bundled_prompts_cache
    if _bundled_prompts_cache is not None and _bundled_prompts_cache.is_file():
        return _bundled_prompts_cache

    ref = resources.files("mudidi").joinpath("assets/PROMPT.json")
    text = ref.read_text(encoding="utf-8")
    cache_dir = Path(tempfile.gettempdir()) / "mudidi"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "PROMPT.json"
    cache_path.write_text(text, encoding="utf-8")
    _bundled_prompts_cache = cache_path
    return cache_path


def default_prompts_path() -> Path:
    """Default path to bundled ``PROMPT.json``."""
    bundled = package_root() / "assets" / "PROMPT.json"
    if bundled.is_file():
        return bundled
    try:
        return _materialize_zip_resource_prompts()
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        return bundled


def configure_prompts(path: Path | str) -> None:
    """Set the prompts file used by :func:`get_prompt_store`."""
    global _configured_path
    prompts_path = Path(path).expanduser().resolve()
    _configured_path = prompts_path
    get_prompt_store().set_path(prompts_path)
    logger.info("Prompts file: %s", prompts_path)


def _prompts_file_path() -> Path:
    if _configured_path is not None:
        return _configured_path
    return default_prompts_path()


def parse_prompt_file(text: str) -> Dict[str, PromptDefinition]:
    """
    Parse a prompts JSON document.

    Time: O(n) in file length.
    """
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Prompts file must be a JSON object keyed by prompt id.")
    return {
        str(key): PromptDefinition.model_validate(value)
        for key, value in raw.items()
    }


class PromptStore:
    """Cached reader for ``PROMPT.json`` with mtime-based invalidation."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _prompts_file_path()
        self._signature: Optional[tuple[float, int]] = None
        self._prompts: Dict[str, PromptDefinition] = {}

    def set_path(self, path: Path) -> None:
        """Point at a different prompts file and force reload."""
        self._path = path
        self._signature = None
        self._prompts = {}

    @property
    def path(self) -> Path:
        return self._path

    def _reload_if_changed(self) -> None:
        if not self._path.is_file():
            raise FileNotFoundError(
                f"Prompts file not found: {self._path}. "
                "Create it or pass --prompts-file to mudidi run."
            )
        stat = self._path.stat()
        signature = (stat.st_mtime, stat.st_size)
        if signature == self._signature and self._prompts:
            return
        text = self._path.read_text(encoding="utf-8")
        self._prompts = parse_prompt_file(text)
        self._signature = signature
        logger.debug("Loaded %d prompts from %s", len(self._prompts), self._path)

    def prompt_ids(self) -> list[str]:
        """Return loaded prompt identifiers."""
        self._reload_if_changed()
        return sorted(self._prompts)

    def get_definition(self, prompt_id: str) -> PromptDefinition:
        """Return the full prompt definition (text + variable metadata)."""
        self._reload_if_changed()
        try:
            return self._prompts[prompt_id]
        except KeyError as exc:
            available = ", ".join(sorted(self._prompts))
            raise KeyError(
                f"Prompt {prompt_id!r} not found in {self._path}. "
                f"Available: {available}"
            ) from exc

    def get(self, prompt_id: str) -> str:
        """Return raw prompt text (no placeholder substitution)."""
        return self.get_definition(prompt_id).prompt

    def variables(self, prompt_id: str) -> list[PromptVariable]:
        """Return variable metadata for a prompt."""
        return self.get_definition(prompt_id).variables

    def format(self, prompt_id: str, **kwargs: object) -> str:
        """Return prompt text with ``str.format`` placeholders filled."""
        template = self.get(prompt_id)
        if not kwargs:
            return template
        return template.format(**kwargs)


_store: Optional[PromptStore] = None


def get_prompt_store() -> PromptStore:
    """Return the process-wide prompt store."""
    global _store
    if _store is None:
        _store = PromptStore(_prompts_file_path())
    return _store
