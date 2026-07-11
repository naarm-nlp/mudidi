from __future__ import annotations

import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from mudidi.config.yaml_config import MudidiConfig

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/generate_docs_reference.py"
_SPEC = spec_from_file_location("generate_docs_reference", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
render_config_reference = _MODULE.render_config_reference


def _public_schema_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            keys.update(str(key) for key in properties if key != "source_config")
        for child in value.values():
            keys.update(_public_schema_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_public_schema_keys(child))
    return keys


def test_config_reference_is_exhaustive_yaml_shaped_documentation() -> None:
    rendered = render_config_reference()

    assert rendered.startswith("# YAML configuration reference")
    assert "```json" not in rendered
    assert '"$defs"' not in rendered
    assert "source_config:" not in rendered
    for kind in (
        "inference",
        "benchmark_run",
        "benchmark_sweep",
        "stage1_evaluation",
        "stage2_evaluation",
    ):
        assert f"## `{kind}`" in rendered
        assert f'kind: "{kind}"' in rendered

    schema = TypeAdapter(MudidiConfig).json_schema()
    for key in _public_schema_keys(schema):
        assert re.search(rf"(?m)^\s*{re.escape(key)}:", rendered), key


def test_every_generated_configuration_block_is_valid_yaml() -> None:
    rendered = render_config_reference()
    blocks = re.findall(r"```yaml\n(.*?)\n```", rendered, flags=re.DOTALL)

    assert len(blocks) == 5
    for block in blocks:
        assert isinstance(yaml.safe_load(block), dict)
