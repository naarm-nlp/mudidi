"""Integration tests for the local web application shell."""

from __future__ import annotations

import subprocess
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mudidi.web.app import _validation_errors, create_app
from mudidi.web.credentials import CredentialVault, PersistentCredentialStore
from mudidi.web.models import Provider


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def test_home_page_exposes_primary_local_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "New run" in response.text
    assert "Active run" in response.text
    assert "Run history" in response.text
    assert "Input" in response.text
    assert "Pipeline" in response.text
    assert "MDF parsing guide" in response.text
    assert 'data-new-run-wizard' in response.text
    for step in ("input", "pipeline", "model", "agentic"):
        assert f'id="wizard-{step}"' in response.text
        assert f'data-wizard-panel="{step}"' in response.text
    assert response.text.count('aria-current="step"') == 1
    assert 'data-wizard-marker="input" aria-current="step"' in response.text
    assert 'action="/runs/preview"' in response.text
    assert 'data-wizard-submit' in response.text
    panel_order = [
        response.text.index(f'data-wizard-panel="{step}"')
        for step in ("input", "pipeline", "model", "agentic")
    ]
    assert panel_order == sorted(panel_order)
    assert 'aria-labelledby="wizard-input-title"' in response.text
    assert 'aria-labelledby="wizard-pipeline-title"' in response.text
    assert 'aria-labelledby="wizard-model-title"' in response.text
    assert 'aria-labelledby="wizard-agentic-title"' in response.text
    assert 'name="dictionary_pages"' in response.text
    assert 'name="stage1_model"' in response.text
    assert 'name="stage2_pass1_model"' in response.text
    assert 'name="stage2_pass2_model"' in response.text
    for provider in ("gemini", "openai", "anthropic", "openrouter"):
        assert response.text.count(f'id="credential-{provider}"') == 1
        assert response.text.count(f'id="credential-status-{provider}"') == 1
        assert f'data-save-key data-provider="{provider}"' in response.text
    assert 'href="/providers"' not in response.text
    assert response.text.count('type="password"') >= 4
    assert response.text.count('class="eye-icon eye-show"') == 4
    assert response.text.count('class="eye-icon eye-hide"') == 4
    assert 'name="provider"' in response.text
    assert response.text.count('name="provider"') == 1
    assert '<input type="hidden" name="provider" value="gemini" data-provider-value' in response.text
    assert 'data-provider-value' in response.text
    assert response.text.count("data-provider-choice") >= 2
    assert 'data-stage2-mode="shared"' in response.text
    assert 'data-stage2-toggle' in response.text
    assert "Advanced · split passes" in response.text
    assert 'data-stage2-pass="pass1"' in response.text
    assert 'data-stage2-pass="pass2"' in response.text
    assert 'data-selected-credential' in response.text
    assert 'data-other-credentials' in response.text
    assert "◉" not in response.text
    assert 'name="stage1_reasoning"' in response.text
    assert 'name="stage2_pass1_reasoning"' in response.text
    assert 'name="stage2_pass2_reasoning"' in response.text
    assert 'name="reasoning"' not in response.text
    assert 'name="stage1_custom_model"' in response.text
    assert 'name="openrouter_provider"' in response.text
    assert 'data-model-provider="openai"' in response.text
    assert 'data-model-provider="anthropic"' in response.text
    assert 'data-model-provider="gemini"' in response.text
    assert "OpenRouter Provider" in response.text
    assert "qwen/qwen3-235b-a22b" in response.text
    assert 'data-pipeline-stages="stage1"' in response.text
    assert "GPT-5.6 Sol" in response.text
    assert "GPT-5.6 Terra" in response.text
    assert "GPT-5.6 Luna" in response.text
    assert "Claude Fable 5" in response.text
    assert "Claude Opus 4.8" in response.text
    assert "Claude Sonnet 5" in response.text
    assert "Claude Haiku 4.5" in response.text
    assert "Gemini 3.1 Pro" in response.text
    assert "Gemini 3.5 Flash" in response.text
    assert "Gemini 3.1 Flash-Lite" in response.text
    assert "Other / advanced provider" in response.text
    assert "Stage and model overrides" not in response.text
    assert 'name="verify_stage1"' in response.text
    assert 'name="verify_stage2"' in response.text
    assert 'name="verify_stage1" type="checkbox" value="true" checked disabled' in response.text
    assert 'name="verify_stage2" type="checkbox" value="true" checked disabled' in response.text
    assert 'name="agentic" value="false" checked' in response.text
    assert 'name="agentic" value="true"' in response.text
    assert '<fieldset class="agentic-settings" data-agentic-settings hidden>' in response.text
    for field in (
        "max_iterations",
        "min_retry_confidence",
        "evaluator_provider",
        "evaluator_model",
        "evaluator_reasoning",
        "rewriter_provider",
        "rewriter_model",
        "rewriter_reasoning",
        "verifier_patches",
        "require_concrete_retry",
    ):
        assert f'name="{field}"' in response.text
    assert 'data-agentic-model-group="evaluator"' in response.text
    assert 'data-agentic-model-group="rewriter"' in response.text
    assert 'list="model-catalog"' not in response.text
    assert (
        'name="evaluator_reasoning" disabled><option value="">Use default</option>'
        '<option value="none">None</option><option value="low">Low</option>'
        '<option value="medium">Medium</option><option value="high" selected>High</option>'
        in response.text
    )
    assert (
        'name="rewriter_reasoning" disabled><option value="">Use default</option>'
        '<option value="none">None</option><option value="low" selected>Low</option>'
        in response.text
    )
    assert 'data-wizard-submit' in response.text
    assert "Review run" in response.text
    assert 'name="page_limit"' not in response.text
    assert 'name="media_reference"' not in response.text
    assert 'name="prompt_cache"' not in response.text
    assert response.text.index('name="temperature"') < response.text.index(
        'name="batch_size"'
    )
    assert response.text.index('name="batch_size"') < response.text.index(
        'data-wizard-panel="agentic"'
    )
    assert 'aria-label="About temperature"' in response.text
    assert 'aria-label="About Stage 1 model reasoning"' in response.text
    assert 'aria-label="About Stage 2 Pass 1 model reasoning"' in response.text
    assert 'aria-label="About Stage 2 Pass 2 model reasoning"' in response.text
    assert "requests sent in parallel to your model provider" in response.text
    assert "Check the rate limits for your provider and selected model" in response.text
    assert 'name="strategy"' not in response.text
    assert 'name="vlm_model"' not in response.text
    assert 'name="mathpix_max_wait_seconds"' not in response.text
    assert 'name="output_policy"' in response.text
    assert "Require a new or empty directory" not in response.text
    assert (
            '<input type="radio" name="output_policy" value="resume" checked required>'
        in response.text
    )
    assert "Resume compatible existing artifacts" in response.text
    assert (
        '<input type="radio" name="output_policy" value="overwrite">'
        in response.text
    )
    assert "Overwrite existing artifacts" in response.text
    assert 'aria-label="About resuming existing artifacts"' in response.text
    assert 'aria-label="About overwriting existing artifacts"' in response.text
    assert "after an interrupted run" in response.text
    assert "inputs, models, instructions, or settings changed" in response.text
    assert '<select name="output_policy">' not in response.text
    assert "Dictionary Profile (optional)" in response.text
    assert "can improve extraction accuracy" in response.text
    assert 'name="profile_headword_language"' in response.text
    assert 'name="profile_target_languages"' in response.text
    assert 'aria-label="Remove language"' in response.text
    assert 'name="profile_headword_script"' in response.text
    assert 'name="profile_page_layout"' in response.text
    assert 'name="profile_information_types"' in response.text
    assert 'name="profile_other_information_types"' in response.text
    assert 'class="profile-other-information"' in response.text
    assert (
        "1–2. What language are the dictionary headwords written in, and what script do they use?"
        in response.text
    )
    assert (
        "3–4. Which languages are used for translations, glosses, or definitions, and which script does each use?"
        in response.text
    )
    assert "5. How is information arranged on the page?" in response.text
    assert 'class="profile-layout-question"' in response.text
    assert (
        "There are two columns; each column contains independent dictionary entries."
        in response.text
    )
    assert "6. Which information types appear in an entry?" in response.text
    assert 'name="dictionary_languages"' not in response.text
    assert 'name="stage1_typography"' not in response.text
    assert "/static/app.js?v=dashboard-ui-1" in response.text
    assert "Start offline demo" not in response.text
    assert 'action="/runs/demo"' not in response.text


