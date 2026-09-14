"""Integration tests for the local web application shell."""

from __future__ import annotations

import re
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
    assert '<section class="workspace" data-new-run-wizard>' in response.text
    assert '<form class="panel run-form" data-new-run-wizard' not in response.text
    for step in ("input", "pipeline", "model", "agentic"):
        assert f'id="wizard-{step}"' in response.text
        assert f'data-wizard-panel="{step}"' in response.text
        assert f'data-wizard-go="{step}"' in response.text
    assert response.text.count('aria-current="step"') == 1
    assert 'data-wizard-marker="input" aria-current="step"' in response.text
    assert response.text.count("data-wizard-go=") == 4
    assert 'data-wizard-marker="review" aria-disabled="true"' in response.text
    assert 'action="/runs/preview"' in response.text
    assert "data-wizard-submit" in response.text
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
    assert "data-dictionary-dropzone" in response.text
    assert (
        '<label class="dictionary-upload-trigger" data-dictionary-file-trigger>'
        in response.text
    )
    assert '<span class="sr-only">Choose dictionary PDF</span>' in response.text
    assert 'class="dictionary-file-input"' in response.text
    assert 'data-dictionary-file-status aria-live="polite"' in response.text
    assert 'class="primary mdf-guide-upload-trigger"' in response.text
    assert "data-mdf-guide-file-input" in response.text
    assert 'data-mdf-guide-file-status aria-live="polite"' in response.text
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
    assert 'name="provider"' not in response.text
    assert response.text.count('name="stage1_provider"') == 1
    assert response.text.count('name="stage2_provider"') == 1
    assert "data-provider-value" not in response.text
    assert response.text.count("data-provider-choice") >= 2
    assert 'data-stage2-mode="shared"' in response.text
    assert "data-stage2-toggle" in response.text
    assert "Advanced · split passes" in response.text
    assert 'data-stage2-pass="pass1"' in response.text
    assert 'data-stage2-pass="pass2"' in response.text
    assert (
        '<p class="stage2-explanation" data-stage2-explanation hidden>' in response.text
    )
    assert 'class="stage1-settings"' in response.text
    assert (
        '<div class="stage-settings-heading"><div><span class="eyebrow">Stage 1</span>'
        "<h3>Model and reasoning</h3></div></div>" in response.text
    )
    assert 'class="credential-grid"' in response.text
    assert response.text.count("data-credential-card") == 4
    assert "data-selected-credential" not in response.text
    assert "data-other-credentials" not in response.text
    assert "Manage keys" not in response.text
    assert "◉" not in response.text
    assert 'name="stage1_reasoning"' in response.text
    assert 'name="stage2_pass1_reasoning"' in response.text
    assert 'name="stage2_pass2_reasoning"' in response.text
    assert 'name="reasoning"' not in response.text
    assert 'name="stage1_custom_model"' in response.text
    assert 'name="openrouter_provider"' in response.text
    assert "data-model-provider=" not in response.text
    assert "OpenRouter Provider" in response.text
    assert "Authenticate a provider to load models" in response.text
    subscription_option = response.text.index(
        'value="subscription" checked data-auth-mode-choice'
    )
    api_key_option = response.text.index(
        'value="api_key" data-auth-mode-choice'
    )
    assert subscription_option < api_key_option
    assert "Google subscription" in response.text
    assert "Run agy" not in response.text
    assert 'data-pipeline-stages="stage1"' in response.text
    for server_catalog_model in (
        "GPT-5.6 Sol",
        "Claude Fable 5",
    ):
        assert server_catalog_model not in response.text
    assert "Other / advanced provider" in response.text
    assert "Stage and model overrides" not in response.text
    assert 'name="verify_stage1"' in response.text
    assert 'name="verify_stage2"' in response.text
    assert (
        'name="verify_stage1" type="checkbox" value="true" checked disabled'
        in response.text
    )
    assert (
        'name="verify_stage2" type="checkbox" value="true" checked disabled'
        in response.text
    )
    assert 'name="agentic" value="false" checked' in response.text
    assert 'name="agentic" value="true"' in response.text
    assert (
        '<fieldset class="agentic-settings" data-agentic-settings hidden>'
        in response.text
    )
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
        'name="evaluator_reasoning" data-reasoning-select disabled>'
        '<option value="">Use default</option><option value="minimal">Minimal</option>'
        '<option value="low">Low</option><option value="medium">Medium</option>'
        '<option value="high" selected>High</option>'
        '<option value="xhigh">Extra high</option><option value="max">Max</option>'
        in response.text
    )
    assert (
        'name="rewriter_reasoning" data-reasoning-select disabled>'
        '<option value="">Use default</option><option value="minimal">Minimal</option>'
        '<option value="low" selected>Low</option>' in response.text
    )
    assert "data-wizard-submit" in response.text
    assert "Review run" in response.text
    assert 'name="page_limit"' not in response.text
    assert 'name="media_reference"' not in response.text
    assert 'name="prompt_cache"' not in response.text
    assert 'name="temperature"' not in response.text
    assert 'aria-label="About temperature"' not in response.text
    assert response.text.index('name="batch_size"') < response.text.index(
        'data-wizard-panel="agentic"'
    )
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
        '<input type="radio" name="output_policy" value="overwrite">' in response.text
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
    assert response.text.count('class="primary profile-remove"') == 2
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
    assert "/static/app.js?v=dashboard-ui-16" in response.text
    assert "Start offline demo" not in response.text
    assert 'action="/runs/demo"' not in response.text


