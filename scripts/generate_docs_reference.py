"""Generate deterministic CLI and configuration reference pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from mudidi.cli.main import build_parser
from mudidi.config.yaml_config import MudidiConfig


ROOT = Path(__file__).resolve().parents[1]
_MISSING = object()

_CONFIG_REFERENCE_SECTIONS = (
    (
        "inference",
        "InferenceConfig",
        "Production inference for a page directory or source PDF.",
        "mudidi run --config CONFIG",
    ),
    (
        "benchmark_run",
        "BenchmarkRunConfig",
        "One benchmark extraction over a dataset, sample tree, or page input.",
        "mudidi benchmark run --config CONFIG",
    ),
    (
        "benchmark_sweep",
        "BenchmarkSweepConfig",
        "A validated collection of benchmark runs expanded from axes or experiments.",
        "mudidi benchmark sweep --config CONFIG",
    ),
    (
        "stage1_evaluation",
        "Stage1EvaluationConfig",
        "Stage 1 evaluation for one file pair or a discovered experiment tree.",
        "mudidi benchmark evaluate stage1 --config CONFIG",
    ),
    (
        "stage2_evaluation",
        "Stage2EvaluationConfig",
        "Stage 2 MDF evaluation for one file pair or a discovered experiment tree.",
        "mudidi benchmark evaluate stage2 --config CONFIG",
    ),
)


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


def _resolve_schema(schema: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local JSON Schema reference and select a non-null union branch."""
    if "$ref" in schema:
        return _resolve_schema(definitions[schema["$ref"].rsplit("/", 1)[-1]], definitions)
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        non_null = [variant for variant in variants if variant.get("type") != "null"]
        if non_null:
            return _resolve_schema(non_null[0], definitions)
    return schema


def _schema_type(schema: dict[str, Any], definitions: dict[str, Any]) -> str:
    """Return a compact user-facing type description for one schema node."""
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        names = [_schema_type(variant, definitions) for variant in variants]
        return " | ".join(dict.fromkeys(names))
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "enum" in schema:
        return "one of " + ", ".join(json.dumps(value) for value in schema["enum"])
    schema_type = schema.get("type", "value")
    if schema.get("format") == "path":
        return "path"
    if schema_type == "array":
        return f"list[{_schema_type(schema.get('items', {}), definitions)}]"
    if schema_type == "object":
        return "mapping"
    return str(schema_type)


def _schema_comment(
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    required: bool,
    example: Any = _MISSING,
) -> str:
    """Describe type, default, and numeric constraints as a YAML comment."""
    details = [_schema_type(schema, definitions)]
    if required:
        details.append("required")
    elif "default" in schema:
        default = schema["default"]
        if (
            default is not None
            and example is not _MISSING
            and not isinstance(example, (dict, list))
            and example != default
        ):
            details.append(f"effective default: {json.dumps(example)}")
        else:
            details.append(f"default: {json.dumps(default)}")
    else:
        details.append("optional")
    if "minimum" in schema:
        details.append(f">= {schema['minimum']}")
    if "exclusiveMinimum" in schema:
        details.append(f"> {schema['exclusiveMinimum']}")
    if "maximum" in schema:
        details.append(f"<= {schema['maximum']}")
    if "pattern" in schema:
        details.append(f"pattern: {schema['pattern']}")
    return "; ".join(details)


def _scalar_example(
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    example: Any = _MISSING,
) -> Any:
    """Choose a readable YAML value for a scalar schema node."""
    if example is not _MISSING and not isinstance(example, (dict, list)):
        return example
    if "const" in schema:
        return schema["const"]
    if "default" in schema:
        return schema["default"]
    variants = schema.get("anyOf")
    if isinstance(variants, list) and any(v.get("type") == "null" for v in variants):
        return None
    resolved = _resolve_schema(schema, definitions)
    if "enum" in resolved:
        return resolved["enum"][0]
    schema_type = resolved.get("type")
    if resolved.get("format") == "path":
        return "path/to/value"
    if schema_type == "string":
        return "value"
    if schema_type == "integer":
        return max(int(resolved.get("minimum", 0)), 1)
    if schema_type == "number":
        return max(float(resolved.get("minimum", 0.0)), 0.1)
    if schema_type == "boolean":
        return False
    if schema_type == "array":
        return []
    return None