def test_agentic_pipeline_sync_keeps_off_controls_out_of_form_data_and_restores_on() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Field {
  constructor({
    name = "",
    value = "",
    type = "text",
    checked = false,
    disabled = false,
  } = {}) {
    this.name = name;
    this._value = value;
    this.type = type;
    this.checked = checked;
    this.disabled = disabled;
    this.hidden = false;
    this.required = false;
    this.dataset = {};
    this.parentElement = this;
    this.options = [];
    this.selectedOptions = [];
  }
  get value() { return this._value; }
  set value(value) {
    this._value = value;
    this.selectedOptions = this.options.filter((option) => option.value === value);
  }
  addEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  setAttribute() {}
  removeAttribute() {}
}
class Fields extends Array {
  namedItem(name) {
    return this.find((field) => field.name === name) || null;
  }
}

const off = new Field({name: "agentic", value: "false", checked: true});
const on = new Field({name: "agentic", value: "true"});
const complete = new Field({name: "pipeline", value: "complete", checked: true});
const transcription = new Field({name: "pipeline", value: "transcription"});
const structure = new Field({name: "pipeline", value: "structure"});
const verifyStage1 = new Field({
  name: "verify_stage1",
  value: "true",
  type: "checkbox",
  checked: true,
  disabled: true,
});
const verifyStage2 = new Field({
  name: "verify_stage2",
  value: "true",
  type: "checkbox",
  checked: true,
  disabled: true,
});
const maxIterations = new Field({name: "max_iterations", value: "2", disabled: true});
const minConfidence = new Field({name: "min_retry_confidence", value: "0.55", disabled: true});
const evaluatorProvider = new Field({
  name: "evaluator_provider",
  value: "gemini",
  disabled: true,
});
const evaluatorModel = new Field({name: "evaluator_model", value: "", disabled: true});
const evaluatorCustom = new Field({
  name: "evaluator_custom_model",
  value: "",
  disabled: true,
});
const evaluatorReasoning = new Field({
  name: "evaluator_reasoning",
  value: "high",
  disabled: true,
});
const rewriterProvider = new Field({
  name: "rewriter_provider",
  value: "gemini",
  disabled: true,
});
const rewriterModel = new Field({name: "rewriter_model", value: "", disabled: true});
const rewriterCustom = new Field({
  name: "rewriter_custom_model",
  value: "",
  disabled: true,
});
const rewriterReasoning = new Field({
  name: "rewriter_reasoning",
  value: "low",
  disabled: true,
});
const verifierPatches = new Field({
  name: "verifier_patches",
  value: "true",
  disabled: true,
});
const requireConcreteRetry = new Field({
  name: "require_concrete_retry",
  value: "true",
  disabled: true,
});
const modelOption = (value, modelProvider = "") => ({
  value,
  dataset: {modelProvider},
  hidden: false,
  disabled: false,
});
const modelOptions = [
  modelOption(""),
  modelOption("gemini/gemini-3.5-flash", "gemini"),
  modelOption("__other__", "other"),
];
[evaluatorModel, rewriterModel].forEach((select) => {
  select.options = modelOptions.map((option) => ({...option, dataset: {...option.dataset}}));
  select.value = select.value;
});
const advancedFields = [
  verifyStage1,
  verifyStage2,
  maxIterations,
  minConfidence,
  evaluatorProvider,
  evaluatorModel,
  evaluatorCustom,
  evaluatorReasoning,
  rewriterProvider,
  rewriterModel,
  rewriterCustom,
  rewriterReasoning,
  verifierPatches,
  requireConcreteRetry,
];
const runForm = new Field();
runForm.elements = new Fields(
  off, on, complete, transcription, structure, ...advancedFields,
);
const agenticSettings = new Field();
agenticSettings.hidden = true;
agenticSettings.querySelectorAll = (selector) => (
  selector === "input, select, textarea" ? advancedFields : []
);
const makeAgenticGroup = (provider, model, custom) => ({
  querySelector(selector) {
    if (selector === "[data-agentic-provider]") return provider;
    if (selector === "[data-agentic-model]") return model;
    if (selector === "[data-agentic-custom-model]") return custom;
    return null;
  },
  closest(selector) {
    return selector === "[data-agentic-settings]" ? agenticSettings : null;
  },
});
const agenticGroups = [
  makeAgenticGroup(evaluatorProvider, evaluatorModel, evaluatorCustom),
  makeAgenticGroup(rewriterProvider, rewriterModel, rewriterCustom),
];