def test_agentic_pipeline_sync_keeps_off_controls_out_of_form_data_and_restores_on() -> (
    None
):
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
  replaceChildren() {
    this.options = [];
    this.children = [];
    this.selectedOptions = [];
  }
  append(child) {
    this.children ||= [];
    this.children.push(child);
    if (child.value !== undefined) this.options.push(child);
  }
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
const modelOptions = (provider = "gemini") => [
  modelOption(""),
  modelOption(`${provider}/catalog-model`, provider),
  modelOption("__other__", "other"),
];
const primeCatalog = (select, provider) => {
  select.options = modelOptions(provider).map(
    (option) => ({...option, dataset: {...option.dataset}}),
  );
  select.dataset.catalogProvider = provider;
  select.value = "";
};
[evaluatorModel, rewriterModel].forEach((select) => primeCatalog(select, "gemini"));
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
const makeAgenticGroup = (provider, model, custom) => {
  model.parentElement = {
    querySelector(selector) {
      return selector.includes("data-agentic-custom-model") ? custom : null;
    },
  };
  return {
    querySelector(selector) {
      if (selector === "[data-agentic-provider]") return provider;
      if (selector === "[data-agentic-model]") return model;
      if (selector === "[data-agentic-custom-model]") return custom;
      return null;
    },
    closest(selector) {
      return selector === "[data-agentic-settings]" ? agenticSettings : null;
    },
  };
};
const agenticGroups = [
  makeAgenticGroup(evaluatorProvider, evaluatorModel, evaluatorCustom),
  makeAgenticGroup(rewriterProvider, rewriterModel, rewriterCustom),
];
const credentialCards = ["gemini", "openai", "anthropic", "openrouter"].map((provider) => {
  const card = new Field();
  card.dataset.provider = provider;
  card.dataset.keyAvailable = "true";
  return card;
});

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
    if (selector === "[data-credential-card]") return credentialCards;
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
primeCatalog(evaluatorModel, "gemini");
sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
evaluatorModel.value = "__other__";
sync.synchronizeAgenticModelGroup(evaluatorGroup);
evaluatorCustom.value = "stale/evaluator-model";
sync.synchronizeAgenticModelGroup(evaluatorGroup);
if (evaluatorCustom.hidden || evaluatorCustom.disabled) {
  throw new Error("Agentic custom evaluator was not enabled for its selected model");
}

primeCatalog(evaluatorModel, "openai");
evaluatorProvider.value = "openai";
sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
primeCatalog(evaluatorModel, "gemini");
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
  if (provider !== "custom") primeCatalog(evaluatorModel, provider);
  sync.synchronizeAgenticModelGroup(evaluatorGroup, true);
  if (provider === "openrouter") {
    if (evaluatorModel.hidden || evaluatorModel.disabled) {
      throw new Error("OpenRouter did not keep its discovered model selector active");
    }
    if (!evaluatorCustom.hidden || !evaluatorCustom.disabled) {
      throw new Error("OpenRouter enabled custom entry before Other was selected");
    }
    evaluatorModel.value = "__other__";
    sync.synchronizeAgenticModelGroup(evaluatorGroup);
  } else if (!evaluatorModel.hidden || !evaluatorModel.disabled) {
    throw new Error(`${provider} did not hide and disable its model selector`);
  }
  evaluatorCustom.value = value;
  sync.synchronizeAgenticModelGroup(evaluatorGroup);
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
  throw new Error("Pipeline synchronization disabled a selected OpenRouter Other model");
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


def test_instruction_source_panel_sync_handles_switching_confirmation_and_kept_presets() -> (
    None
):
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Node {
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.required = false;
    this.value = "";
    this.textContent = "";
    this.focused = false;
    this.listeners = {};
    this._files = [];
  }
  get files() { return this._files; }
  set files(list) { this._files = list; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  focus() { this.focused = true; }
}

class FileInputNode extends Node {
  set value(next) {
    if (next === "") this._files = [];
    this._value = next;
  }
  get value() { return this._files[0]?.name || ""; }
}

class Radio extends Node {
  constructor(value, checked) {
    super();
    this.value = value;
    this.checked = checked;
  }
}

class InstructionPanel extends Node {
  constructor({ hasPreset = false, presetKind = "" } = {}) {
    super();
    this.dataset.instructionHasPreset = hasPreset ? "true" : "false";
    if (presetKind) this.dataset.instructionPresetKind = presetKind;
    this.sourceRadios = [new Radio("typed", true), new Radio("file", false)];
    this.typedPanel = new Node();
    this.textarea = new Node();
    this.filePanel = new Node();
    this.keptFileContainer = new Node();
    this.keptRadios = hasPreset ? [new Radio("keep", true), new Radio("replace", false)] : [];
    this.uploadRow = new Node();
    this.fileInput = new FileInputNode();
    this.fileStatus = new Node();
    this.fileStatus.dataset.emptyLabel = "No file selected";
    this.keepExisting = new Node();
    this.pdfPagesField = new Node();
    this.pdfPagesInput = new Node();
    this.pdfWarning = new Node();
  }
  querySelectorAll(selector) {
    if (selector === "[data-instruction-source-radio]") return this.sourceRadios;
    if (selector === "[data-instruction-kept-radio]") return this.keptRadios;
    return [];
  }
  querySelector(selector) {
    switch (selector) {
      case "[data-instruction-typed-panel]": return this.typedPanel;
      case "[data-instruction-typed-panel] textarea": return this.textarea;
      case "[data-instruction-file-panel]": return this.filePanel;
      case "[data-instruction-kept-file]": return this.keptFileContainer;
      case "[data-instruction-upload-row]": return this.uploadRow;
      case "[data-instruction-file-input]": return this.fileInput;
      case "[data-instruction-file-status]": return this.fileStatus;
      case "[data-instruction-keep-existing]": return this.keepExisting;
      case "[data-instruction-pdf-pages]": return this.pdfPagesField;
      case "[data-instruction-pdf-pages] input": return this.pdfPagesInput;
      case "[data-instruction-pdf-warning]": return this.pdfWarning;
      default: return null;
    }
  }
}

