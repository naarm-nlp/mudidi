"""Generate deterministic CLI and configuration reference pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from mudidi.cli.main import build_parser
from mudidi.config.yaml_config import MudidiConfig


ROOT = Path(__file__).resolve().parents[1]


def _command_help(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    sections = [(parser.prog, parser.format_help())]
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for child in action.choices.values():
            sections.extend(_command_help(child))
    return sections


def render_cli_reference() -> str:
    sections = ["# CLI reference", "", "Generated from the public argparse tree.", ""]
    for command, help_text in _command_help(build_parser()):
        sections.extend(
            [f"## `{command}`", "", "```text", help_text.rstrip(), "```", ""]
        )
    return "\n".join(sections)


def render_config_reference() -> str:
    schema = TypeAdapter(MudidiConfig).json_schema()
    return "\n".join(
        [
            "# Configuration schema",
            "",
            "Generated from the versioned Pydantic configuration union.",
            "",
            "```json",
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def generated_pages() -> dict[Path, str]:
    return {
        ROOT / "docs/reference/cli.md": render_cli_reference(),
        ROOT / "docs/reference/config.md": render_config_reference(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale: list[Path] = []
    for path, content in generated_pages().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if stale:
        parser.error("generated documentation is stale: " + ", ".join(map(str, stale)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
