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
    (pages / "page_1.png").write_bytes(b"safe source image")
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
    (stage1 / "page_1_stage1_flat.txt").write_text(
        "hello transcription", encoding="utf-8"
    )
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

    overview = client.get(f"/runs/{run_id}")
    outputs = client.get(f"/runs/{run_id}/outputs")
    pages = client.get(f"/runs/{run_id}/pages")
    usage = client.get(f"/runs/{run_id}/usage")
    download = client.get(f"/runs/{run_id}/artifacts/stage-2/page_1/page_1.mdf.txt")

    assert overview.status_code == 200
    assert "Output Preview" in overview.text
    assert "File Artifacts" in overview.text
    assert outputs.status_code == 200
    assert "File Artifacts" in outputs.text
    assert "page_1.mdf.txt" in outputs.text
    assert pages.status_code == 200
    assert "Output Preview" in pages.text
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


def test_page_detail_combines_safe_source_and_generated_evidence(
    tmp_path: Path,
) -> None:
    _app, client, run_id, _output = _prepared_app(tmp_path)

    detail = client.get(f"/runs/{run_id}/pages/page_1")
    source = client.get(f"/runs/{run_id}/pages/page_1/source")

    assert detail.status_code == 200
    assert "Page page_1" in detail.text
    assert "hello transcription" in detail.text
    assert "\\lx hello" in detail.text
    assert f"/runs/{run_id}/pages/page_1/source" in detail.text
    assert source.status_code == 200
    assert source.content == b"safe source image"


def test_page_editor_saves_stage_outputs_to_existing_artifacts(tmp_path: Path) -> None:
    _app, client, run_id, output = _prepared_app(tmp_path)

    response = client.post(
        f"/runs/{run_id}/pages/page_1/edit",
        data={
            "stage1_text": "corrected transcription\nsecond line",
            "stage2_text": "\\lx corrected\n\\ge revised gloss",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/pages/page_1?saved=1")
    assert (output / "stage-1/page_1/page_1_stage1_flat.txt").read_text(
        encoding="utf-8"
    ) == "corrected transcription\nsecond line"
    assert (output / "stage-2/page_1/page_1.mdf.txt").read_text(
        encoding="utf-8"
    ) == "\\lx corrected\n\\ge revised gloss"


def test_page_editor_has_natural_slider_navigation_and_no_event_panel(
    tmp_path: Path,
) -> None:
    _app, client, run_id, output = _prepared_app(tmp_path)
    pages = tmp_path / "pages"
    for number in (2, 10):
        (pages / f"page_{number}.png").write_bytes(b"safe source image")
        stage1 = output / f"stage-1/page_{number}"
        stage1.mkdir(parents=True)
        (stage1 / f"page_{number}_stage1_flat.txt").write_text(
            f"page {number}", encoding="utf-8"
        )

    response = client.get(f"/runs/{run_id}/pages/page_2")

    assert response.status_code == 200
    assert "Page events" not in response.text
    assert 'type="range"' in response.text
    assert 'min="0"' in response.text
    assert 'max="2"' in response.text
    assert 'value="1"' in response.text
    assert f'href="/runs/{run_id}/pages/page_1"' in response.text
    assert f'href="/runs/{run_id}/pages/page_10"' in response.text


def test_page_detail_uses_source_pdf_for_pdf_backed_output(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    source_pdf = tmp_path / "dictionary.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\nsource dictionary")
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": source_pdf, "dictionary_pages": "34"},
            "output": {"directory": output},
            "pipeline": {"stage": "all"},
        }
    )
    run_id = "pdf-artifact-run"
    app.state.job_controller.prepare_inference(
        run_id,
        config=config,
        provider=Provider.ANTHROPIC,
    )
    stage1 = output / "stage-1/page_34"
    stage1.mkdir(parents=True)
    (stage1 / "page_34_stage1_flat.txt").write_text(
        "PDF transcription", encoding="utf-8"
    )
    stage2 = output / "stage-2/page_34"
    stage2.mkdir(parents=True)
    (stage2 / "page_34_mdf.txt").write_text("\\lx PDF", encoding="utf-8")
    client = TestClient(app)

    detail = client.get(f"/runs/{run_id}/pages/page_34")
    source = client.get(f"/runs/{run_id}/pages/page_34/source")

    assert detail.status_code == 200
    assert "PDF transcription" in detail.text
    assert '<iframe class="source-document"' in detail.text
    assert f'/runs/{run_id}/pages/page_34/source#page=34' in detail.text
    assert "Page events" not in detail.text
    assert source.status_code == 200
    assert source.content == source_pdf.read_bytes()
    assert source.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in source.headers["content-security-policy"]


def test_source_page_route_rejects_unknown_or_unsafe_page(tmp_path: Path) -> None:
    _app, client, run_id, _output = _prepared_app(tmp_path)

    assert client.get(f"/runs/{run_id}/pages/not-a-page/source").status_code == 404
    assert client.get(f"/runs/{run_id}/pages/%2E%2E/source").status_code == 404
