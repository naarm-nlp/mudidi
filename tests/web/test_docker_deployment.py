"""Deployment contract for the local-only Docker dashboard."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_publishes_only_to_host_loopback_and_persists_data() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["mudidi"]

    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert "./mudidi-data:/data" in service["volumes"]
    assert service["init"] is True
    assert "no-new-privileges:true" in service["security_opt"]


def test_image_runs_container_mode_with_persistent_application_data() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen --no-dev --extra web" in dockerfile
    assert "pdftk-java" in dockerfile
    assert 'HEALTHCHECK' in dockerfile
    assert 'CMD ["mudidi", "web", "--container"' in dockerfile
    assert '"--data-dir", "/data/app"' in dockerfile
    assert '"--no-browser"]' in dockerfile


def test_docker_context_excludes_secrets_and_local_generated_data() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "mudidi-data/" in ignored
    assert "outputs/" in ignored
    assert "dataset/" in ignored
