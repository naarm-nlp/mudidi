"""Integration tests for the shared dashboard theme shell."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app


def test_every_page_renders_the_shared_shell(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    for path in ("/", "/history", "/presets", "/active"):
        response = client.get(path)

        assert response.status_code == 200
        assert 'class="sidebar"' in response.text
        assert 'aria-label="Primary navigation"' in response.text
        assert 'href="/presets"' in response.text
        assert "data-theme-toggle" in response.text
        assert 'class="standalone"' not in response.text


def test_nav_marks_only_the_current_section_active(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/history")

    assert response.status_code == 200
    assert 'class="active" href="/history"' in response.text
    assert 'class="active" href="/"' not in response.text


def test_theme_script_is_served_and_follows_the_os_preference(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/theme.js")

    assert response.status_code == 200
    assert "mudidi:theme" in response.text
    assert "prefers-color-scheme" in response.text
    assert "dataset.theme" in response.text


def test_stylesheet_exposes_the_brutalist_theme(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--border-width: 3px" in response.text
    assert '[data-theme="dark"]' in response.text
    assert "8px 8px 0 0" in response.text
    assert "border-radius" not in response.text
