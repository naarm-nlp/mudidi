"""Browser acceptance tests for the localhost production application."""

from __future__ import annotations

import base64
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from mudidi.web.app import create_app
from mudidi.web.credentials import CredentialVault, PersistentCredentialStore
from mudidi.web.models import Provider

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def local_site(tmp_path: Path) -> Iterator[str]:
    """Serve one offline app on an ephemeral loopback port."""

    data_dir = tmp_path / "app-data"
    vault = CredentialVault(
        environ={},
        persistent_store=PersistentCredentialStore(
            database_path=data_dir / "mudidi-web.sqlite3",
            key_path=data_dir / ".credential-key",
        ),
    )
    vault.set_persistent(Provider.ANTHROPIC, "sk-ant-browser-e2e")
    app = create_app(
        data_dir=data_dir,
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
    page.locator('select[name="stage1_model"]').select_option(
        "anthropic/claude-sonnet-5"
    )
    page.locator('select[name="stage2_pass1_model"]').select_option(
        "anthropic/claude-sonnet-5"
    )
    page.locator('select[name="stage2_pass2_model"]').select_option(
        "anthropic/claude-sonnet-5"
    )
    page.get_by_role("button", name="Review run").click()

    expect(page.get_by_role("heading", name="Review your run")).to_be_visible()
    page.get_by_role("button", name="Start run").click()
    expect(page.get_by_role("link", name="Review MDF parsing guide →")).to_be_visible(
        timeout=10_000
    )
    page.get_by_role("link", name="Review MDF parsing guide →").click()

    expect(page.get_by_role("heading", name="Review MDF parsing guide")).to_be_visible()
    page.get_by_role("button", name="Approve and continue MDF parsing").click()
    expect(page.get_by_text("Completed", exact=True)).to_be_visible(timeout=10_000)

    page.get_by_role("link", name="Output Preview").click()
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


def test_new_run_defaults_to_gemini_flash(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    page.goto(local_site)

    expect(page.locator('select[name="provider"]')).to_have_value("gemini")
    for field in ("stage1_model", "stage2_pass1_model", "stage2_pass2_model"):
        expect(page.locator(f'select[name="{field}"]')).to_have_value(
            "gemini/gemini-3.5-flash"
        )
    expect(page.locator("#credential-gemini")).to_have_attribute("type", "password")


def test_reasoning_controls_follow_their_pipeline_models(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    page.goto(local_site)

    stage1 = page.locator('select[name="stage1_reasoning"]')
    pass1 = page.locator('select[name="stage2_pass1_reasoning"]')
    pass2 = page.locator('select[name="stage2_pass2_reasoning"]')
    expect(stage1).to_be_visible()
    expect(pass1).to_be_visible()
    expect(pass2).to_be_visible()

    page.locator('input[name="pipeline"][value="transcription"]').check()
    expect(stage1).to_be_visible()
    expect(pass1).to_be_hidden()
    expect(pass2).to_be_hidden()

    page.locator('input[name="pipeline"][value="structure"]').check()
    expect(stage1).to_be_hidden()
    expect(pass1).to_be_visible()
    expect(pass2).to_be_visible()


def test_provider_key_is_masked_and_revealed_only_on_request(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    expected_value = "browser-e2e-dummy-value"
    page.goto(local_site)

    field = page.locator("#credential-openai")
    field.fill(expected_value)
    page.get_by_role("button", name="Save OpenAI API key").click()

    expect(page.get_by_text("Saved", exact=True)).to_be_visible()
    field = page.locator("#credential-openai")
    expect(field).to_have_attribute("type", "password")
    expect(field).to_have_value("")

    reveal = page.get_by_role("button", name="Show OpenAI API key")
    reveal.click()
    expect(field).to_have_attribute("type", "text")
    expect(field).to_have_value(expected_value)

    page.get_by_role("button", name="Hide openai API key").click()
    expect(field).to_have_attribute("type", "password")


def test_info_tooltip_only_appears_while_hovering_the_icon(
    local_site: str,
    browser_page: Page,
) -> None:
    page = browser_page
    page.goto(local_site)
    button = page.get_by_role("button", name="About temperature")
    heading = button.locator("xpath=..")

    box = heading.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + 3, box["y"] + box["height"] / 2)
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "0"

    button.dispatch_event("pointerenter")
    expect(button).to_have_class(re.compile("is-tooltip-hovered"))
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "1"
    button.dispatch_event("pointerleave")
    expect(button).not_to_have_class(re.compile("is-tooltip-hovered"))
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "0"

    button.click()
    page.mouse.move(0, 0)
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "0"

    button.hover()
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "1"

    page.mouse.move(0, 0)
    page.wait_for_timeout(200)
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "0"


def test_invalid_preview_restores_safe_form_values(
    local_site: str,
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    output = str(tmp_path / "remembered-output")
    page.goto(local_site)
    page.get_by_label("Output directory").fill(output)
    page.locator('input[name="page_files"]').set_input_files(
        {"name": "page_1.png", "mimeType": "image/png", "buffer": _PNG}
    )
    page.evaluate(
        """
        () => {
          const temperature = document.querySelector('input[name="temperature"]');
          temperature.value = "-1";
          temperature.dispatchEvent(new Event("input", { bubbles: true }));
          document.querySelector("form.run-form").submit();
        }
        """
    )

    expect(page.get_by_text("Temperature", exact=True)).to_be_visible()
    page.get_by_role("link", name="Return to New run").click()
    expect(page.get_by_label("Output directory")).to_have_value(output)
    expect(page.locator('input[name="temperature"]')).to_have_value("-1")
    expect(page.locator('input[name="page_files"]')).to_have_value("")


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

    evaluator_provider = page.locator('select[name="evaluator_provider"]')
    evaluator_model = page.locator('select[name="evaluator_model"]')
    evaluator_custom = page.locator('input[name="evaluator_custom_model"]')
    evaluator_provider.select_option("openai")
    expect(
        evaluator_model.locator('option[data-model-provider="openai"]').first
    ).to_be_enabled()
    gemini_option = evaluator_model.locator(
        'option[data-model-provider="gemini"]'
    ).first
    assert gemini_option.get_attribute("disabled") is not None
    assert gemini_option.get_attribute("hidden") is not None
    evaluator_model.select_option("__other__")
    expect(evaluator_custom).to_be_visible()
    expect(evaluator_custom).to_have_attribute(
        "placeholder", "Enter a model name supported by OpenAI"
    )

    page.locator('select[name="provider"]').select_option("gemini")
    stage1_model = page.locator('select[name="stage1_model"]')
    stage1_model.select_option("__other__")
    expect(page.locator('input[name="stage1_custom_model"]')).to_have_attribute(
        "placeholder", "Enter a model name supported by Google Gemini"
    )

    page.locator('input[name="pipeline"][value="transcription"]').check()
    expect(page.get_by_label("Verify Stage 2")).to_be_disabled()
    expect(page.locator("[data-mdf-manual]")).to_be_hidden()

    page.locator('input[name="pipeline"][value="structure"]').check()
    expect(page.get_by_label("Verify Stage 1")).to_be_disabled()
    expect(page.locator("[data-mdf-manual]")).to_be_visible()
    official_manual = page.get_by_role(
        "link", name="Open or download the official SIL MDF manual"
    )
    expect(official_manual).to_be_visible()
    expect(official_manual).to_have_attribute(
        "href",
        "http://www.fieldlinguiststoolbox.org/ToolboxReferenceManual.pdf",
    )
    manual_help = page.get_by_role("button", name="About the optional MDF manual")
    expect(manual_help).to_have_attribute(
        "data-help", re.compile("only the pages that describe the MDF markers or tags")
    )
    expect(manual_help).to_have_attribute(
        "data-help", re.compile("run Complete digitization first")
    )
    manual_help.hover()
    page.wait_for_timeout(200)
    assert manual_help.evaluate(
        "element => getComputedStyle(element, '::after').opacity"
    ) == "1"
    page.get_by_label("Upload my own MDF manual").check()
    expect(page.locator("[data-custom-mdf-manual]")).to_be_visible()


def test_saved_preset_loads_back_into_editable_form(
    local_site: str,
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    output = tmp_path / "preset-output"

    page.goto(local_site)
    page.locator('input[name="page_files"]').set_input_files(
        {"name": "page_1.png", "mimeType": "image/png", "buffer": _PNG}
    )
    page.get_by_label("Output directory").fill(str(output))
    page.locator("select[name=provider]").select_option("anthropic")
    for name in ("stage1_model", "stage2_pass1_model", "stage2_pass2_model"):
        page.locator(f'select[name="{name}"]').select_option(
            "anthropic/claude-sonnet-5"
        )
    page.get_by_role("button", name="Review run").click()
    page.get_by_label("Save these non-secret settings as a preset").fill(
        "Editable dictionary setup"
    )
    page.get_by_role("button", name="Save preset").click()

    page.get_by_role("link", name="Load preset").click()

    expect(page.get_by_text("Loaded preset: Editable dictionary setup")).to_be_visible()
    expect(page.get_by_label("Output directory")).to_have_value(str(output))
    expect(page.locator('select[name="provider"]')).to_have_value("anthropic")
    page.get_by_role("button", name="Review run").click()
    expect(page.get_by_role("heading", name="Review your run")).to_be_visible()
