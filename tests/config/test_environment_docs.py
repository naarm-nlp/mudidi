from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_openrouter_api_key_name_matches_client_environment_variable() -> None:
    files = [
        ROOT / "README.md",
        ROOT / ".env.example",
        ROOT / "docs/getting-started/installation.md",
        ROOT / "docs/CODEMAPS/dependencies.md",
    ]

    for path in files:
        contents = path.read_text(encoding="utf-8")
        assert "OPENROUTER_API_KEY" not in contents, path
        assert "OPEN_ROUTER_API_KEY" in contents, path
