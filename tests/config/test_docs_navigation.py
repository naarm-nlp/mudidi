from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_docs_navigation_uses_sidebar_search_and_custom_templates() -> None:
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))

    assert config["theme"]["custom_dir"] == "docs/overrides"
    assert "javascripts/navigation.js" in config["extra_javascript"]

    header = (
        ROOT / "docs/overrides/partials/header.html"
    ).read_text(encoding="utf-8")
    navigation = (
        ROOT / "docs/overrides/partials/nav.html"
    ).read_text(encoding="utf-8")

    assert 'include "partials/search.html"' in header
    assert 'include "partials/search.html"' not in navigation
    assert "md-nav__search-spacer" in navigation


def test_desktop_header_is_hidden_and_parent_items_expand() -> None:
    css = (ROOT / "docs/stylesheets/navigation.css").read_text(encoding="utf-8")
    javascript = (
        ROOT / "docs/javascripts/navigation.js"
    ).read_text(encoding="utf-8")

    assert ".md-header" in css
    assert ".md-nav__search-spacer" in css
    assert "position: static" in css
    assert ".md-nav__toggle:checked ~ .md-nav" in css
    assert "grid-template-rows" in css
    assert 'querySelectorAll(".md-nav__item--nested")' in javascript
    assert 'setAttribute("aria-expanded"' in javascript
    assert "event.preventDefault()" in javascript