const document = {
  body: {append() {}},
  addEventListener() {},
  createElement() { return new Field(); },
  querySelector(selector) {
    if (selector === "form.run-form") return runForm;
    if (selector === "[data-agentic-settings]") return agenticSettings;
    if (selector === 'input[name="verify_stage1"]') return verifyStage1;
    if (selector === 'input[name="verify_stage2"]') return verifyStage2;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === 'input[name="pipeline"]') {
      return [complete, transcription, structure];
    }
    if (selector === 'input[name="agentic"]') return [off, on];
    if (selector === 'input[name="verify_stage1"], input[name="verify_stage2"]') {
      return [verifyStage1, verifyStage2];
    }
    if (selector === "[data-agentic-model-group]") return agenticGroups;
    return [];
  },
};
const window = {
  EventSource: null,
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
  `${source}\nglobalThis.__dashboardTest = {
    synchronizeAgentic,
    synchronizePipeline,
    synchronizeAgenticModelGroup,
  };`,
  context,
);
const sync = context.__dashboardTest;
const formDataFor = (form) => ({
  has(name) {
    return [...form.elements].some((field) => (
      field.name === name
      && !field.disabled
      && (!(field.type === "checkbox" || field.type === "radio") || field.checked)
    ));
  },
});

on.checked = true;
off.checked = false;
sync.synchronizeAgentic();
maxIterations.value = "7";
evaluatorProvider.value = "openai";
verifyStage1.checked = true;
verifyStage1.dataset.userTouched = "true";

