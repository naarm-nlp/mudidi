"""Browser acceptance tests for the localhost production application."""

from __future__ import annotations

import base64
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialVault
from mudidi.web.models import Provider

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def local_site(tmp_path: Path) -> Iterator[str]:
    """Serve one offline app on an ephemeral loopback port."""

    vault = CredentialVault(environ={})
    vault.set_temporary(Provider.ANTHROPIC, "sk-ant-browser-e2e")
    app = create_app(
        data_dir=tmp_path / "app-data",
        credential_vault=vault,
        offline_inference=True,
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("test web server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def browser_page() -> Iterator[Page]:
    """Provide an isolated headless Chromium page."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        yield page
        browser.close()


def test_complete_production_journey_requires_and_uses_review(
    local_site: str,
    browser_page: Page,
    tmp_path: Path,
) -> None:
    output = tmp_path / "web-output"
    page = browser_page

    page.goto(local_site)
    page.locator('input[name="page_files"]').set_input_files(
        {"name": "page_1.png", "mimeType": "image/png", "buffer": _PNG}
    )
    page.get_by_label("Output directory").fill(str(output))
    page.locator("select[name=provider]").select_option("anthropic")
    page.get_by_label("Stage 1 model").select_option("anthropic/claude-sonnet-5")
    page.get_by_label("Stage 2 Pass 1 model").select_option("anthropic/claude-sonnet-5")
    page.get_by_label("Stage 2 Pass 2 model").select_option("anthropic/claude-sonnet-5")
    page.get_by_role("button", name="Review run").click()

    expect(page.get_by_role("heading", name="Review your run")).to_be_visible()
    page.get_by_role("button", name="Start run").click()
    expect(page.get_by_role("link", name="Review MDF parsing guide →")).to_be_visible(
        timeout=10_000
    )
    page.get_by_role("link", name="Review MDF parsing guide →").click()

    expect(page.get_by_role("heading", name="Review MDF parsing guide")).to_be_visible()
    page.get_by_label("Dictionary name").fill("Browser-approved dictionary")
    page.get_by_role("button", name="Approve and continue MDF parsing").click()
    expect(page.get_by_text("Completed", exact=True)).to_be_visible(timeout=10_000)

    page.get_by_role("link", name="Pages").click()
    expect(page.get_by_text("offline transcription")).to_be_visible()
    expect(page.get_by_text("\\lx offline")).to_be_visible()


def test_active_run_updates_from_sse_without_manual_refresh(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    page.goto(local_site)

    page.get_by_role("button", name="Start offline demo").click()

    expect(page.get_by_text("Completed", exact=True)).to_be_visible(timeout=10_000)


def test_agentic_and_manual_controls_follow_pipeline(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    page.goto(local_site)
    page.get_by_text("Input and context", exact=True).click()

    settings = page.locator("[data-agentic-settings]")
    expect(settings).to_be_hidden()
    page.get_by_label("Yes", exact=True).check()
    expect(settings).to_be_visible()
    expect(page.get_by_label("Verify Stage 1")).to_be_checked()
    expect(page.get_by_label("Verify Stage 2")).to_be_checked()

    page.locator('input[name="pipeline"][value="transcription"]').check()
    expect(page.get_by_label("Verify Stage 2")).to_be_disabled()
    expect(page.locator("[data-mdf-manual]")).to_be_hidden()

    page.locator('input[name="pipeline"][value="structure"]').check()
    expect(page.get_by_label("Verify Stage 1")).to_be_disabled()
    expect(page.locator("[data-mdf-manual]")).to_be_visible()
    page.get_by_label("Upload a custom or shortened MDF manual").check()
    expect(page.locator("[data-custom-mdf-manual]")).to_be_visible()
