"""Load LLM prompt templates from a manifest or legacy inline JSON file.

The bundled manifest maps each prompt id to a readable text-template file,
an optional description, and placeholder metadata. User-supplied legacy JSON
files with inline ``prompt`` values remain supported. The store reloads when
the manifest or any referenced template file changes.
"""

from __future__ import annotations

import json
import logging
import tempfile
from importlib import resources
from pathlib import Path, PurePosixPath
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


class PromptSourceDefinition(BaseModel):
    """Manifest entry pointing to external text or containing legacy inline text."""

    description: str = ""
    file: str | None = None
    prompt: str | None = None
    variables: list[PromptVariable] = Field(default_factory=list)


def package_root() -> Path:
    """Installed package root (``mudidi/``)."""
    return Path(__file__).resolve().parents[1]


def _materialize_zip_resource_prompts() -> Path:
    """Copy wheel-bundled prompt manifest and templates to a stable cache path."""
    global _bundled_prompts_cache
    if _bundled_prompts_cache is not None and _bundled_prompts_cache.is_file():
        return _bundled_prompts_cache

    source_dir = resources.files("mudidi").joinpath("assets/prompts")
    manifest_ref = source_dir.joinpath("manifest.json")
    manifest_text = manifest_ref.read_text(encoding="utf-8")
    sources = _parse_prompt_sources(manifest_text)
    cache_dir = Path(tempfile.gettempdir()) / "mudidi" / "prompts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for source in sources.values():
        if source.file is None:
            continue
        relative = _validated_relative_template_path(source.file)
        target = cache_dir.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        template_ref = source_dir.joinpath(*relative.parts)
        target.write_text(template_ref.read_text(encoding="utf-8"), encoding="utf-8")
    cache_path = cache_dir / "manifest.json"
    cache_path.write_text(manifest_text, encoding="utf-8")
    _bundled_prompts_cache = cache_path
    return cache_path


def default_prompts_path() -> Path:
    """Default path to the bundled prompt manifest."""
    bundled = package_root() / "assets" / "prompts" / "manifest.json"
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


def _parse_prompt_sources(text: str) -> Dict[str, PromptSourceDefinition]:
    """Parse and validate prompt-source metadata."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Prompts file must be a JSON object keyed by prompt id.")
    sources = {
        str(key): PromptSourceDefinition.model_validate(value)
        for key, value in raw.items()
    }
    for prompt_id, source in sources.items():
        if (source.file is None) == (source.prompt is None):
            raise ValueError(
                f"Prompt {prompt_id!r} must define exactly one of 'file' or 'prompt'."
            )
    return sources


def _validated_relative_template_path(file_name: str) -> PurePosixPath:
    """Return a safe manifest-relative POSIX path."""
    relative = PurePosixPath(file_name)
    if not file_name or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(
            f"Prompt template path must remain inside its manifest directory: {file_name!r}"
        )
    return relative


def _resolve_template_path(base_dir: Path, file_name: str) -> Path:
    """Resolve a template path without permitting symlink or parent traversal."""
    relative = _validated_relative_template_path(file_name)
    root = base_dir.resolve()
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(
            f"Prompt template path must remain inside its manifest directory: {file_name!r}"
        )
    return path


def _template_text(path: Path) -> str:
    """Read a template, ignoring its conventional final file newline."""
    return path.read_text(encoding="utf-8").removesuffix("\n")


def _materialize_prompt_definitions(
    sources: Dict[str, PromptSourceDefinition],
    base_dir: Path,
) -> Dict[str, PromptDefinition]:
    """Load external templates and return runtime prompt definitions."""
    prompts: Dict[str, PromptDefinition] = {}
    for prompt_id, source in sources.items():
        prompt = source.prompt
        if source.file is not None:
            prompt = _template_text(_resolve_template_path(base_dir, source.file))
        assert prompt is not None
        prompts[prompt_id] = PromptDefinition(
            description=source.description,
            prompt=prompt,
            variables=source.variables,
        )
    return prompts


def parse_prompt_file(
    text: str, *, base_dir: Path | None = None
) -> Dict[str, PromptDefinition]:
    """
    Parse a prompt manifest or legacy inline prompts JSON document.

    Time: O(n) in file length.
    """
    sources = _parse_prompt_sources(text)
    if base_dir is None and any(source.file is not None for source in sources.values()):
        raise ValueError("base_dir is required when prompt entries reference files.")
    return _materialize_prompt_definitions(sources, base_dir or Path.cwd())


class PromptStore:
    """Cached prompt reader with manifest and template mtime invalidation."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _prompts_file_path()
        self._signature: Optional[tuple[tuple[str, int, int], ...]] = None
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
        text = self._path.read_text(encoding="utf-8")
        sources = _parse_prompt_sources(text)
        signature_parts = [(str(self._path), stat.st_mtime_ns, stat.st_size)]
        for source in sources.values():
            if source.file is None:
                continue
            template_path = _resolve_template_path(self._path.parent, source.file)
            template_stat = template_path.stat()
            signature_parts.append(
                (str(template_path), template_stat.st_mtime_ns, template_stat.st_size)
            )
        signature = tuple(sorted(signature_parts))
        if signature == self._signature and self._prompts:
            return
        self._prompts = _materialize_prompt_definitions(sources, self._path.parent)
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