on.checked = false;
off.checked = true;
sync.synchronizeAgentic();
complete.checked = false;
transcription.checked = true;
sync.synchronizePipeline();
if (!advancedFields.every((field) => field.disabled)) {
  throw new Error("Agentic Off left an advanced field enabled");
}
const offFormData = formDataFor(runForm);
for (const name of advancedFields.map((field) => field.name)) {
  if (offFormData.has(name)) throw new Error(`Agentic Off submitted ${name}`);
}

on.checked = true;
off.checked = false;
sync.synchronizeAgentic();
if (maxIterations.disabled || maxIterations.value !== "7") {
  throw new Error("Agentic On did not restore correction iterations");
}
if (evaluatorProvider.disabled || evaluatorProvider.value !== "openai") {
  throw new Error("Agentic On did not restore evaluator provider");
}
if (verifyStage1.disabled || !verifyStage1.checked || !verifyStage2.disabled) {
  throw new Error("Agentic On did not compose with active pipeline stages");
}
const onFormData = formDataFor(runForm);
for (const name of ["verify_stage1", "max_iterations", "evaluator_provider"]) {
  if (!onFormData.has(name)) {
    throw new Error(`Agentic On omitted ${name}: ${JSON.stringify(onFormData)}`);
  }
}

const evaluatorGroup = agenticGroups[0];
const setPipeline = (value) => {
  [complete, transcription, structure].forEach((choice) => {
    choice.checked = choice.value === value;
  });
  sync.synchronizePipeline();
};
const assertUseStageModel = (label) => {
  if (evaluatorProvider.value !== "gemini" || evaluatorModel.value !== "") {
    throw new Error(`${label} changed the evaluator stage-model selection`);
  }
  if (evaluatorModel.hidden || evaluatorModel.disabled) {
    throw new Error(`${label} disabled or hid the evaluator model selector`);
  }
  if (!evaluatorCustom.hidden || !evaluatorCustom.disabled) {
    throw new Error(`${label} left the stale evaluator custom input active`);
  }
  if (!rewriterCustom.hidden || !rewriterCustom.disabled) {
    throw new Error(`${label} left the rewriter custom input active`);
  }
  const formData = formDataFor(runForm);
  for (const name of ["evaluator_custom_model", "rewriter_custom_model"]) {
    if (formData.has(name)) throw new Error(`${label} submitted ${name}`);
  }
};

