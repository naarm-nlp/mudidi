"""Rendered and browser-script contracts for the dynamic model UI."""

from __future__ import annotations

import subprocess
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

from fastapi.testclient import TestClient
from mudidi.web.app import create_app


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.elements.append((tag, dict(attrs)))


def _elements(html: str) -> list[tuple[str, dict[str, str | None]]]:
    collector = _ElementCollector()
    collector.feed(html)
    return collector.elements


def test_model_selects_declare_stages_and_single_refresh_control(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")
    elements = _elements(response.text)

    stages = [
        attrs["data-model-stage"]
        for tag, attrs in elements
        if tag == "select" and "data-model-stage" in attrs
    ]
    refreshes = [attrs for _tag, attrs in elements if "data-model-refresh" in attrs]
    statuses = [attrs for _tag, attrs in elements if "data-model-status" in attrs]

    assert stages == ["stage1", "stage2", "stage2", "verification", "verification"]
    assert len(refreshes) == 1
    assert len(statuses) == 1
    assert statuses[0]["role"] == "status"
    assert statuses[0]["aria-live"] == "polite"
    assert all(
        "disabled" in attrs
        for tag, attrs in elements
        if tag == "select" and "data-model-stage" in attrs
    )
    assert "data-model-provider" not in response.text


def test_initial_authentication_panels_show_all_cards_for_active_mode(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")
    elements = _elements(response.text)

    api_panel = next(
        attrs for _tag, attrs in elements if "data-api-credential-entry" in attrs
    )
    subscription_panel = next(
        attrs for _tag, attrs in elements if "data-subscription-entry" in attrs
    )
    credential_cards = {
        attrs["data-provider"]: attrs
        for _tag, attrs in elements
        if "data-credential-card" in attrs
    }
    subscription_cards = {
        attrs["data-subscription-provider"]: attrs
        for _tag, attrs in elements
        if "data-subscription-card" in attrs
    }

    assert "hidden" not in api_panel
    assert "hidden" in subscription_panel
    assert all("hidden" not in attrs for attrs in credential_cards.values())
    assert all("hidden" not in attrs for attrs in subscription_cards.values())


def test_browser_script_fetches_grouped_catalogs_and_filters_auth_panels() -> None:
    script = files("mudidi.web").joinpath("static/app.js").read_text(encoding="utf-8")

    assert "const loadModelCatalog" in script
    assert "const applyModelCatalog" in script
    assert "new AbortController()" in script
    assert "Promise.all" in script
    assert 'document.createElement("optgroup")' in script
    assert "Recommended for this stage" in script
    assert "Available from your account" in script
    assert "Available from your subscription — newest first" in script
    assert "if (!subscription) {" in script
    assert "const synchronizeCredentialPanels" in script
    assert "apiCredentialEntry.hidden = subscription" in script
    assert "subscriptionEntry.hidden = !subscription" in script
    assert 'const manualEntry = provider === "custom";' in script
    assert "providerIsAuthenticated(target.provider)" in script
    assert (
        'modelStatus.textContent = "Authenticate a provider to load models."' in script
    )
    assert "loadModelCatalog({force: true})" in script
    load_body = script[
        script.index("const loadModelCatalog") : script.index(
            "const synchronizeCredentialPanels"
        )
    ]
    assert "migrateStage2CachesForProvider();" in load_body
    assert "forcedProviders" not in load_body
    assert 'if (force) url.searchParams.set("force", "true");' in load_body
    assert 'payload.warning?.code === "provider_unavailable"' in script


def test_restoring_unlisted_preset_model_creates_current_selection() -> None:
    script = files("mudidi.web").joinpath("static/app.js")
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tagName = "OPTION") {
    this.tagName = tagName;
    this.dataset = {};
    this.children = [];
    this._value = "";
    this.textContent = "";
  }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  get options() {
    return this.children.flatMap((child) => (
      child.tagName === "OPTGROUP" ? child.options : [child]
    ));
  }
  get selectedOptions() {
    return this.options.filter((option) => option.value === this.value);
  }
  get value() { return this._value; }
  set value(next) {
    this._value = this.tagName === "SELECT"
      && !this.options.some((option) => option.value === next)
      ? ""
      : next;
  }
  hasAttribute(name) {
    return name === "data-model-stage" && this.dataset.modelStage !== undefined;
  }
}

const document = {
  body: {append() {}},
  addEventListener() {},
  createElement(tagName) { return new Element(tagName.toUpperCase()); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const window = {
  confirm: () => true,
  fetch: async () => { throw new Error("not used"); },
  location: {origin: "http://test"},
  addEventListener() {},
  sessionStorage: {getItem: () => null, setItem() {}},
};
const context = vm.createContext({
  URL,
  URLSearchParams,
  console,
  document,
  queueMicrotask,
  window,
});
const source = fs.readFileSync(process.argv[1], "utf8");
vm.runInContext(
  `${source}
globalThis.__restoreModelFieldValue = restoreModelFieldValue;
globalThis.__applyModelCatalog = applyModelCatalog;
globalThis.__catalogStatusMessage = catalogStatusMessage;
globalThis.__shouldPreserveModelSelection = shouldPreserveModelSelection;`,
  context,
);

const select = new Element("SELECT");
select.dataset.modelStage = "stage1";
const bundled = new Element();
bundled.value = "openai/gpt-bundled";
select.append(bundled);
context.__restoreModelFieldValue(select, "openai/gpt-retired");
            if (select.dataset.catalogProvider !== "openai") {
              throw new Error("Restored model provider was not preserved while loading");
            }
context.__applyModelCatalog(select, {
  recommended: [{
    model_id: "openai/gpt-current",
    display_name: "GPT Current",
    compatibility: "verified",
  }],
  available: [],
}, "openai");
if (select.value !== "openai/gpt-retired") {
  throw new Error("unlisted current model was not restored");
}
const restored = select.options.find((option) => option.value === "openai/gpt-retired");
if (!restored || restored.dataset.modelProvider !== "openai") {
  throw new Error("restored model option did not retain provider identity");
}
if ((restored.textContent.match(/current selection/g) || []).length !== 1) {
  throw new Error("restored model option has a duplicated explanation");
}
context.__applyModelCatalog(select, {
  recommended: [],
  available: [],
  warning: {code: "authentication_required"},
}, "openai");
if (!select.disabled || select.value !== "") {
  throw new Error("authentication expiry did not disable and clear the model");
}
if (select.options.length !== 1 || select.options[0].value !== "") {
  throw new Error("authentication expiry exposed a selectable model option");
}
const statusMessage = context.__catalogStatusMessage;
if (statusMessage([{
  stale: false,
  source: "bundled",
  warning: {code: "provider_unavailable"},
}]) !== "Provider model discovery is unavailable.") {
  throw new Error("uncached provider failure status was not explicit");
}
if (statusMessage([{
  stale: true,
  source: "stale",
  warning: {code: "provider_unavailable"},
}]) !== "Provider unavailable — showing the last successful model list.") {
  throw new Error("stale status did not take precedence over its warning");
}
if (!context.__shouldPreserveModelSelection(true, "anthropic", "openai")) {
  throw new Error("deleting Anthropic reset an unrelated OpenAI selection");
}
if (context.__shouldPreserveModelSelection(true, "anthropic", "anthropic")) {
  throw new Error("deleted provider selection was preserved");
}
if (context.__shouldPreserveModelSelection(false, null, "openai")) {
  throw new Error("global selection reset no longer applies");
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
