"""Static contracts for the repository's GitHub Actions configuration."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA_ACTION = re.compile(
    r"(?:-\s+)?uses:\s+[^\s]+@[0-9a-f]{40}(?:\s+#\s+v\S+)?$"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_actions_are_pinned(workflow: str) -> None:
    action_lines = [line.strip() for line in workflow.splitlines() if "uses:" in line]
    assert action_lines
    assert all(FULL_SHA_ACTION.fullmatch(line) for line in action_lines)


def test_core_ci_runs_the_complete_locked_suite_and_package_smoke_test() -> None:
    workflow = _read(WORKFLOWS / "ci.yml")

    assert "name: Continuous integration" in workflow
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "timeout-minutes: 15" in workflow
    assert 'python-version: "3.11"' in workflow
    assert 'version: "0.11.28"' in workflow
    assert "uv sync --locked --extra dev --extra web" in workflow
    assert "uv run --locked pytest" in workflow
    assert "tests/web" not in workflow
    assert "--cov=mudidi.web" in workflow
    assert "--cov-fail-under=80" in workflow
    assert "uv build" in workflow
    assert "dist/*.whl" in workflow
    assert "mudidi --help" in workflow
    _assert_actions_are_pinned(workflow)


def test_dependency_audit_is_locked_and_excludes_paddle() -> None:
    workflow = _read(WORKFLOWS / "ci.yml")

    assert "dependency-audit:" in workflow
    assert "uv sync --locked --extra dev --extra web --extra docs" in workflow
    assert "uv run --locked pip-audit" in workflow
    assert "--extra paddle" not in workflow


def test_docs_are_strict_without_duplicate_python_tests() -> None:
    workflow = _read(WORKFLOWS / "docs.yml")

    assert "uv sync --locked --extra dev --extra docs" in workflow
    assert "pytest tests/config tests/cli" not in workflow
    assert "generate_docs_reference.py --check" in workflow
    assert "mkdocs build --strict" in workflow
    assert "group: github-pages" in workflow
    assert "cancel-in-progress: false" in workflow
    _assert_actions_are_pinned(workflow)


def test_docker_workflow_builds_and_health_checks_relevant_changes() -> None:
    workflow = _read(WORKFLOWS / "docker.yml")

    for relevant_path in (
        "Dockerfile",
        ".dockerignore",
        "compose.yaml",
        "pyproject.toml",
        "uv.lock",
        "src/**",
    ):
        assert relevant_path in workflow

    assert "timeout-minutes: 20" in workflow
    assert "docker/setup-buildx-action" in workflow
    assert "docker/build-push-action" in workflow
    assert "cache-from: type=gha" in workflow
    assert "cache-to: type=gha,mode=max" in workflow
    assert "docker compose up --detach --no-build" in workflow
    assert "http://127.0.0.1:8000/healthz" in workflow
    assert "docker compose logs --no-color --tail=200" in workflow
    assert "docker compose down --volumes --remove-orphans" in workflow
    _assert_actions_are_pinned(workflow)


def test_dependabot_is_not_enabled() -> None:
    assert not (ROOT / ".github" / "dependabot.yml").exists()