evaluatorProvider.value = "gemini";
sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
evaluatorModel.value = "__other__";
sync.synchronizeAgenticModelGroup(evaluatorGroup);
evaluatorCustom.value = "stale/evaluator-model";
sync.synchronizeAgenticModelGroup(evaluatorGroup);
if (evaluatorCustom.hidden || evaluatorCustom.disabled) {
  throw new Error("Agentic custom evaluator was not enabled for its selected model");
}

evaluatorProvider.value = "openai";
sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
evaluatorProvider.value = "gemini";
sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
evaluatorModel.value = "";
sync.synchronizeAgenticModelGroup(evaluatorGroup);
assertUseStageModel("Initial Gemini fallback");
["transcription", "structure", "complete", "transcription", "complete"].forEach((pipeline) => {
  setPipeline(pipeline);
  assertUseStageModel(`Pipeline ${pipeline}`);
});

const assertManualModel = (provider, value) => {
  evaluatorProvider.value = provider;
  sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
  evaluatorCustom.value = value;
  sync.synchronizeAgenticModelGroup(evaluatorGroup);
  if (!evaluatorModel.hidden || !evaluatorModel.disabled) {
    throw new Error(`${provider} did not hide and disable its model selector`);
  }
  if (evaluatorCustom.hidden || evaluatorCustom.disabled) {
    throw new Error(`${provider} custom model input was not enabled`);
  }
  if (!formDataFor(runForm).has("evaluator_custom_model")) {
    throw new Error(`${provider} custom model was omitted from FormData`);
  }
};
assertManualModel("openrouter", "qwen/qwen3-235b-a22b");
setPipeline("structure");
if (evaluatorCustom.disabled || evaluatorCustom.hidden) {
  throw new Error("Pipeline synchronization disabled a selected OpenRouter custom model");
}
assertManualModel("custom", "provider/model");
setPipeline("transcription");
if (evaluatorCustom.disabled || evaluatorCustom.hidden) {
  throw new Error("Pipeline synchronization disabled a selected custom-provider model");
}

on.checked = false;
off.checked = true;
sync.synchronizeAgentic();
if (!agenticSettings.hidden || !advancedFields.every((field) => field.disabled)) {
  throw new Error("Agentic Off did not disable every advanced field");
}
if (formDataFor(runForm).has("evaluator_custom_model")) {
  throw new Error("Agentic Off submitted the custom evaluator model");
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_live_updates_follow_bfcache_lifecycle_without_duplicate_sources() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[1], "utf8");

