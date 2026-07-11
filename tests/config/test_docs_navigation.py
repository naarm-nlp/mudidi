from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_docs_use_builtin_readthedocs_navigation() -> None:
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))

    assert config["theme"] == {
        "name": "readthedocs",
        "locale": "en",
        "navigation_depth": 4,
        "collapse_navigation": False,
        "sticky_navigation": True,
    }
    assert "extra_css" not in config
    assert "extra_javascript" not in config
