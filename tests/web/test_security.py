"""Security-boundary tests for the localhost HTTP application."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app


def test_cross_origin_state_change_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/runs/demo",
        data={"page_count": "1"},
        headers={"origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert "origin" in response.text.lower()


def test_same_origin_state_change_is_allowed(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/runs/demo",
        data={"page_count": "1", "delay_seconds": "0"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    app.state.job_controller.wait(run_id, timeout=5)


def test_security_headers_are_present_on_html(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_oversized_declared_request_is_rejected_before_form_parsing(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(data_dir=tmp_path, max_request_bytes=128, max_upload_bytes=64)
    )

    response = client.post(
        "/runs/demo",
        content=b"x",
        headers={"content-length": "129"},
    )

    assert response.status_code == 413


def test_false_small_content_length_cannot_bypass_request_limit(tmp_path: Path) -> None:
    client = TestClient(
        create_app(data_dir=tmp_path, max_request_bytes=128, max_upload_bytes=64)
    )

    response = client.post(
        "/runs/demo",
        content=b"page_count=1&padding=" + b"x" * 129,
        headers={
            "content-length": "1",
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 413


def test_default_byte_limits_allow_large_local_upload_ceiling(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path)

    assert app.state.max_request_bytes == 110 * 1024 * 1024
    assert app.state.inputs.max_total_bytes == 100 * 1024 * 1024


def test_configured_request_and_upload_limits_are_applied(tmp_path: Path) -> None:
    app = create_app(
        data_dir=tmp_path,
        max_request_bytes=128,
        max_upload_bytes=64,
    )
    assert app.state.max_request_bytes == 128
    assert app.state.inputs.max_total_bytes == 64

    response = TestClient(app).post(
        "/runs/demo",
        content=b"x" * 129,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413


def test_request_limit_must_exceed_upload_limit(tmp_path: Path) -> None:
    try:
        create_app(
            data_dir=tmp_path,
            max_request_bytes=64,
            max_upload_bytes=64,
        )
    except ValueError as exc:
        assert "request byte limit must exceed upload byte limit" in str(exc)
    else:
        raise AssertionError("request limit must leave room for multipart overhead")


def test_llm_derived_page_text_is_html_escaped(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(b"safe source image")
    output = tmp_path / "output"
    from mudidi.config.yaml_config import InferenceConfig
    from mudidi.web.models import Provider

    config = InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": output},
            "pipeline": {"stage": "1"},
        }
    )
    app.state.job_controller.prepare_inference(
        "xss-run", config=config, provider=Provider.ANTHROPIC
    )
    page_dir = output / "stage-1/page_1"
    page_dir.mkdir(parents=True)
    (page_dir / "page_1_stage1_flat.txt").write_text(
        '<script>alert("xss")</script>', encoding="utf-8"
    )

    response = TestClient(app).get("/runs/xss-run/pages")

    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text