class Element {
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.listeners = {};
    this.attributes = {};
    this.textContent = "";
  }
  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }
  querySelector(selector) {
    return this.children?.[selector] || null;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

const makePage = ({withToggle}) => {
  const events = {};
  const sources = [];
  const meta = {content: "/runs/live/events?after=3"};
  const streamStatus = new Element();
  const toggle = withToggle ? new Element() : null;
  if (toggle) {
    const pauseLabel = new Element();
    const resumeLabel = new Element();
    resumeLabel.hidden = true;
    toggle.children = {
      "[data-live-pause-label]": pauseLabel,
      "[data-live-resume-label]": resumeLabel,
    };
  }
  class EventSourceStub {
    constructor(url) {
      this.url = url;
      this.closed = false;
      this.listeners = {};
      sources.push(this);
    }
    addEventListener(type, listener) {
      this.listeners[type] = listener;
    }
    close() {
      this.closed = true;
    }
  }
  const window = {
    EventSource: EventSourceStub,
    confirm: () => true,
    fetch: async () => { throw new Error("not used"); },
    location: {origin: "http://test", reload() {}},
    addEventListener(type, listener) {
      (events[type] ||= []).push(listener);
    },
    sessionStorage: {getItem: () => null, setItem() {}},
  };
  const document = {
    body: {append() {}},
    addEventListener() {},
    createElement() { return new Element(); },
    querySelector(selector) {
      if (selector === 'meta[name="mudidi-events"]') return meta;
      if (selector === "[data-stream-status]") return streamStatus;
      if (selector === "[data-live-toggle]") return toggle;
      return null;
    },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({
    URL,
    URLSearchParams,
    console,
    document,
    queueMicrotask,
    window,
    EventSource: EventSourceStub,
  });
  vm.runInContext(source, context);
  return {
    sources,
    streamStatus,
    toggle,
    dispatch(type, event = {}) {
      (events[type] || []).forEach((listener) => listener(event));
    },
  };
};

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const logs = makePage({withToggle: true});
assert(logs.sources.length === 1, "logs did not open its initial source");
logs.dispatch("pageshow", {persisted: true});
assert(logs.sources.length === 1, "pageshow duplicated an open logs source");
logs.dispatch("pagehide", {persisted: true});
assert(logs.sources[0].closed, "pagehide did not close the bfcache logs source");
logs.dispatch("pageshow", {persisted: true});
assert(logs.sources.length === 2, "pageshow did not restart bfcache logs");

logs.toggle.listeners.click();
assert(logs.toggle.dataset.livePaused === "true", "logs pause state was not set");
assert(logs.streamStatus.textContent === "Paused", "logs did not report Paused");
logs.dispatch("pagehide", {persisted: true});
logs.dispatch("pageshow", {persisted: true});
assert(logs.sources.length === 2, "paused logs restarted on pageshow");
assert(logs.streamStatus.textContent === "Paused", "paused logs status changed");

const otherLivePage = makePage({withToggle: false});
assert(otherLivePage.sources.length === 1, "other live page did not open");
otherLivePage.dispatch("pagehide", {persisted: true});
otherLivePage.dispatch("pageshow", {persisted: true});
assert(otherLivePage.sources.length === 2, "other live page did not restart");
otherLivePage.dispatch("pageshow", {persisted: true});
assert(otherLivePage.sources.length === 2, "other live page duplicated its source");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_home_prefills_gemini_flash_for_each_stage(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert '<option value="gemini" selected>Google Gemini</option>' in response.text
    assert response.text.count(
        'value="gemini/gemini-3.5-flash" data-model-provider="gemini" selected'
    ) == 3


def test_home_explains_each_pipeline_model_role(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert 'aria-label="About Stage 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 2 model"' in response.text
    assert "transcribes the selected dictionary pages into faithful flat text" in response.text
    assert "infers the dictionary-specific MDF parsing guide" in response.text
    assert "applies the approved MDF parsing guide" in response.text


def test_health_endpoint_is_small_and_versioned(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 1}


def test_untrusted_host_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0:8000",
        "host.docker.internal:8000",
        "docker.for.mac.localhost:8000",
    ],
)
def test_container_mode_accepts_docker_loopback_hosts(
    tmp_path: Path,
    host: str,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path, container_mode=True))

    response = client.get("/", headers={"host": host})

    assert response.status_code == 200


def test_container_mode_still_rejects_untrusted_hosts(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path, container_mode=True))

    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