def _yaml_scalar(value: Any) -> str:
    """Serialize one scalar using JSON syntax, which is also valid YAML."""
    return json.dumps(value, ensure_ascii=False)


def _render_yaml_mapping(
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    indent: int = 0,
    example: dict[str, Any] | None = None,
) -> list[str]:
    """Render every property in a model schema as an annotated YAML mapping."""
    resolved = _resolve_schema(schema, definitions)
    required_fields = set(resolved.get("required", []))
    lines: list[str] = []
    properties = resolved.get("properties", {})
    names = list(properties)
    if "kind" in names:
        names.remove("kind")
        names.insert(1 if names and names[0] == "version" else 0, "kind")
    for name in names:
        field_schema = properties[name]
        if name == "source_config":
            continue
        lines.extend(
            _render_yaml_field(
                name,
                field_schema,
                definitions,
                indent=indent,
                required=name in required_fields,
                example=(example or {}).get(name, _MISSING),
            )
        )
    return lines


def _render_yaml_field(
    name: str,
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    indent: int,
    required: bool,
    example: Any = _MISSING,
) -> list[str]:
    """Render one scalar, nested model, sequence, or free-form mapping field."""
    prefix = " " * indent
    comment = _schema_comment(
        schema,
        definitions,
        required=required,
        example=example,
    )
    resolved = _resolve_schema(schema, definitions)

    if resolved.get("type") == "object" and "properties" in resolved:
        return [
            f"{prefix}{name}:  # {comment}",
            *_render_yaml_mapping(
                resolved,
                definitions,
                indent=indent + 2,
                example=example if isinstance(example, dict) else None,
            ),
        ]

    if resolved.get("type") == "object":
        additional = resolved.get("additionalProperties")
        if isinstance(additional, dict):
            child = _resolve_schema(additional, definitions)
            mapping_example = example if isinstance(example, dict) else {}
            example_key = next(iter(mapping_example), "example_key")
            example_child = mapping_example.get(example_key, _MISSING)
            lines = [f"{prefix}{name}:  # {comment}"]
            if child.get("type") == "array":
                lines.append(f"{prefix}  {example_key}:")
                item_example = (
                    example_child[0]
                    if isinstance(example_child, list) and example_child
                    else _MISSING
                )
                lines.extend(
                    _render_yaml_list_item(
                        child.get("items", {}),
                        definitions,
                        indent=indent + 4,
                        example=item_example,
                    )
                )
            else:
                value = _scalar_example(child, definitions, example=example_child)
                lines.append(
                    f"{prefix}  {example_key}: {_yaml_scalar(value)}"
                )
            return lines
        if isinstance(example, dict) and example:
            example_key, example_value = next(iter(example.items()))
            return [
                f"{prefix}{name}:  # {comment}",
                f"{prefix}  {example_key}: {_yaml_scalar(example_value)}",
            ]
        return [
            f"{prefix}{name}:  # {comment}",
            f'{prefix}  dotted.field.path: "value"',
        ]

    if resolved.get("type") == "array":
        items = resolved.get("items", {})
        item = _resolve_schema(items, definitions)
        if item.get("type") == "object":
            item_example = example[0] if isinstance(example, list) and example else _MISSING
            return [
                f"{prefix}{name}:  # {comment}",
                *_render_yaml_list_item(
                    items,
                    definitions,
                    indent=indent + 2,
                    example=item_example,
                ),
            ]
        value = _scalar_example(schema, definitions, example=example)
        return [f"{prefix}{name}: {_yaml_scalar(value)}  # {comment}"]

    value = _scalar_example(schema, definitions, example=example)
    return [f"{prefix}{name}: {_yaml_scalar(value)}  # {comment}"]


def _render_yaml_list_item(
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    indent: int,
    example: Any = _MISSING,
) -> list[str]:
    """Render one representative item while retaining all nested item fields."""
    prefix = " " * indent
    resolved = _resolve_schema(schema, definitions)
    if resolved.get("type") == "object" and "properties" in resolved:
        mapping = _render_yaml_mapping(
            resolved,
            definitions,
            indent=indent,
            example=example if isinstance(example, dict) else None,
        )
        if not mapping:
            return [f"{prefix}- {{}}"]
        first, *rest = mapping
        return [
            f"{prefix}- {first[len(prefix):]}",
            *(f"  {line}" for line in rest),
        ]
    if resolved.get("type") == "object":
        return [f'{prefix}- example_key: "value"']
    return [
        f"{prefix}- {_yaml_scalar(_scalar_example(schema, definitions, example=example))}"
    ]