const freshPanel = new InstructionPanel();
const keptPdfPanel = new InstructionPanel({ hasPreset: true, presetKind: "pdf" });
const document = {
  body: { append() {} },
  addEventListener() {},
  createElement() { return new Node(); },
  querySelector(selector) {
    if (selector === ".form-error-summary") return null;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-instruction-source-panel]") return [freshPanel, keptPdfPanel];
    return [];
  },
};
let confirmResult = true;
let confirmCalls = 0;
const window = {
  confirm: () => { confirmCalls += 1; return confirmResult; },
  fetch: async () => { throw new Error("not used"); },
  location: { origin: "http://test" },
  addEventListener() {},
  sessionStorage: { getItem: () => null, setItem() {} },
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
    synchronizeInstructionPanel,
    synchronizeInstructionPanels,
    wireInstructionPanel,
  };`,
  context,
);
const sync = context.__dashboardTest;
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const selectRadio = (panel, value) => {
  panel.sourceRadios.forEach((radio) => { radio.checked = radio.value === value; });
  // Real radios fire "input" (which bubbles to a generic resync via persistRunForm)
  // before "change" fires on the target. Reproduce that ordering here so a
  // regression that lets the generic resync clobber the previous-mode marker
  // before the dedicated change handler reads it is caught.
  sync.synchronizeInstructionPanels();
  panel.sourceRadios.find((radio) => radio.value === value).listeners.change();
};
const selectKeptRadio = (panel, value) => {
  panel.keptRadios.forEach((radio) => { radio.checked = radio.value === value; });
  panel.keptRadios.find((radio) => radio.value === value).listeners.change();
};

sync.wireInstructionPanel(freshPanel);
sync.wireInstructionPanel(keptPdfPanel);

assert(!freshPanel.typedPanel.hidden, "typed panel starts visible");
assert(!freshPanel.textarea.disabled, "typed textarea starts enabled");
assert(freshPanel.filePanel.hidden, "file panel starts hidden");
assert(freshPanel.fileInput.disabled, "file input starts disabled");

selectRadio(freshPanel, "file");
assert(confirmCalls === 0, "switching away from an empty typed field must not confirm");
assert(freshPanel.typedPanel.hidden, "typed panel hides once file mode is active");
assert(freshPanel.textarea.disabled, "typed textarea disables once file mode is active");
assert(!freshPanel.filePanel.hidden, "file panel becomes visible in file mode");
assert(!freshPanel.fileInput.disabled, "file input enables in file mode");
assert(freshPanel.fileInput.required, "file input is required without a kept preset");
assert(freshPanel.fileInput.focused, "switching to file mode focuses the file input");
assert(freshPanel.fileStatus.textContent === "No file selected", "empty file mode shows the empty label");
assert(freshPanel.keptFileContainer.hidden, "no kept preset means the kept-file block stays hidden");
assert(!freshPanel.uploadRow.hidden, "no kept preset means the upload row is visible in file mode");

freshPanel.fileInput.files = [{ name: "guide.pdf" }];
freshPanel.fileInput.listeners.change();
assert(!freshPanel.pdfPagesField.hidden, "PDF selection reveals the pages field");
assert(!freshPanel.pdfPagesInput.disabled, "PDF selection enables the pages input");
assert(!freshPanel.pdfWarning.hidden, "PDF selection reveals the persistent cost warning");
assert(freshPanel.fileStatus.textContent === "Selected: guide.pdf", "filename status reflects the selected file");

freshPanel.fileInput.files = [{ name: "guide.txt" }];
freshPanel.fileInput.listeners.change();
assert(freshPanel.pdfPagesField.hidden, "TXT selection hides the pages field");
assert(freshPanel.pdfPagesInput.disabled, "TXT selection disables the pages input");
assert(freshPanel.pdfWarning.hidden, "TXT selection hides the persistent cost warning");

confirmResult = false;
selectRadio(freshPanel, "typed");
assert(confirmCalls === 1, "switching away from a non-empty upload must confirm");
assert(freshPanel.sourceRadios[1].checked, "cancelling the confirmation keeps file mode selected");
assert(!freshPanel.sourceRadios[0].checked, "cancelling the confirmation does not select typed mode");
assert(freshPanel.fileInput.files.length === 1, "cancelling the confirmation preserves the uploaded file");
assert(!freshPanel.filePanel.hidden, "cancelling the confirmation keeps the file panel visible");

confirmResult = true;
confirmCalls = 0;
selectRadio(freshPanel, "typed");
assert(confirmCalls === 1, "switching away from a non-empty upload must confirm");
assert(freshPanel.sourceRadios[0].checked, "accepting the confirmation selects typed mode");
assert(freshPanel.fileInput.files.length === 0, "accepting the confirmation clears the uploaded file");
assert(freshPanel.typedPanel.hidden === false, "accepting the confirmation reveals the typed panel");
assert(freshPanel.textarea.focused, "accepting the confirmation focuses the typed textarea");

freshPanel.textarea.value = "Mark uncertain characters.";
confirmCalls = 0;
confirmResult = false;
selectRadio(freshPanel, "file");
assert(confirmCalls === 1, "switching away from non-empty typed text must confirm");
assert(freshPanel.sourceRadios[0].checked, "cancelling keeps typed mode selected");
assert(freshPanel.textarea.value === "Mark uncertain characters.", "cancelling preserves typed text");

confirmResult = true;
confirmCalls = 0;
selectRadio(freshPanel, "file");
assert(confirmCalls === 1, "switching away from non-empty typed text must confirm");
assert(freshPanel.textarea.value === "", "accepting the confirmation clears typed text");
assert(freshPanel.fileInput.required, "file input remains required after re-entering file mode");

selectRadio(keptPdfPanel, "file");
assert(!keptPdfPanel.keptFileContainer.hidden, "a kept preset shows the explicit Keep/Replace block");
assert(keptPdfPanel.keptRadios[0].checked, "Keep is the default selected action for a kept preset");
assert(keptPdfPanel.uploadRow.hidden, "keeping the saved file hides the upload row");
assert(keptPdfPanel.fileInput.disabled, "keeping the saved file disables the file input");
assert(!keptPdfPanel.fileInput.required, "keeping the saved file means a fresh file is not required");
assert(keptPdfPanel.keepExisting.value === "true", "keep-existing reports true while Keep is selected");
assert(!keptPdfPanel.pdfPagesField.hidden, "a kept PDF preset shows the pages field while keeping");
assert(!keptPdfPanel.pdfWarning.hidden, "a kept PDF preset shows the persistent cost warning while keeping");

selectKeptRadio(keptPdfPanel, "replace");
assert(!keptPdfPanel.uploadRow.hidden, "choosing Replace reveals the upload row");
assert(!keptPdfPanel.fileInput.disabled, "choosing Replace enables the file input");
assert(keptPdfPanel.fileInput.required, "choosing Replace requires a fresh file");
assert(keptPdfPanel.fileInput.focused, "choosing Replace focuses the file input");
assert(keptPdfPanel.keepExisting.value === "false", "choosing Replace clears keep-existing");
assert(keptPdfPanel.pdfPagesField.hidden, "choosing Replace hides the pages field until a new PDF is chosen");

keptPdfPanel.fileInput.files = [{ name: "replacement.txt" }];
keptPdfPanel.fileInput.listeners.change();
assert(keptPdfPanel.keepExisting.value === "false", "a chosen replacement keeps keep-existing false");
assert(keptPdfPanel.pdfPagesField.hidden, "a TXT replacement hides the pages field even with a PDF preset");

selectKeptRadio(keptPdfPanel, "keep");
assert(keptPdfPanel.fileInput.files.length === 0, "returning to Keep clears any chosen replacement file");
assert(keptPdfPanel.uploadRow.hidden, "returning to Keep hides the upload row again");
assert(keptPdfPanel.keepExisting.value === "true", "returning to Keep restores keep-existing true");
assert(!keptPdfPanel.pdfPagesField.hidden, "returning to Keep restores the saved PDF pages disclosure");

keptPdfPanel.hidden = true;
keptPdfPanel.fileInput.disabled = true;
keptPdfPanel.fileInput.required = true;
sync.synchronizeInstructionPanel(keptPdfPanel);
assert(keptPdfPanel.fileInput.disabled, "a hidden panel excluded by the pipeline stays disabled");
assert(keptPdfPanel.fileInput.required, "a hidden panel excluded by the pipeline is not re-enabled by our own sync");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_instruction_panel_restore_prefers_submitted_replace_state_over_stale_preset_on_validation_recovery() -> (
    None
):
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Field {
  constructor({ name = "", value = "", type = "text", checked = false } = {}) {
    this.name = name;
    this._value = value;
    this.type = type;
    this.checked = checked;
    this.disabled = false;
    this.required = false;
    this.hidden = false;
    this.dataset = {};
    this.listeners = {};
    this._files = [];
  }
  get files() { return this._files; }
  set files(list) { this._files = list; }
  get value() { return this.type === "file" ? (this._files[0]?.name || "") : this._value; }
  set value(next) {
    if (this.type === "file" && next === "") { this._files = []; return; }
    this._value = next;
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  focus() {}
}
class Fields extends Array {
  namedItem(name) { return this.find((field) => field.name === name) || null; }
}

// A run-owned PDF instruction attachment was saved to a preset with Keep
// semantics (source=file, keep_existing=true). The user then loaded that
// preset, switched Stage 1 to Replace, and submitted; the server rejected an
// unrelated field, forcing a 422 re-render. That re-render always recomputes
// preset_state fresh from the ORIGINAL saved preset (still keep=true), but
// sessionStorage holds exactly what was live in the form at submit time.
const sourceTyped = new Field({ name: "stage1_instruction_source", value: "typed", type: "radio" });
const sourceFile = new Field({ name: "stage1_instruction_source", value: "file", type: "radio", checked: true });
const textarea = new Field({ name: "stage1_additional_instructions", type: "textarea" });
const keptKeep = new Field({ name: "stage1_instruction_kept_choice", value: "keep", type: "radio" });
const keptReplace = new Field({ name: "stage1_instruction_kept_choice", value: "replace", type: "radio", checked: true });
const fileInput = new Field({ name: "stage1_instruction_file", type: "file" });
const keepExisting = new Field({ name: "stage1_instruction_keep_existing", value: "false", type: "hidden" });
const pdfPagesInput = new Field({ name: "stage1_instruction_pdf_pages", type: "text" });
const typedPanelEl = new Field({});
const filePanelEl = new Field({});
const keptFileContainer = new Field({});
const uploadRow = new Field({});
const fileStatus = new Field({});
const pdfPagesField = new Field({});
const pdfWarning = new Field({});

const panel = {
  hidden: false,
  dataset: { instructionHasPreset: "true", instructionPresetKind: "pdf" },
  querySelectorAll(selector) {
    if (selector === "[data-instruction-source-radio]") return [sourceTyped, sourceFile];
    if (selector === "[data-instruction-kept-radio]") return [keptKeep, keptReplace];
    return [];
  },
  querySelector(selector) {
    switch (selector) {
      case "[data-instruction-typed-panel]": return typedPanelEl;
      case "[data-instruction-typed-panel] textarea": return textarea;
      case "[data-instruction-file-panel]": return filePanelEl;
      case "[data-instruction-kept-file]": return keptFileContainer;
      case "[data-instruction-upload-row]": return uploadRow;
      case "[data-instruction-file-input]": return fileInput;
      case "[data-instruction-file-status]": return fileStatus;
      case "[data-instruction-keep-existing]": return keepExisting;
      case "[data-instruction-pdf-pages]": return pdfPagesField;
      case "[data-instruction-pdf-pages] input": return pdfPagesInput;
      case "[data-instruction-pdf-warning]": return pdfWarning;
      default: return null;
    }
  },
};

const runForm = {
  elements: new Fields(sourceTyped, sourceFile, textarea, keptKeep, keptReplace, fileInput, keepExisting, pdfPagesInput),
  addEventListener() {},
  querySelectorAll() { return []; },
};

// The stale server-rendered preset_state script tag: still reports the
// ORIGINAL saved-preset semantics (Keep, keep_existing=true).
const presetStateElement = {
  textContent: JSON.stringify({
    stage1_instruction_source: ["file"],
    stage1_instruction_keep_existing: ["true"],
    stage1_instruction_kept_choice: ["keep"],
  }),
};
// sessionStorage: exactly what was live in the form at submit time (Replace,
// keep_existing=false), captured by the "submit" listener's persistRunForm().
const sessionStorageState = JSON.stringify({
  stage1_instruction_source: ["file"],
  stage1_instruction_keep_existing: ["false"],
  stage1_instruction_kept_choice: ["replace"],
});
const errorSummary = { textContent: "1 issue prevented this run from being prepared." };

const formErrorField = new Field({});
const document = {
  body: { append() {} },
  addEventListener() {},
  createElement() { return new Field({}); },
  querySelector(selector) {
    if (selector === "form.run-form") return runForm;
    if (selector === "#preset-form-state") return presetStateElement;
    if (selector === ".form-error-summary") return errorSummary;
    if (selector === "[data-field-error]") return formErrorField;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-instruction-source-panel]") return [panel];
    return [];
  },
};
const window = {
  confirm: () => true,
  fetch: async () => { throw new Error("not used"); },
  location: { origin: "http://test" },
  addEventListener() {},
  sessionStorage: { getItem: () => sessionStorageState, setItem() {} },
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
// restoreRunForm() and wireInstructionPanel() run automatically as the
// script's own top-level statements, exactly as they do on a real page load.
vm.runInContext(source, context);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(sourceFile.checked, "recovery must keep Stage 1 in file mode");
assert(keptReplace.checked, "recovery must restore the submitted Replace choice, not fall back to Keep");
assert(!keptKeep.checked, "recovery must not silently revert to Keep");
assert(keepExisting.value === "false", "recovery must not silently keep the old preset attachment");
assert(!uploadRow.hidden, "recovery in Replace mode must show the upload row for reselection");
assert(!fileInput.disabled, "recovery in Replace mode must enable the file input");
assert(fileInput.required, "recovery in Replace mode must require a freshly reselected file");
assert(fileInput.files.length === 0, "browsers cannot restore a previously chosen file after navigation");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_stage2_summary_tracks_transcription_and_shared_split_modes() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Field {
  constructor({name = "", value = "", checked = false} = {}) {
    this.name = name;
    this.value = value;
    this.checked = checked;
    this.disabled = false;
    this.hidden = false;
    this.type = "text";
    this.dataset = {};
    this.options = [];
    this.selectedOptions = [];
    this.parentElement = this;
    this.classList = {add() {}, remove() {}, toggle() {}};
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

const complete = new Field({name: "pipeline", value: "complete", checked: true});
const transcription = new Field({name: "pipeline", value: "transcription"});
const structure = new Field({name: "pipeline", value: "structure"});
const pass1Model = new Field({name: "stage2_pass1_model", value: "shared-model"});
const pass1Custom = new Field({name: "stage2_pass1_custom_model"});
const pass1Reasoning = new Field({name: "stage2_pass1_reasoning", value: "low"});
const pass2Model = new Field({name: "stage2_pass2_model", value: "shared-model"});
const pass2Custom = new Field({name: "stage2_pass2_custom_model"});
const pass2Reasoning = new Field({name: "stage2_pass2_reasoning", value: "low"});
const runForm = {
  elements: new Fields(
    complete,
    transcription,
    structure,
    pass1Model,
    pass1Custom,
    pass1Reasoning,
    pass2Model,
    pass2Custom,
    pass2Reasoning,
  ),
  addEventListener() {},
  querySelectorAll() { return []; },
};
const summary = new Field();
summary.textContent = "";
const explanation = new Field();
explanation.hidden = false;
const document = {
  body: {append() {}},
  addEventListener() {},
  createElement() { return new Field(); },
  querySelector(selector) {
    if (selector === "form.run-form") return runForm;
    if (selector === "[data-stage2-summary-model]") return summary;
    if (selector === "[data-stage2-explanation]") return explanation;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === 'input[name="pipeline"]') {
      return [complete, transcription, structure];
    }
    return [];
  },
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
  `${source}\nglobalThis.__dashboardTest = {
    enterSharedStage2,
    enterSplitStage2,
    synchronizePipeline,
    synchronizeStage2Pass,
  };`,
  context,
);
const sync = context.__dashboardTest;
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

sync.synchronizePipeline();
assert(summary.textContent === "Shared model · shared-model", "complete should show shared Stage 2");
assert(explanation.hidden, "shared mode should hide the pass explanation");

complete.checked = false;
transcription.checked = true;
sync.synchronizePipeline();
assert(summary.textContent === "Not used", "transcription-only should hide Stage 2 summary");

transcription.checked = false;
structure.checked = true;
sync.synchronizePipeline();
assert(summary.textContent === "Shared model · shared-model", "structure should restore Stage 2 summary");

sync.enterSplitStage2();
assert(
  summary.textContent === "Separate pass models · Pass 1: shared-model · Pass 2: shared-model",
  "split mode should show both pass models",
);
assert(!explanation.hidden, "split mode should show the pass explanation");
pass1Model.value = "pass-one-model";
pass2Model.value = "pass-two-model";
sync.synchronizeStage2Pass("pass1");
sync.synchronizeStage2Pass("pass2");
assert(
  summary.textContent === "Separate pass models · Pass 1: pass-one-model · Pass 2: pass-two-model",
  "split mode should update independent pass models",
);
sync.enterSharedStage2();
assert(summary.textContent === "Shared model · shared-model", "shared mode should restore shared model");
assert(explanation.hidden, "returning to shared mode should hide the pass explanation");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_credential_delete_browser_state_uses_effective_fallback() -> None:
    app_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/app.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
    this.value = "";
    this.placeholder = "";
    this.type = "password";
    this.disabled = false;
    this.isConnected = true;
  }
  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }
  querySelector(selector) {
    if (selector === "[data-delete-key]") return this.deleteButton?.isConnected ? this.deleteButton : null;
    if (selector === "[data-reveal-key]") return this.revealButton;
    if (selector === "label") return this.label;
    if (selector.startsWith("#credential-")) return this.input;
    return null;
  }
  querySelectorAll() { return []; }
  closest(selector) {
    if (selector === "[data-credential-card]") return this.card;
    if (selector === "[data-delete-key]" || selector === "button") return this;
    return null;
  }
  append(child) {
    this.deleteButton = child;
    child.isConnected = true;
  }
  remove() {
    this.isConnected = false;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  hasAttribute() { return false; }
}

const providerValue = new Element();
providerValue.value = "anthropic";
const card = new Element();
card.dataset.provider = "anthropic";
card.dataset.keySaved = "true";
card.dataset.keyAvailable = "true";
card.dataset.keySource = "persistent";
const label = new Element();
const input = new Element();
const status = new Element();
const reveal = new Element();
const deleteButton = new Element();
deleteButton.dataset.deleteKey = "";
deleteButton.dataset.provider = "anthropic";
deleteButton.card = card;
card.label = label;
card.input = input;
card.revealButton = reveal;
card.deleteButton = deleteButton;
label.card = card;
label.deleteButton = deleteButton;
card.querySelector = (selector) => {
  if (selector === "[data-delete-key]") return card.deleteButton?.isConnected ? card.deleteButton : null;
  if (selector === "[data-reveal-key]") return reveal;
  if (selector === "label") return label;
  if (selector === "#credential-anthropic") return input;
  if (selector === "#credential-status-anthropic") return status;
  return null;
};
label.append = (child) => {
  card.deleteButton = child;
  child.isConnected = true;
};
const listeners = {};
const catalogLoads = [];
const document = {
  body: {append() {}},
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  createElement() { return new Element(); },
  querySelector(selector) {
    if (selector === "[data-provider-value]") return providerValue;
    if (selector === "#credential-anthropic") return input;
    if (selector === "#credential-status-anthropic") return status;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-credential-card]") return [card];
    return [];
  },
  async dispatch(type, event) {
    await Promise.all((listeners[type] || []).map((listener) => listener(event)));
  },
};
const window = {
  confirm: () => true,
  fetch: async () => ({
    ok: true,
    async json() {
      return {
        status: "deleted",
        provider: "anthropic",
        available: true,
        source: "environment",
      };
    },
  }),
  location: {origin: "http://test"},
  addEventListener() {},
  sessionStorage: {getItem: () => null, setItem() {}},
};
const context = vm.createContext({
  loadModelCatalog: (options = {}) => catalogLoads.push(options),
  URL,
  URLSearchParams,
  console,
  document,
  queueMicrotask,
  window,
});
const source = fs.readFileSync(process.argv[1], "utf8").replace(
  "const loadModelCatalog = async",
  "const loadModelCatalogImpl = async",
);
vm.runInContext(source, context);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
(async () => {
  catalogLoads.length = 0;
  await document.dispatch("click", {
    target: deleteButton,
    preventDefault() {},
  });
  assert(card.dataset.keySaved === "false", "fallback must not remain marked saved");
  assert(card.dataset.keyAvailable === "true", "fallback availability was lost");
  assert(card.dataset.keySource === "environment", "fallback source was lost");
  assert(status.textContent === "Available from environment", "fallback status was not rendered");
  assert(input.placeholder.toLowerCase().includes("environment"), "fallback placeholder was not rendered");
  assert(!card.querySelector("[data-delete-key]"), "environment fallback must not be deletable");
  assert(catalogLoads.length === 1, "deletion did not reload the model catalog");
  assert(
    catalogLoads[0].resetProvider === "anthropic",
    "deletion did not scope selection reset to its provider",
  );
  assert(
    catalogLoads[0].preserveSelection !== false,
    "deletion disabled selection preservation for unrelated providers",
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(app_js)],
        capture_output=True,
        text=True,
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


def test_home_defaults_stage_providers_but_gates_models_until_authentication(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert (
        response.text.count('<option value="gemini" selected>Google Gemini</option>')
        >= 2
    )
    for name in ("stage1_model", "stage2_pass1_model", "stage2_pass2_model"):
        assert f'<select name="{name}"' in response.text
        assert re.search(
            rf'<select name="{name}"[^>]*disabled>',
            response.text,
        )
    assert "data-model-provider=" not in response.text


def test_home_explains_each_pipeline_model_role(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    assert 'aria-label="About Stage 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 1 model"' in response.text
    assert 'aria-label="About Stage 2 Pass 2 model"' in response.text
    assert (
        "transcribes the selected dictionary pages into faithful flat text"
        in response.text
    )
    assert "infers the dictionary-specific MDF parsing guide" in response.text
    assert "applies the approved MDF parsing guide" in response.text


def test_new_run_wizard_exposes_stage_instruction_source_panels(tmp_path: Path) -> None:
    response = TestClient(create_app(data_dir=tmp_path)).get("/")

    assert response.status_code == 200
    text = response.text
    assert text.count('class="form-field instruction-source-panel') == 2
    assert 'data-instruction-stage="stage1"' in text
    assert 'data-instruction-stage="stage2"' in text
    assert (
        'data-instruction-source-panel data-instruction-stage="stage1" data-stage-control data-pipeline-stages="stage1"'
        in text
    )
    assert (
        'data-instruction-source-panel data-instruction-stage="stage2" data-stage-control data-pipeline-stages="pass1 pass2"'
        in text
    )
    assert text.count('data-instruction-has-preset="false"') == 2
    assert text.count('name="stage1_instruction_source" value="typed" checked') == 1
    assert text.count('name="stage1_instruction_source" value="file"') == 1
    assert text.count('name="stage2_instruction_source" value="typed" checked') == 1
    assert text.count('name="stage2_instruction_source" value="file"') == 1
    assert text.count('name="stage1_instruction_file"') == 1
    assert text.count('name="stage2_instruction_file"') == 1
    assert text.count('accept=".txt,.md,.pdf"') == 2
    assert 'name="stage1_instruction_pdf_pages"' in text
    assert 'name="stage2_instruction_pdf_pages"' in text
    assert 'name="stage1_additional_instructions"' in text
    assert 'name="stage2_additional_instructions"' in text
    assert (
        '<input type="hidden" name="stage1_instruction_keep_existing" value="false" data-instruction-keep-existing disabled>'
        in text
    )
    assert (
        '<input type="hidden" name="stage2_instruction_keep_existing" value="false" data-instruction-keep-existing disabled>'
        in text
    )
    assert (
        text.count(
            "Selected PDF pages are attached to every applicable model call. Large files or "
            "\u201call pages\u201d can substantially increase token use, cost, latency, and the "
            "chance of exceeding a model context limit."
        )
        == 2
    )
    assert "Pass 1 only \u2014 parsing-guide discovery" in text
    assert "Pass 2 only \u2014 per-page MDF extraction" in text
    assert ">Both passes<" in text
    assert 'name="stage2_instruction_scope" value="both" checked' in text
    assert text.count('id="stage1-instruction-file-status"') == 1
    assert text.count('id="stage2-instruction-file-status"') == 1
    assert 'aria-live="polite"' in text


def test_new_run_wizard_shows_kept_instruction_state_for_a_loaded_preset(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "source-output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_instruction_source": "file",
            "stage2_instruction_source": "typed",
            "stage2_additional_instructions": "Use nt for usage notes.",
            "stage2_instruction_scope": "pass2",
        },
        files=[
            ("dictionary_pdf", ("dictionary.pdf", _pdf_bytes(), "application/pdf")),
            (
                "stage1_instruction_file",
                ("stage1.pdf", _pdf_bytes(2), "application/pdf"),
            ),
        ],
    )
    assert response.status_code == 200
    run_id = response.text.split('action="/runs/', 1)[1].split("/start", 1)[0]

    saved = client.post(
        f"/runs/{run_id}/presets",
        data={"name": "Instruction kept preset"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    preset = app.state.run_store.list_presets()[0]

    loaded = client.get(f"/?preset={preset.preset_id}")
    assert loaded.status_code == 200
    text = loaded.text
    assert '"temperature"' not in text
    assert (
        'data-instruction-stage="stage1" data-stage-control data-pipeline-stages="stage1" data-instruction-has-preset="true" data-instruction-preset-kind="pdf"'
        in text
    )
    assert (
        'data-instruction-stage="stage2" data-stage-control data-pipeline-stages="pass1 pass2" data-instruction-has-preset="false"'
        in text
    )
    assert (
        '<input type="hidden" name="stage1_instruction_keep_existing" value="true" data-instruction-keep-existing disabled>'
        in text
    )
    assert "data-instruction-kept-file" in text
    assert 'name="stage1_instruction_kept_choice" value="keep" checked' in text
    assert 'name="stage1_instruction_kept_choice" value="replace"' in text
    assert "<strong>Keep saved file</strong>" in text
    assert "<strong>Replace file</strong>" in text
    assert "stage1.pdf" in text
    assert "<dt>Kind</dt><dd>PDF</dd>" in text
    assert "<dt>Selected pages</dt><dd>1, 2 (2 pages)</dd>" in text


def test_instruction_file_error_from_a_final_step_submission_marks_field_error_for_wizard_routing(
    tmp_path: Path,
) -> None:
    # Simulates a user who filled every earlier wizard step correctly (Input,
    # Pipeline, Model) and only submits from the final Agentic step — the
    # rejected `stage1_instruction_file` is the sole problem, not something
    # already visible on the Input step in the browser's remembered wizard
    # position (sessionStorage would otherwise keep the wizard on "agentic").
    client = TestClient(
        create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    )

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_instruction_source": "file",
        },
        files=[("dictionary_pdf", ("dictionary.pdf", _pdf_bytes(), "application/pdf"))],
    )

    assert response.status_code == 422
    text = response.text
    assert "Upload exactly one instruction file" in text
    assert 'data-instruction-stage="stage1"' in text
    stage1_start = text.index('data-instruction-stage="stage1"')
    stage1_tag_start = text.rindex("<fieldset", 0, stage1_start)
    stage1_tag_end = text.index(">", stage1_tag_start)
    stage1_open_tag = text[stage1_tag_start:stage1_tag_end]
    assert 'data-field-error="stage1_instruction_file"' in stage1_open_tag
    assert "field-invalid" in stage1_open_tag

    stage2_start = text.index('data-instruction-stage="stage2"')
    stage2_tag_start = text.rindex("<fieldset", 0, stage2_start)
    stage2_tag_end = text.index(">", stage2_tag_start)
    stage2_open_tag = text[stage2_tag_start:stage2_tag_end]
    assert "data-field-error" not in stage2_open_tag


def test_kept_choice_client_only_field_survives_preset_keep_and_replace_submission(
    tmp_path: Path,
) -> None:
    # A real browser submits the checked `stage{N}_instruction_kept_choice`
    # radio (only disabled when Stage instructions are in typed mode), so a
    # loaded preset's Keep AND Replace submissions must both reach Review
    # without NewRunForm's `extra="forbid"` rejecting the client-only field.
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "source-output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_instruction_source": "file",
        },
        files=[
            ("dictionary_pdf", ("dictionary.pdf", _pdf_bytes(), "application/pdf")),
            (
                "stage1_instruction_file",
                ("stage1.pdf", _pdf_bytes(2), "application/pdf"),
            ),
        ],
    )
    assert response.status_code == 200
    run_id = response.text.split('action="/runs/', 1)[1].split("/start", 1)[0]
    saved = client.post(
        f"/runs/{run_id}/presets",
        data={"name": "Kept choice regression preset"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    preset = app.state.run_store.list_presets()[0]

    kept = client.post(
        "/runs/preview",
        data={
            "preset_id": preset.preset_id,
            "output_directory": str(tmp_path / "kept-output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_instruction_source": "file",
            "stage1_instruction_keep_existing": "true",
            "stage1_instruction_kept_choice": "keep",
        },
    )
    assert kept.status_code == 200
    kept_run_id = kept.text.split('action="/runs/', 1)[1].split("/start", 1)[0]
    kept_config = app.state.job_controller.load_inference_config(kept_run_id)
    assert kept_config.pipeline.stage1_guides is not None
    assert kept_config.pipeline.stage1_guides.name == "stage1.pdf"

    replaced = client.post(
        "/runs/preview",
        data={
            "preset_id": preset.preset_id,
            "output_directory": str(tmp_path / "replaced-output"),
            "pipeline": "complete",
            "dictionary_pages": "1",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "stage1_instruction_source": "file",
            "stage1_instruction_kept_choice": "replace",
        },
        files=[
            (
                "stage1_instruction_file",
                ("replacement.txt", b"replacement text", "text/plain"),
            ),
        ],
    )
    assert replaced.status_code == 200
    replaced_run_id = replaced.text.split('action="/runs/', 1)[1].split("/start", 1)[0]
    replaced_config = app.state.job_controller.load_inference_config(replaced_run_id)
    assert replaced_config.pipeline.stage1_guides is not None
    assert replaced_config.pipeline.stage1_guides.name == "replacement.txt"
    assert (
        replaced_config.pipeline.stage1_guides.read_text(encoding="utf-8")
        == "replacement text"
    )


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
    assert any(
        line.startswith(".rules-editor .editor-row button {") for line in css_rules
    )
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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 200
    run = app.state.run_store.list_runs()[0]
    config = app.state.job_controller.load_inference_config(run.run_id)
    assert config.output.directory == Path("/app/outputs/web-output")


def test_container_dashboard_rejects_unmounted_host_output_path(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data", container_mode=True))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": "/Users/example/Desktop/custom-output",
            "pipeline": "complete",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
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


def test_preview_marks_invalid_agentic_stage_toggle(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "agentic": "true",
            "verify_stage1": "sometimes",
            "verify_stage2": "true",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 422
    assert (
        'class="agentic-stage-toggle field-invalid" data-field-error="verify_stage1"'
        in response.text
    )


def test_preview_ignores_retired_controls_from_a_stale_browser_tab(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(data_dir=tmp_path / "app-data"))

    response = client.post(
        "/runs/preview",
        data={
            "output_directory": str(tmp_path / "output"),
            "pipeline": "complete",
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "page_limit": "12",
            "media_reference": "inline",
            "prompt_cache": "off",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
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
            "stage1_model": "anthropic/claude-sonnet-5",
            "stage1_provider": "openrouter",
            "stage2_provider": "anthropic",
            "openrouter_provider": "anthropic",
            "reasoning": "none",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
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
        files={"dictionary_pdf": ("dictionary.pdf", _pdf_bytes(), "application/pdf")},
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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
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
    assert (
        b"sk-ant-browser-secret" not in (tmp_path / "mudidi-web.sqlite3").read_bytes()
    )

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
    assert deleted.json() == {
        "status": "deleted",
        "provider": "anthropic",
        "available": False,
        "source": "missing",
    }
    assert restarted.post("/credentials/anthropic/reveal").status_code == 404


def test_deleting_persistent_override_reports_environment_fallback(
    tmp_path: Path,
) -> None:
    vault = CredentialVault(
        environ={"ANTHROPIC_API_KEY": "env-only-secret"},
        persistent_store=PersistentCredentialStore(
            database_path=tmp_path / "mudidi-web.sqlite3",
            key_path=tmp_path / ".credential-key",
        ),
    )
    client = TestClient(create_app(data_dir=tmp_path, credential_vault=vault))

    saved = client.post(
        "/credentials/anthropic",
        data={"api_key": "persistent-override"},
        headers={"accept": "application/json"},
    )
    deleted = client.post("/credentials/anthropic/delete")

    assert saved.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "deleted",
        "provider": "anthropic",
        "available": True,
        "source": "environment",
    }
    home = client.get("/")
    assert 'data-key-source="environment"' in home.text
    assert 'data-key-available="true"' in home.text
    assert 'data-delete-key data-provider="anthropic"' not in home.text


def test_environment_credential_has_no_destructive_action(tmp_path: Path) -> None:
    vault = CredentialVault(
        environ={"ANTHROPIC_API_KEY": "env-only-secret"},
        persistent_store=PersistentCredentialStore(
            database_path=tmp_path / "mudidi-web.sqlite3",
            key_path=tmp_path / ".credential-key",
        ),
    )
    response = TestClient(create_app(data_dir=tmp_path, credential_vault=vault)).get(
        "/"
    )

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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
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
            "stage1_provider": "anthropic",
            "stage2_provider": "anthropic",
            "model": "anthropic/claude-sonnet-5",
            "reasoning": "low",
            "dictionary_pages": "1",
        },
        files={"dictionary_pdf": ("../escape.pdf", _pdf_bytes(), "application/pdf")},
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