def test_static_assets_are_served_locally(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--color-accent" in response.text
    assert "[hidden]" in response.text
    assert "display: none !important" in response.text
    assert ".profile-layout-question" in response.text
    assert "grid-template-columns: minmax(0, 1fr)" in response.text
    assert ".profile-layout-question textarea" in response.text
    assert ".profile-other-information" in response.text
    assert ".profile-other-information textarea" in response.text
    css_rules = [line.strip() for line in response.text.splitlines()]
    assert any(line.startswith(".rules-editor .editor-row button {") for line in css_rules)
    assert not any(line.startswith(".editor-row button {") for line in css_rules)
    assert ".preset-loader {" in response.text
    assert "margin-bottom: 24px" in response.text
    assert "padding: 20px 24px" in response.text
    assert ".preset-loader > label" in response.text
    assert ".preset-loader .primary" in response.text
    assert ".review-layout" in response.text
    assert ".review-groups" in response.text
    assert ".review-actions" in response.text
    assert ".credential-blocked" in response.text


def test_new_run_form_previews_typed_configuration(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert "Review your run" in response.text
    assert "Human approval required" in response.text
    assert "anthropic/claude-sonnet-4-6" in response.text


@pytest.mark.parametrize(
    "submitted_output",
    [
        "outputs/web-output",
        "/Users/example/project/outputs/web-output",
    ],
)
def test_container_dashboard_rebases_project_outputs_to_the_host_mount(
    tmp_path: Path,
    submitted_output: str,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", container_mode=True)
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": submitted_output,
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.output.directory == Path("/app/outputs/web-output")


def test_container_dashboard_rejects_unmounted_host_output_path(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(data_dir=tmp_path / "app-data", container_mode=True)
    )

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": "/Users/example/Desktop/custom-output",
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert 'data-field-error="output_directory"' in response.text
    assert "Use outputs/ or /data/ for Docker output files" in response.text


def test_container_dashboard_defaults_to_the_host_outputs_mount(
    tmp_path: Path,
) -> None:
    response = TestClient(
        create_app(data_dir=tmp_path / "app-data", container_mode=True)
    ).get("/")

    assert response.status_code == 200
    assert 'name="output_directory" value="outputs"' in response.text


def test_new_run_saves_selected_dashboard_credential_immediately(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)
    expected_value = "dashboard-dummy-provider-value"

    response = client.post(
        "/credentials/gemini",
        data={"api_key": expected_value},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "provider": "gemini"}
    resolved = app.state.credential_vault.resolve(Provider.GEMINI)
    assert resolved is not None
    assert resolved.get_secret_value() == expected_value
    assert app.state.run_store.list_runs() == []
    assert expected_value not in response.text


def test_preview_error_identifies_the_invalid_field(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "temperature": "-1",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert "Temperature" in response.text
    assert "greater than or equal to 0" in response.text
    assert "Submitted values are not echoed" not in response.text


def test_preview_ignores_retired_controls_from_a_stale_browser_tab(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "page_limit": "12",
            "media_reference": "inline",
            "prompt_cache": "off",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert "Review your run" in response.text


def test_new_run_accepts_provider_aware_stage_models_without_legacy_model(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "transcription",
            "provider": "openrouter",
            "openrouter_provider": "anthropic",
            "stage1_model": "openrouter/anthropic/claude-sonnet-5",
            "temperature": "0.1",
            "reasoning": "none",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.models.stage1 == "openrouter/anthropic/claude-sonnet-5"
    assert config.models.stage1_reasoning == "low"
    assert config.models.openrouter_provider == "anthropic"


def test_new_run_collects_optional_dictionary_profile_questions(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "profile_headword_language": "Chukchi",
            "profile_headword_script": "Cyrillic",
            "profile_target_languages": ["Russian", "English"],
            "profile_target_scripts": ["Cyrillic", "Latin"],
            "profile_page_layout": "There are two columns with independent entries.",
            "profile_information_types": ["translation", "other"],
            "profile_other_information_types": "dialect labels, semantic domains",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.input.dictionary_profile is not None
    assert config.input.dictionary_profile.headword.language == "Chukchi"
    assert [target.script for target in config.input.dictionary_profile.targets] == [
        "Cyrillic",
        "Latin",
    ]
    assert config.pipeline.stage1_typography is False
    assert (
        config.input.dictionary_profile.other_information_types
        == "dialect labels, semantic domains"
    )


def test_new_run_form_renders_validation_errors_without_echoing_secret(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/runs/preview",
        data={
            "pages": str(tmp_path / "missing"),
            "output_directory": str(tmp_path / "output"),
            "pipeline": "2-pass-2",
            "provider": "anthropic",
            "model": "sk-do-not-render",
            "reasoning": "low",
        },
    )

    assert response.status_code == 422
    assert "Check the highlighted configuration" in response.text
    assert "sk-do-not-render" not in response.text


def test_empty_pydantic_error_still_has_a_user_facing_validation_issue() -> None:
    error = ValidationError.from_exception_data("configuration", [])

    assert _validation_errors(error) == [
        {
            "key": "configuration",
            "field": "Configuration",
            "message": "The submitted configuration could not be validated",
        }
    ]


def test_separate_provider_page_is_removed(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/providers")

    assert response.status_code == 404


def test_provider_key_is_encrypted_revealable_and_persistent(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/credentials/anthropic",
        data={"api_key": "sk-ant-browser-secret"},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "provider": "anthropic"}
    assert "sk-ant-browser-secret" not in response.text
    assert b"sk-ant-browser-secret" not in (tmp_path / "mudidi-web.sqlite3").read_bytes()

    restarted = TestClient(create_app(data_dir=tmp_path))
    home_page = restarted.get("/")
    revealed = restarted.post("/credentials/anthropic/reveal")

    assert home_page.status_code == 200
    assert "1 provider key saved" in home_page.text
    assert "Saved key — leave blank to keep it" in home_page.text
    assert "sk-ant-browser-secret" not in home_page.text
    assert 'data-delete-key data-provider="anthropic"' in home_page.text
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "no-store"
    assert revealed.json() == {"api_key": "sk-ant-browser-secret"}

    deleted = restarted.post("/credentials/anthropic/delete")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "provider": "anthropic"}
    assert restarted.post("/credentials/anthropic/reveal").status_code == 404

def test_environment_credential_has_no_destructive_action(tmp_path: Path) -> None:
    vault = CredentialVault(
        environ={"ANTHROPIC_API_KEY": "env-only-secret"},
        persistent_store=PersistentCredentialStore(
            database_path=tmp_path / "mudidi-web.sqlite3",
            key_path=tmp_path / ".credential-key",
        ),
    )
    response = TestClient(
        create_app(data_dir=tmp_path, credential_vault=vault)
    ).get("/")

    assert response.status_code == 200
    assert "env-only-secret" not in response.text
    assert 'data-delete-key data-provider="anthropic"' not in response.text


def test_invalid_provider_key_submission_is_not_reflected(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/credentials/openai",
        data={"api_key": "   "},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "API key cannot be empty"}


def test_new_run_accepts_uploaded_dictionary_pdf_into_managed_input(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "dictionary_pages": "1-2",
        },
        files={
            "dictionary_pdf": (
                "dictionary.pdf",
                _pdf_bytes(2),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200
    runs = app.state.run_store.list_runs()
    assert len(runs) == 1
    config = app.state.job_controller.load_inference_config(runs[0].run_id)
    assert config.input.pages is not None
    assert (
        config.input.pages.parent.parent
        == (tmp_path / "app-data" / "runs" / runs[0].run_id / "inputs").resolve()
    )
    assert config.input.pages.name == "dictionary.pdf"
    assert config.input.pages.is_file()


def test_upload_rejects_unsafe_filename_without_creating_run(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "app-data")
    client = TestClient(app)

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "transcription",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={
            "dictionary_pdf": ("../escape.pdf", _pdf_bytes(), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert app.state.run_store.list_runs() == []
    assert not (tmp_path / "app-data" / "escape.png").exists()

def test_new_run_wizard_exposes_ordered_named_panels_and_non_color_states(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    panel_positions = [
        response.text.index(f'data-wizard-panel="{step}"')
        for step in ("input", "pipeline", "model", "agentic")
    ]
    assert panel_positions == sorted(panel_positions)
    for step in ("input", "pipeline", "model", "agentic"):
        assert (
            f'<section id="wizard-{step}" data-wizard-panel="{step}" '
            f'aria-labelledby="wizard-{step}-title"'
        ) in response.text
        assert f'id="wizard-{step}-title"' in response.text
    assert response.text.count('aria-current="step"') == 1
    assert 'data-stage2-mode="shared"' in response.text
    assert 'data-stage2-pass="pass1"' in response.text
    assert 'data-stage2-pass="pass2"' in response.text
    assert 'data-wizard-validation-summary role="alert"' in response.text
