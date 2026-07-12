"""Tests for safe run artifact, page, and usage inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mudidi.config.yaml_config import InferenceConfig
from mudidi.web.app import create_app
from mudidi.web.artifacts import ArtifactAccessError, ArtifactService
from mudidi.web.models import Provider


def _prepared_app(tmp_path: Path) -> tuple[object, TestClient, str, Path]:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    pages = tmp_path / "pages"
    pages.mkdir()
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": output},
            "pipeline": {"stage": "1"},
        }
    )
    run_id = "artifact-run"
    app.state.job_controller.prepare_inference(
        run_id,
        config=config,
        provider=Provider.ANTHROPIC,
    )
    stage1 = output / "stage-1/page_1"
    stage1.mkdir(parents=True)
    (stage1 / "page_1_stage1_flat.txt").write_text("hello transcription", encoding="utf-8")
    stage2 = output / "stage-2/page_1"
    stage2.mkdir(parents=True)
    (stage2 / "page_1.mdf.txt").write_text("\\lx hello\n\\ge gloss", encoding="utf-8")
    (stage2 / "page_1_usage.json").write_text(
        json.dumps({"total_tokens": 120, "total_cost_usd": 0.012}),
        encoding="utf-8",
    )
    return app, TestClient(app), run_id, output


def test_artifact_listing_is_relative_and_grouped_by_page(tmp_path: Path) -> None:
    app, _client, run_id, _output = _prepared_app(tmp_path)
    service = ArtifactService(controller=app.state.job_controller)

    artifacts = service.list_artifacts(run_id)
    pages = service.list_pages(run_id)

    assert {artifact.relative_path.as_posix() for artifact in artifacts} >= {
        "stage-1/page_1/page_1_stage1_flat.txt",
        "stage-2/page_1/page_1.mdf.txt",
    }
    assert pages[0].page_id == "page_1"
    assert pages[0].stage1 is not None
    assert pages[0].stage2 is not None


@pytest.mark.parametrize("unsafe", ["../secret.txt", "/etc/passwd", "stage-1/../../x"])
def test_artifact_resolution_rejects_traversal(
    tmp_path: Path,
    unsafe: str,
) -> None:
    app, _client, run_id, _output = _prepared_app(tmp_path)
    service = ArtifactService(controller=app.state.job_controller)

    with pytest.raises(ArtifactAccessError):
        service.resolve(run_id, unsafe)


def test_artifact_resolution_rejects_symlink_escape(tmp_path: Path) -> None:
    app, _client, run_id, output = _prepared_app(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = output / "stage-1/page_1/escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not supported on this platform")

    with pytest.raises(ArtifactAccessError):
        ArtifactService(controller=app.state.job_controller).resolve(
            run_id,
            "stage-1/page_1/escape.txt",
        )


def test_usage_summary_aggregates_page_usage(tmp_path: Path) -> None:
    app, _client, run_id, _output = _prepared_app(tmp_path)

    usage = ArtifactService(controller=app.state.job_controller).usage_summary(run_id)

    assert usage.total_tokens == 120
    assert usage.total_cost_usd == 0.012
    assert usage.files_scanned == 1


def test_output_pages_usage_and_download_routes(tmp_path: Path) -> None:
    _app, client, run_id, _output = _prepared_app(tmp_path)

    outputs = client.get(f"/runs/{run_id}/outputs")
    pages = client.get(f"/runs/{run_id}/pages")
    usage = client.get(f"/runs/{run_id}/usage")
    download = client.get(
        f"/runs/{run_id}/artifacts/stage-2/page_1/page_1.mdf.txt"
    )

    assert outputs.status_code == 200
    assert "page_1.mdf.txt" in outputs.text
    assert pages.status_code == 200
    assert "hello transcription" in pages.text
    assert "\\lx hello" in pages.text
    assert usage.status_code == 200
    assert "120" in usage.text
    assert "$0.012" in usage.text
    assert download.status_code == 200
    assert download.text.startswith("\\lx hello")


def test_download_route_hides_traversal_as_not_found(tmp_path: Path) -> None:
    _app, client, run_id, _output = _prepared_app(tmp_path)

    response = client.get(f"/runs/{run_id}/artifacts/%2E%2E/secret.txt")

    assert response.status_code == 404