def _reference_example(adapter: TypeAdapter[Any], kind: str) -> dict[str, Any]:
    """Build a valid minimal config whose resolved values seed each template."""
    examples: dict[str, dict[str, Any]] = {
        "inference": {
            "version": 1,
            "kind": "inference",
            "input": {"pages": "path/to/pages"},
            "output": {"directory": "path/to/output"},
        },
        "benchmark_run": {
            "version": 1,
            "kind": "benchmark_run",
            "input": {"dataset_dir": "path/to/dataset"},
            "output": {"directory": "path/to/output"},
        },
        "benchmark_sweep": {
            "version": 1,
            "kind": "benchmark_sweep",
            "name": "example-sweep",
            "base": {
                "version": 1,
                "kind": "benchmark_run",
                "input": {"dataset_dir": "path/to/dataset"},
                "output": {"directory": "path/to/output"},
            },
            "axes": {
                "model": [
                    {"id": "example-model", "set": {"models.stage1": "provider/model"}}
                ]
            },
            "experiment_name": "{model}",
        },
        "stage1_evaluation": {
            "version": 1,
            "kind": "stage1_evaluation",
            "input": {
                "predicted": "path/to/predicted",
                "gold": "path/to/gold",
            },
            "output": {"directory": "path/to/output"},
        },
        "stage2_evaluation": {
            "version": 1,
            "kind": "stage2_evaluation",
            "input": {
                "predicted": "path/to/predicted",
                "gold": "path/to/gold",
            },
            "output": {"directory": "path/to/output"},
        },
    }
    config = adapter.validate_python(examples[kind])
    return config.model_dump(
        mode="json",
        by_alias=True,
        exclude={"source_config"},
    )


def render_config_reference() -> str:
    """Render an exhaustive, YAML-shaped reference for every configuration kind."""
    adapter = TypeAdapter(MudidiConfig)
    schema = adapter.json_schema()
    definitions = schema["$defs"]
    sections = [
        "# YAML configuration reference",
        "",
        "Generated from MUDIDI's strict Pydantic configuration models. Choose the",
        "template matching your command's `kind`; unknown keys are rejected.",
        "",
        "Each template is exhaustive: it shows optional fields and mutually exclusive",
        "alternatives together so that every available key is discoverable. Remove keys",
        "you do not use, especially one side of the sweep and evaluation alternatives.",
        "Paths are resolved relative to the YAML file. API credentials belong in `.env`.",
        "",
        "## How to read the templates",
        "",
        "- Inline comments show each field's type, default, and numeric constraints.",
        "- `null` means the field is optional and currently unset.",
        "- `[]` and example mapping entries show the expected container shape.",
        "- `source_config` is internal loader state and is intentionally omitted.",
        "",
        "## Important validation rules",
        "",
        "- `inference` requires `input.pages`, cannot use `stage1_source: gold`, and uses the optional `input.dictionary_profile` instead of the benchmark-only `dictionary_languages` file.",
        "- `benchmark_run` requires one of `dataset_dir`, `samples_dir`, or `pages`.",
        "- `vlm_ocr` and `mathpix_ocr` are Stage 1-only strategies.",
        "- Evaluation uses either `predicted` + `gold` or `dataset_dir` + `pred_root`.",
        "- A benchmark sweep uses exactly one of `axes` or `experiments`.",
        "",
    ]
    for kind, definition_name, description, command in _CONFIG_REFERENCE_SECTIONS:
        sections.extend(
            [
                f"## `{kind}`",
                "",
                description,
                "",
                f"Run with `{command}`.",
                "",
                "```yaml",
                *_render_yaml_mapping(
                    definitions[definition_name],
                    definitions,
                    example=_reference_example(adapter, kind),
                ),
                "```",
                "",
            ]
        )
    return "\n".join(sections)


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
