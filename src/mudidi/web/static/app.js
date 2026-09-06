"use strict";

const runForm = document.querySelector("form.run-form");
const runFormStorageKey = "mudidi:new-run-form:v1";
const presetStateElement = document.querySelector("#preset-form-state");
const wizard = document.querySelector("[data-new-run-wizard]");
const wizardPanels = wizard ? [...wizard.querySelectorAll("[data-wizard-panel]")] : [];
const wizardOrder = ["input", "pipeline", "model", "agentic"];
const wizardStorageKey = "mudidi:new-run-wizard:v1";
const readWizardSessionState = () => {
  try {
    const raw = window.sessionStorage.getItem(wizardStorageKey);
    if (!raw) return {};
    if (raw.startsWith("{")) return JSON.parse(raw) || {};
    return { step: raw };
  } catch (_error) {
    return {};
  }
};
const wizardSessionState = readWizardSessionState();
let activeWizardStep = "input";
let wizardErrorSequence = 0;
let firstInvalidWizardField = null;
let invalidWizardFields = [];
let invalidAttemptResetScheduled = false;

const persistWizardStep = () => {
  if (!wizard) return;
  try {
    window.sessionStorage.setItem(wizardStorageKey, JSON.stringify({
      step: activeWizardStep,
      stage2Mode: stage2State?.mode || "shared",
    }));
  } catch (_error) {
    // Storage may be unavailable in privacy-restricted browser contexts.
  }
};

const wizardPanelForField = (field) => field.closest("[data-wizard-panel]");

const wizardFieldOwner = (field) => (
  field.closest("[data-wizard-field], .dropzone, label, fieldset, .input-row")
  || field.parentElement
);

const wizardFieldErrorAnchor = (field) => field.closest(".input-row") || field;

const wizardFieldErrorScope = (field) => (
  wizardFieldErrorAnchor(field).parentElement || wizardFieldOwner(field)
);

const wizardFieldErrorId = (field) => {
  if (!field.dataset.wizardErrorId) {
    const base = (field.id || field.name || "field").replace(/[^a-z0-9_-]+/gi, "-");
    field.dataset.wizardErrorId = `wizard-field-error-${base}-${++wizardErrorSequence}`;
  }
  return field.dataset.wizardErrorId;
};

const wizardClientError = (field) => {
  const scope = wizardFieldErrorScope(field);
  if (!scope) return null;
  const id = field.dataset.wizardErrorId;
  return id
    ? [...scope.querySelectorAll(".field-error-message")]
      .find((message) => message.dataset.wizardFieldErrorFor === id)
    : null;
};

const markWizardFieldInvalid = (field) => {
  const owner = wizardFieldOwner(field);
  if (!owner) return;
  owner.classList.add("field-invalid");
  const errorId = wizardFieldErrorId(field);
  let message = wizardClientError(field);
  if (!message) {
    message = document.createElement("small");
    message.className = "field-error-message";
    message.dataset.wizardFieldErrorFor = errorId;
    wizardFieldErrorAnchor(field).insertAdjacentElement("afterend", message);
  }
  message.id = errorId;
  message.textContent = field.validationMessage;
  const describedBy = new Set((field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
  describedBy.add(errorId);
  field.setAttribute("aria-describedby", [...describedBy].join(" "));
  field.setAttribute("aria-invalid", "true");
};

const clearWizardFieldInvalid = (field) => {
  const owner = wizardFieldOwner(field);
  if (!owner) return;
  wizardClientError(field)?.remove();
  const errorId = field.dataset.wizardErrorId;
  if (errorId) {
    const describedBy = (field.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((id) => id && id !== errorId);
    if (describedBy.length) field.setAttribute("aria-describedby", describedBy.join(" "));
    else field.removeAttribute("aria-describedby");
  }
  if (!owner.hasAttribute("data-field-error")) {
    owner.classList.remove("field-invalid");
    field.removeAttribute("aria-invalid");
  }
};

const wizardFieldLabel = (field) => {
  const heading = field.closest("label")?.querySelector(".field-heading");
  const headingText = heading
    ? [...heading.childNodes]
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent.trim())
      .join(" ")
      .trim()
    : "";
  return (
    field.getAttribute("aria-label")
    || headingText
    || field.name
    || field.id
    || "Field"
  );
};

const updateWizardValidationSummary = (panel, invalidFields) => {
  const summary = panel.querySelector("[data-wizard-validation-summary]");
  if (!summary) return;
  summary.replaceChildren();
  summary.hidden = invalidFields.length === 0;
  if (!invalidFields.length) return;
  const heading = document.createElement("strong");
  heading.textContent = "Complete the highlighted fields before continuing.";
  summary.append(heading);
  if (invalidFields.length > 1) {
    const list = document.createElement("ul");
    invalidFields.forEach((field) => {
      const item = document.createElement("li");
      item.textContent = `${wizardFieldLabel(field)}: ${field.validationMessage}`;
      list.append(item);
    });
    summary.append(list);
  }
};

const setWizardStep = (step, { focus = true } = {}) => {
  if (!wizard || !wizardOrder.includes(step)) return;
  activeWizardStep = step;
  wizardPanels.forEach((panel) => {
    panel.hidden = panel.dataset.wizardPanel !== step;
  });
  document.querySelectorAll("[data-wizard-marker]").forEach((marker) => {
    const markerIndex = [...marker.parentElement.children].indexOf(marker);
    const activeIndex = wizardOrder.indexOf(step);
    if (marker.dataset.wizardMarker === step) marker.setAttribute("aria-current", "step");
    else marker.removeAttribute("aria-current");
    marker.classList.toggle("is-active", marker.dataset.wizardMarker === step);
    marker.classList.toggle("is-complete", markerIndex < activeIndex);
  });
  persistWizardStep();
  if (focus) document.querySelector(`#wizard-${step}-title`)?.focus();
};

const validateWizardPanel = (panel) => {
  const fields = [...panel.querySelectorAll("input, select, textarea")]
    .filter((field) => !field.disabled && !field.closest("[hidden]"));
  const invalidFields = fields.filter((field) => !field.checkValidity());
  if (!invalidFields.length) {
    updateWizardValidationSummary(panel, []);
    return true;
  }
  invalidFields.forEach(markWizardFieldInvalid);
  updateWizardValidationSummary(panel, invalidFields);
  invalidFields[0].focus();
  return false;
};

const persistRunForm = () => {
  if (!runForm) return;
  if (stage2State?.mode === "shared") synchronizeSharedStage2();
  const state = {};
  [...runForm.elements].forEach((field) => {
    if (!field.name || ["file", "password", "submit", "button"].includes(field.type)) return;
    if (!state[field.name]) state[field.name] = [];
    if (field.type === "checkbox" || field.type === "radio") {
      if (field.checked) state[field.name].push(field.value);
    } else {
      state[field.name].push(field.value);
    }
  });
  try {
    window.sessionStorage.setItem(runFormStorageKey, JSON.stringify(state));
  } catch (_error) {
    // Storage may be unavailable in privacy-restricted browser contexts.
  }
};

const restoreRunForm = () => {
  if (!runForm) return;
  let state;
  try {
    state = presetStateElement
      ? JSON.parse(presetStateElement.textContent || "null")
      : JSON.parse(window.sessionStorage.getItem(runFormStorageKey) || "null");
  } catch (_error) {
    return;
  }
  if (!state || typeof state !== "object") return;

  const targetCount = Math.max(
    state.profile_target_languages?.length || 0,
    state.profile_target_scripts?.length || 0,
  );
  const rows = document.querySelector("#profile-target-rows");
  const template = document.querySelector("#profile-target-template");
  while (rows && template && rows.children.length < targetCount) {
    rows.append(template.content.cloneNode(true));
  }

  Object.entries(state).forEach(([name, values]) => {
    if (!Array.isArray(values)) return;
    if (name === "output_policy") {
      values = values.map((value) => value === "new" ? "resume" : value);
    }
    if (name === "evaluator_reasoning" && values[0] === "") values = ["high"];
    if (name === "rewriter_reasoning" && values[0] === "") values = ["low"];
    const fields = [...runForm.elements].filter((field) => field.name === name);
    fields.forEach((field, index) => {
      if (field.type === "file" || field.type === "password") return;
      if (field.type === "checkbox" || field.type === "radio") {
        field.checked = values.includes(field.value);
        if (presetStateElement && ["verify_stage1", "verify_stage2"].includes(name)) {
          field.dataset.userTouched = "true";
        }
      } else if (values[index] !== undefined) {
        field.value = values[index];
      }
    });
  });
};

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;

  const kind = button.dataset.add;
  if (kind) {
    const template = document.querySelector(`#${kind}-template`);
    const rows = document.querySelector(`#${kind}-rows`);
    if (template && rows) rows.append(template.content.cloneNode(true));
    persistRunForm();
    return;
  }

  if (button.hasAttribute("data-remove")) {
    const row = button.closest(".editor-row");
    if (row) row.remove();
    persistRunForm();
  }
});

const otherInformationToggle = document.querySelector("[data-profile-other-toggle]");
const otherInformationField = document.querySelector("#profile-other-information");
if (otherInformationToggle && otherInformationField) {
  const otherInformationInput = otherInformationField.querySelector("textarea");
  const synchronizeOtherInformation = () => {
    const selected = otherInformationToggle.checked;
    otherInformationField.hidden = !selected;
    if (otherInformationInput) otherInformationInput.disabled = !selected;
  };
  otherInformationToggle.addEventListener("change", synchronizeOtherInformation);
  synchronizeOtherInformation();
}

document.querySelectorAll(".info-button").forEach((button) => {
  button.addEventListener("pointerenter", () => {
    button.classList.add("is-tooltip-hovered");
  });
  button.addEventListener("pointerleave", () => {
    button.classList.remove("is-tooltip-hovered");
  });
});

const pipelineChoices = [...document.querySelectorAll('input[name="pipeline"]')];
const providerValue = document.querySelector("[data-provider-value]");
const providerChoices = [...document.querySelectorAll("[data-provider-choice]")];
const modelSelects = [...document.querySelectorAll("[data-model-select]")];
const openRouterProvider = document.querySelector("[data-openrouter-provider]");
const stage2Container = document.querySelector("[data-stage2-container]");
const stage2Toggle = document.querySelector("[data-stage2-toggle]");
const pipelineStages = {
  complete: new Set(["stage1", "pass1", "pass2"]),
  transcription: new Set(["stage1"]),
  structure: new Set(["pass1", "pass2"]),
};
const providerDefaults = {
  anthropic: "anthropic/claude-sonnet-5",
  openai: "openai/gpt-5.6-terra",
  gemini: "gemini/gemini-3.5-flash",
  openrouter: "openrouter/anthropic/claude-sonnet-5",
};
const providerLabels = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
  custom: "your selected provider",
};
const credentialProviderLabels = {
  ...providerLabels,
  custom: "Other / advanced provider",
};
const customModelPlaceholder = (provider) => provider === "openrouter"
  ? "e.g. qwen/qwen3-235b-a22b"
  : `Enter a model name supported by ${providerLabels[provider] || "your selected provider"}`;

const credentialCards = [...document.querySelectorAll("[data-credential-card]")];
const selectedCredential = document.querySelector("[data-selected-credential]");
const otherCredentials = document.querySelector("[data-other-credentials]");
const selectedProviderBadge = document.querySelector("[data-selected-provider-badge]");

const credentialStatusLabels = {
  persistent: "Stored",
  environment: "Available from environment",
  temporary: "Available for this session",
  missing: "Not saved",
};
const credentialBadgeLabels = {
  persistent: "Key saved",
  environment: "Environment key",
  temporary: "Session key",
  missing: "Not saved",
};
const credentialPlaceholders = {
  persistent: "Saved key — leave blank to keep it",
  environment: "Environment key available — enter a key to save an override",
  temporary: "Session key available — enter a key to save an override",
  missing: "Paste provider API key",
};

const ensureDeleteButton = (card, provider, saved) => {
  const label = card?.querySelector("label");
  if (!label) return;
  let button = card.querySelector("[data-delete-key]");
  if (saved && !button) {
    button = document.createElement("button");
    button.type = "button";
    button.className = "credential-delete";
    button.dataset.deleteKey = "";
    button.dataset.provider = provider;
    button.textContent = "Remove saved key";
    label.append(button);
  } else if (!saved && button) {
    button.remove();
  }
};

const renderCredentialSelection = (provider = providerValue?.value) => {
  if (!provider) return;
  const selected = credentialCards.find((card) => card.dataset.provider === provider);
  if (selected && selectedCredential) selectedCredential.append(selected);
  credentialCards
    .filter((card) => card !== selected)
    .forEach((card) => otherCredentials?.append(card));
  const source = selected?.dataset.keySource || "missing";
  if (selectedProviderBadge) {
    selectedProviderBadge.textContent = `${credentialProviderLabels[provider] || provider} · ${credentialBadgeLabels[source] || credentialBadgeLabels.missing}`;
  }
};
const applyCredentialStatus = (card, provider, payload) => {
  const source = payload?.source;
  const available = payload?.available;
  if (
    typeof source !== "string"
    || typeof available !== "boolean"
    || !Object.prototype.hasOwnProperty.call(credentialStatusLabels, source)
  ) {
    return false;
  }
  card.dataset.keySaved = String(source === "persistent");
  card.dataset.keyAvailable = String(available);
  card.dataset.keySource = source;
  const input = card.querySelector(`#credential-${provider}`);
  const status = card.querySelector(`#credential-status-${provider}`);
  if (input) input.placeholder = credentialPlaceholders[source];
  if (status) status.textContent = credentialStatusLabels[source];
  ensureDeleteButton(card, provider, source === "persistent");
  renderCredentialSelection(providerValue?.value);
  return true;
};

const synchronizeCustomModel = (select) => {
  const custom = select.parentElement.querySelector("[data-custom-model]");
  if (!custom) return;
  const active = !select.closest("[data-stage-control]").hidden;
  const customSelected = select.value === "__other__";
  custom.hidden = !customSelected;
  custom.disabled = !active || !customSelected;
  custom.required = active && customSelected;
};

const synchronizeModels = (providerChanged = false) => {
  if (!providerValue) return;
  const provider = providerValue.value;
  providerChoices.forEach((choice) => {
    choice.value = provider;
  });
  modelSelects.forEach((select) => {
    const active = !select.closest("[data-stage-control]").hidden;
    const manualEntry = provider === "openrouter" || provider === "custom";
    [...select.options].forEach((option) => {
      const enabled = option.dataset.modelProvider === provider || option.value === "__other__";
      option.hidden = !enabled;
      option.disabled = !enabled;
    });
    const selected = select.selectedOptions[0];
    if (!selected || selected.disabled || providerChanged) {
      const preferred = providerDefaults[provider];
      const next = [...select.options].find((option) => option.value === preferred)
        || [...select.options].find((option) => !option.disabled);
      if (next) select.value = next.value;
    }
    if (manualEntry) select.value = "__other__";
    select.hidden = manualEntry;
    select.disabled = !active || manualEntry;
    const custom = select.parentElement.querySelector("[data-custom-model]");
    if (custom) {
      if (providerChanged) custom.value = "";
      custom.placeholder = customModelPlaceholder(provider);
    }
    synchronizeCustomModel(select);
  });
  if (openRouterProvider) {
    const visible = provider === "openrouter";
    openRouterProvider.hidden = !visible;
    openRouterProvider.querySelectorAll("input").forEach((input) => {
      input.disabled = !visible;
    });
  }
};

const agenticModelGroups = [...document.querySelectorAll("[data-agentic-model-group]")];
const synchronizeAgenticModelGroup = (group, providerChanged = false) => {
  const provider = group.querySelector("[data-agentic-provider]")?.value;
  const select = group.querySelector("[data-agentic-model]");
  const custom = group.querySelector("[data-agentic-custom-model]");
  if (!provider || !select || !custom) return;

  [...select.options].forEach((option) => {
    const enabled = !option.value
      || option.value === "__other__"
      || option.dataset.modelProvider === provider;
    option.hidden = !enabled;
    option.disabled = !enabled;
  });
  const selected = select.selectedOptions[0];
  if (providerChanged || !selected || selected.disabled) select.value = "";

  const manualEntry = provider === "openrouter" || provider === "custom";
  if (manualEntry) select.value = "";
  select.hidden = manualEntry;
  const agenticEnabled = !group.closest("[data-agentic-settings]")?.hidden;
  select.disabled = !agenticEnabled || manualEntry;
  const customSelected = manualEntry || select.value === "__other__";
  custom.hidden = !customSelected;
  custom.disabled = !agenticEnabled || !customSelected;
  custom.placeholder = customModelPlaceholder(provider);
};
const stage2FieldNames = {
  pass1: {
    model: "stage2_pass1_model",
    customModel: "stage2_pass1_custom_model",
    reasoning: "stage2_pass1_reasoning",
  },
  pass2: {
    model: "stage2_pass2_model",
    customModel: "stage2_pass2_custom_model",
    reasoning: "stage2_pass2_reasoning",
  },
};

const stage2Field = (pass, key) => runForm?.elements.namedItem(stage2FieldNames[pass]?.[key]);
const readStage2Pass = (pass) => ({
  model: stage2Field(pass, "model")?.value || "",
  customModel: stage2Field(pass, "customModel")?.value || "",
  reasoning: stage2Field(pass, "reasoning")?.value || "",
});
const synchronizeStage2CustomModels = () => {
  ["pass1", "pass2"].forEach((pass) => {
    const model = stage2Field(pass, "model");
    if (model) synchronizeCustomModel(model);
  });
};
const writeStage2Pass = (pass, state) => {
  const model = stage2Field(pass, "model");
  const customModel = stage2Field(pass, "customModel");
  const reasoning = stage2Field(pass, "reasoning");
  if (model && state.model !== undefined) model.value = state.model;
  if (customModel && state.customModel !== undefined) customModel.value = state.customModel;
  if (reasoning && state.reasoning !== undefined) reasoning.value = state.reasoning;
  synchronizeStage2CustomModels();
};
const stage2ValuesEqual = (left, right) => (
  left.model === right.model
  && left.customModel === right.customModel
  && left.reasoning === right.reasoning
);

const synchronizeSharedStage2 = () => {
  if (stage2State?.mode !== "shared") return;
  stage2State.shared = readStage2Pass("pass1");
  writeStage2Pass("pass2", stage2State.shared);
  updateStage2Summary();
};
const providerCompatibleStage2State = (state) => ({
  ...state,
  customModel: state.model === "__other__" ? state.customModel : "",
});
const migrateProviderFields = (cachedState, activeState) => ({
  ...cachedState,
  model: activeState.model,
  customModel: activeState.customModel,
});

const migrateStage2CachesForProvider = () => {
  if (!stage2State) return;
  const pass1 = providerCompatibleStage2State(readStage2Pass("pass1"));
  const pass2 = providerCompatibleStage2State(readStage2Pass("pass2"));
  stage2State.shared = migrateProviderFields(stage2State.shared, pass1);
  stage2State.split.pass1 = migrateProviderFields(stage2State.split.pass1, pass1);
  stage2State.split.pass2 = migrateProviderFields(stage2State.split.pass2, pass2);
  if (stage2State.mode === "shared") {
    writeStage2Pass("pass1", pass1);
    writeStage2Pass("pass2", pass1);
  } else {
    synchronizeStage2CustomModels();
  }
};

const modelDisplayName = (pass) => {
  const state = readStage2Pass(pass);
  if (state.model === "__other__") return state.customModel || "Custom model";
  const selected = stage2Field(pass, "model")?.selectedOptions[0];
  return selected?.textContent.trim() || state.model || "Not selected";
};

const stage2IsEnabled = () => {
  const selected = pipelineChoices.find((choice) => choice.checked);
  const active = selected ? pipelineStages[selected.value] : null;
  return Boolean(active?.has("pass1") || active?.has("pass2"));
};

const updateStage2Summary = () => {
  const summary = document.querySelector("[data-stage2-summary-model]");
  if (!summary || !stage2State) return;
  if (!stage2IsEnabled()) {
    summary.textContent = "Not used";
    return;
  }
  if (stage2State.mode === "shared") {
    summary.textContent = `Shared model · ${modelDisplayName("pass1")}`;
  } else {
    summary.textContent = `Separate pass models · Pass 1: ${modelDisplayName("pass1")} · Pass 2: ${modelDisplayName("pass2")}`;
  }
};

const renderStage2Mode = () => {
  if (!stage2State) return;
  const split = stage2State.mode === "split";
  if (stage2Container) stage2Container.dataset.stage2Mode = stage2State.mode;
  const pass1Card = stage2Container?.querySelector('[data-stage2-pass="pass1"]');
  const pass2Card = stage2Container?.querySelector('[data-stage2-pass="pass2"]');
  if (pass1Card) {
    pass1Card.querySelectorAll("[data-stage2-model-label]").forEach((label) => {
      label.textContent = split ? "Stage 2 Pass 1 model" : "Stage 2 model";
    });
    pass1Card.querySelectorAll("[data-stage2-reasoning-label]").forEach((label) => {
      label.textContent = split ? "Stage 2 Pass 1 reasoning" : "Stage 2 reasoning";
    });
    pass1Card.querySelectorAll(".info-button").forEach((button) => {
      const currentLabel = button.getAttribute("aria-label") || "";
      const kind = currentLabel.includes("reasoning") ? "model reasoning" : "model";
      button.setAttribute("aria-label", `About ${split ? "Stage 2 Pass 1" : "Stage 2"} ${kind}`);
    });
  }
  if (pass2Card) pass2Card.hidden = !split;
  if (stage2Toggle) {
    stage2Toggle.textContent = split ? "Use one Stage 2 model" : "Advanced · split passes";
    stage2Toggle.setAttribute("aria-expanded", String(split));
  }
  if (!split) synchronizeSharedStage2();
  else updateStage2Summary();
  synchronizeStage2CustomModels();
};

const enterSplitStage2 = () => {
  stage2State.shared = readStage2Pass("pass1");
  if (stage2HasVisitedSplit) {
    writeStage2Pass("pass1", stage2State.split.pass1);
    writeStage2Pass("pass2", stage2State.split.pass2);
  } else {
    writeStage2Pass("pass1", stage2State.shared);
    writeStage2Pass("pass2", stage2State.shared);
    stage2State.split.pass1 = readStage2Pass("pass1");
    stage2State.split.pass2 = readStage2Pass("pass2");
    stage2HasVisitedSplit = true;
  }
  stage2State.mode = "split";
  renderStage2Mode();
};

const enterSharedStage2 = () => {
  stage2State.split.pass1 = readStage2Pass("pass1");
  stage2State.split.pass2 = readStage2Pass("pass2");
  writeStage2Pass("pass1", stage2State.shared);
  writeStage2Pass("pass2", stage2State.shared);
  stage2State.mode = "shared";
  renderStage2Mode();
};

const synchronizeStage2Pass = (pass) => {
  const model = stage2Field(pass, "model");
  if (model) synchronizeCustomModel(model);
  if (stage2State?.mode === "shared" && pass === "pass1") {
    synchronizeSharedStage2();
  } else if (stage2State?.mode === "split" && pass) {
    stage2State.split[pass] = readStage2Pass(pass);
    updateStage2Summary();
  }
};

agenticModelGroups.forEach((group) => {
  group.querySelector("[data-agentic-provider]")?.addEventListener("change", () => {
    synchronizeAgenticModelGroup(group, true);
  });
  group.querySelector("[data-agentic-model]")?.addEventListener("change", () => {
    synchronizeAgenticModelGroup(group);
  });
});

const synchronizePipeline = () => {
  const selected = pipelineChoices.find((choice) => choice.checked);
  if (!selected) return;
  const active = pipelineStages[selected.value] || new Set();
  document.querySelectorAll("[data-stage-control]").forEach((control) => {
    const stages = control.dataset.pipelineStages.split(/\s+/);
    const visible = stages.some((stage) => active.has(stage));
    control.hidden = !visible;
    control.querySelectorAll("input, select, textarea").forEach((input) => {
      input.disabled = !visible;
    });
  });
  if (stage2Container) {
    stage2Container.hidden = !(active.has("pass1") || active.has("pass2"));
  }
  const agenticEnabled = [...document.querySelectorAll('input[name="agentic"]')]
    .some((choice) => choice.checked && choice.value === "true");
  [
    ["verify_stage1", active.has("stage1")],
    ["verify_stage2", active.has("pass1") || active.has("pass2")],
  ].forEach(([name, enabled]) => {
    const input = document.querySelector(`input[name="${name}"]`);
    if (!input) return;
    input.disabled = !agenticEnabled || !enabled;
    if (!enabled) input.checked = false;
    else if (input.dataset.userTouched !== "true") input.checked = true;
  });
  synchronizeModels();
  if (typeof synchronizeManual === "function") synchronizeManual();
  updateStage2Summary();
  if (typeof synchronizeAgenticAvailability === "function") {
    synchronizeAgenticAvailability(agenticEnabled);
  }
};

modelSelects.forEach((select) => {
  select.addEventListener("change", () => {
    const pass = select.closest("[data-stage2-pass]")?.dataset.stage2Pass;
    if (pass) synchronizeStage2Pass(pass);
    else synchronizeCustomModel(select);
  });
});
providerChoices.forEach((choice) => {
  choice.addEventListener("change", () => {
    if (!providerValue) return;
    providerValue.value = choice.value;
    providerChoices.forEach((other) => {
      other.value = choice.value;
    });
    synchronizeModels(true);
    migrateStage2CachesForProvider();
    updateStage2Summary();
    renderCredentialSelection(choice.value);
  });
});
stage2Toggle?.addEventListener("click", () => {
  if (stage2State.mode === "shared") enterSplitStage2();
  else enterSharedStage2();
  persistRunForm();
  persistWizardStep();
});
pipelineChoices.forEach((choice) => choice.addEventListener("change", synchronizePipeline));

document.querySelectorAll('input[name="verify_stage1"], input[name="verify_stage2"]').forEach((input) => {
  input.addEventListener("change", () => {
    input.dataset.userTouched = "true";
  });
});

const agenticChoices = [...document.querySelectorAll('input[name="agentic"]')];
const agenticSettings = document.querySelector("[data-agentic-settings]");
const synchronizeAgenticAvailability = (enabled) => {
  if (!agenticSettings) return;
  agenticSettings.querySelectorAll("input, select, textarea").forEach((input) => {
    const stageDisabled = input.name === "verify_stage1" || input.name === "verify_stage2";
    input.disabled = !enabled || (stageDisabled && input.disabled);
  });
  agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
};
const synchronizeAgentic = () => {
  if (!agenticSettings) return;
  const enabled = agenticChoices.some((choice) => choice.checked && choice.value === "true");
  agenticSettings.hidden = !enabled;
  synchronizeAgenticAvailability(enabled);
  if (enabled) synchronizePipeline();
};
agenticChoices.forEach((choice) => choice.addEventListener("change", synchronizeAgentic));

document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this run from local history? Generated output files will be kept.")) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll("[data-confirm-delete-all]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm("Delete all inactive runs from local history? Generated output files will be kept.")) {
      event.preventDefault();
    }
  });
});

const formatElapsed = (elapsedSeconds) => {
  if (elapsedSeconds < 60) return `${elapsedSeconds}s`;
  const minutes = Math.floor(elapsedSeconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
};

document.querySelectorAll("[data-elapsed-from]").forEach((element) => {
  const startedAt = Date.parse(element.dataset.elapsedFrom || "");
  if (!Number.isFinite(startedAt)) return;
  const updateElapsed = () => {
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    element.textContent = `Elapsed ${formatElapsed(elapsedSeconds)}`;
  };
  updateElapsed();
  window.setInterval(updateElapsed, 1000);
});

document.querySelectorAll("[data-confirm-preset-delete]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const name = form.dataset.confirmPresetDelete || "this preset";
    if (!window.confirm(`Remove preset "${name}"? Its managed inputs will also be removed.`)) {
      event.preventDefault();
    }
  });
});

const manualChoices = [...document.querySelectorAll('input[name="mdf_manual_source"]')];
const customManual = document.querySelector("[data-custom-mdf-manual]");
const synchronizeManual = () => {
  if (!customManual) return;
  const selected = manualChoices.find((choice) => choice.checked);
  const visible = selected && selected.value === "upload" && !selected.disabled;
  customManual.hidden = !visible;
  customManual.querySelectorAll("input").forEach((input) => {
    input.disabled = !visible;
    input.required = visible;
  });
};
manualChoices.forEach((choice) => choice.addEventListener("change", synchronizeManual));
restoreRunForm();
const restoredPass1 = readStage2Pass("pass1");
const restoredPass2 = readStage2Pass("pass2");
const restoredPassesUnequal = !stage2ValuesEqual(restoredPass1, restoredPass2);
const stage2State = {
  mode: restoredPassesUnequal
    ? "split"
    : (wizardSessionState.stage2Mode === "split" ? "split" : "shared"),
  shared: restoredPass1,
  split: {
    pass1: restoredPass1,
    pass2: restoredPass2,
  },
};
let stage2HasVisitedSplit = stage2State.mode === "split";
if (presetStateElement && otherInformationToggle) {
  otherInformationToggle.dispatchEvent(new Event("change"));
}
renderStage2Mode();
synchronizePipeline();
synchronizeAgentic();
synchronizeManual();
renderCredentialSelection();

const beginWizardInvalidAttempt = () => {
  firstInvalidWizardField = null;
  invalidWizardFields = [];
};

const scheduleWizardInvalidAttemptReset = () => {
  if (invalidAttemptResetScheduled) return;
  invalidAttemptResetScheduled = true;
  queueMicrotask(() => {
    firstInvalidWizardField = invalidWizardFields.reduce((candidate, field) => {
      if (!candidate) return field;
      return candidate.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING
        ? candidate
        : field;
    }, null);
    if (firstInvalidWizardField) {
      const panel = wizardPanelForField(firstInvalidWizardField);
      if (panel?.dataset.wizardPanel && panel.dataset.wizardPanel !== activeWizardStep) {
        setWizardStep(panel.dataset.wizardPanel, { focus: false });
      }
      firstInvalidWizardField.focus();
    }
    invalidWizardFields = [];
    invalidAttemptResetScheduled = false;
  });
};

if (wizard) {
  let initialWizardStep = "input";
  const firstServerError = wizard.querySelector("[data-field-error]");
  const firstServerPanel = firstServerError && wizardPanelForField(firstServerError);
  if (firstServerPanel?.dataset.wizardPanel) {
    initialWizardStep = firstServerPanel.dataset.wizardPanel;
  } else {
    if (wizardOrder.includes(wizardSessionState.step)) {
      initialWizardStep = wizardSessionState.step;
    }
  }
  setWizardStep(initialWizardStep, { focus: false });

  wizard.querySelectorAll("[data-wizard-next]").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = button.closest("[data-wizard-panel]");
      const nextStep = button.dataset.wizardNext;
      if (!panel || !nextStep || !validateWizardPanel(panel)) return;
      persistRunForm();
      setWizardStep(nextStep);
    });
  });
  wizard.querySelectorAll("[data-wizard-back]").forEach((button) => {
    button.addEventListener("click", () => {
      const previousStep = button.dataset.wizardBack;
      if (!wizardOrder.includes(previousStep)) return;
      persistRunForm();
      setWizardStep(previousStep);
    });
  });
  wizard.querySelectorAll("[data-wizard-submit]").forEach((button) => {
    button.addEventListener("click", (event) => {
      beginWizardInvalidAttempt();
      const panel = button.closest("[data-wizard-panel]");
      if (!panel || validateWizardPanel(panel)) return;
      event.preventDefault();
      runForm?.reportValidity();
    });
  });
}

if (runForm) {
  runForm.addEventListener("input", (event) => {
    const pass = event.target.closest("[data-stage2-pass]")?.dataset.stage2Pass;
    if (pass) synchronizeStage2Pass(pass);
    persistRunForm();
    if (event.target.validity?.valid) clearWizardFieldInvalid(event.target);
  });
  runForm.addEventListener("change", (event) => {
    const pass = event.target.closest("[data-stage2-pass]")?.dataset.stage2Pass;
    if (pass) synchronizeStage2Pass(pass);
    persistRunForm();
    if (event.target.validity?.valid) clearWizardFieldInvalid(event.target);
  });
  runForm.addEventListener("invalid", (event) => {
    runForm.classList.add("was-validated");
    const field = event.target;
    invalidWizardFields.push(field);
    markWizardFieldInvalid(field);
    scheduleWizardInvalidAttemptReset();
  }, true);
  runForm.addEventListener("submit", () => {
    runForm.classList.add("was-validated");
    persistRunForm();
  });
}

document.querySelectorAll("[data-reveal-key]").forEach((button) => {
  button.addEventListener("click", async () => {
    const provider = button.dataset.provider;
    const target = button.dataset.credentialTarget || `key-${provider}`;
    const input = document.querySelector(`#${target}`);
    if (!input) return;
    if (input.type === "text") {
      input.type = "password";
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-label", `Show ${provider} API key`);
      return;
    }
    if (!input.value) {
      const response = await window.fetch(`/credentials/${provider}/reveal`, {
        method: "POST",
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      input.value = payload.api_key || "";
    }
    input.type = "text";
    button.setAttribute("aria-pressed", "true");
    button.setAttribute("aria-label", `Hide ${provider} API key`);
  });
});

document.querySelectorAll("[data-save-key]").forEach((button) => {
  button.addEventListener("click", async () => {
    const provider = button.dataset.provider;
    const target = button.dataset.credentialTarget || `credential-${provider}`;
    const input = document.querySelector(`#${target}`);
    const status = document.querySelector(`#credential-status-${provider}`);
    const card = button.closest("[data-credential-card]");
    if (!input || !status) return;

    const apiKey = input.value.trim();
    if (!apiKey) {
      status.textContent = "Enter a key first";
      return;
    }

    button.disabled = true;
    status.textContent = "Saving…";
    try {
      const response = await window.fetch(`/credentials/${provider}`, {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({ api_key: apiKey }),
      });
      const payload = await response.json();
      if (!response.ok) {
        status.textContent = payload.detail || "Could not save key";
        return;
      }
      input.value = "";
      input.type = "password";
      if (card) {
        applyCredentialStatus(card, provider, {source: "persistent", available: true});
        status.textContent = "Saved";
      } else {
        input.placeholder = credentialPlaceholders.persistent;
        status.textContent = "Saved";
      }
      const continueAction = button.dataset.continueAction;
      if (continueAction) {
        const continuationUrl = new URL(continueAction, window.location.origin);
        if (continuationUrl.origin !== window.location.origin) {
          status.textContent = "Could not continue safely";
          return;
        }
        const continuation = document.createElement("form");
        continuation.method = "post";
        continuation.action = continuationUrl.pathname + continuationUrl.search;
        continuation.hidden = true;
        document.body.append(continuation);
        continuation.submit();
      }
    } catch (_error) {
      status.textContent = "Could not save key";
    } finally {
      button.disabled = false;
    }
  });
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-key]");
  if (!button) return;
  event.preventDefault();
  const provider = button.dataset.provider;
  const card = button.closest("[data-credential-card]");
  const input = card?.querySelector(`#credential-${provider}`);
  const status = card?.querySelector(`#credential-status-${provider}`);
  if (!card || !input || !status) return;
  button.disabled = true;
  try {
    const response = await window.fetch(`/credentials/${provider}/delete`, {
      method: "POST",
      headers: { "Accept": "application/json" },
    });
    if (!response.ok) {
      status.textContent = "Could not remove key";
      return;
    }
    const payload = await response.json();
    if (!applyCredentialStatus(card, provider, payload)) {
      status.textContent = "Could not remove key";
      return;
    }
    input.value = "";
    input.type = "password";
    const reveal = card.querySelector("[data-reveal-key]");
    reveal?.setAttribute("aria-pressed", "false");
    reveal?.setAttribute("aria-label", `Show ${provider} API key`);
  } catch (_error) {
    status.textContent = "Could not remove key";
  } finally {
    if (button.isConnected) button.disabled = false;
  }
});

const liveRun = document.querySelector('meta[name="mudidi-events"]');
const streamStatus = document.querySelector("[data-stream-status]");
const liveToggle = document.querySelector("[data-live-toggle]");
const livePauseLabel = liveToggle?.querySelector("[data-live-pause-label]");
const liveResumeLabel = liveToggle?.querySelector("[data-live-resume-label]");
const liveEventNames = [
  "stage.started",
  "page.started",
  "page.completed",
  "parse_rules.generated",
  "run.completed",
  "run.failed",
  "run.cancelled",
];
let liveEventSource = null;

const setStreamStatus = (text) => {
  if (streamStatus) streamStatus.textContent = text;
};

const setLiveToggleState = (paused) => {
  if (!liveToggle) return;
  liveToggle.dataset.livePaused = String(paused);
  liveToggle.setAttribute("aria-pressed", String(paused));
  if (livePauseLabel) livePauseLabel.hidden = paused;
  if (liveResumeLabel) liveResumeLabel.hidden = !paused;
};

const stopLiveUpdates = () => {
  if (!liveEventSource) return;
  liveEventSource.close();
  liveEventSource = null;
};

const startLiveUpdates = () => {
  if (!liveRun || !window.EventSource || liveEventSource) return false;
  setStreamStatus("Connecting…");
  const source = new EventSource(liveRun.content);
  liveEventSource = source;
  source.addEventListener("open", () => {
    if (source === liveEventSource) setStreamStatus("Live");
  });
  source.addEventListener("error", () => {
    if (source === liveEventSource) setStreamStatus("Reconnecting…");
  });
  liveEventNames.forEach((eventName) => {
    source.addEventListener(eventName, () => {
      if (source !== liveEventSource) return;
      stopLiveUpdates();
      window.location.reload();
    });
  });
  return true;
};

if (liveRun && window.EventSource) startLiveUpdates();
if (liveToggle) {
  const initiallyPaused = liveResumeLabel ? !liveResumeLabel.hidden : false;
  setLiveToggleState(initiallyPaused);
  liveToggle.addEventListener("click", () => {
    const paused = liveToggle.dataset.livePaused === "true";
    if (paused) {
      const started = startLiveUpdates();
      if (!started) {
        setLiveToggleState(true);
        setStreamStatus("Unavailable");
        return;
      }
      setLiveToggleState(false);
      setStreamStatus("Connecting…");
      return;
    }
    stopLiveUpdates();
    setLiveToggleState(true);
    setStreamStatus("Paused");
  });
}
window.addEventListener("pageshow", () => {
  if (liveToggle?.dataset.livePaused === "true") return;
  startLiveUpdates();
});
window.addEventListener("pagehide", stopLiveUpdates);

const artifactFilters = document.querySelector("[data-artifact-filters]");
if (artifactFilters) {
  const pathFilter = artifactFilters.querySelector("[data-artifact-path-filter]");
  const stageFilter = artifactFilters.querySelector("[data-artifact-stage-filter]");
  const artifactRows = [...document.querySelectorAll("[data-artifact-row]")];
  const emptyState = document.querySelector("[data-artifact-filter-empty]");
  const applyArtifactFilters = () => {
    const pathQuery = (pathFilter?.value || "").trim().toLowerCase();
    const stage = stageFilter?.value || "";
    let visible = 0;
    artifactRows.forEach((row) => {
      const matchesPath = (row.dataset.artifactPath || "").toLowerCase().includes(pathQuery);
      const matchesStage = !stage || row.dataset.artifactStage === stage;
      row.hidden = !(matchesPath && matchesStage);
      if (!row.hidden) visible += 1;
    });
    if (emptyState) emptyState.hidden = visible !== 0;
  };
  pathFilter?.addEventListener("input", applyArtifactFilters);
  stageFilter?.addEventListener("change", applyArtifactFilters);
  applyArtifactFilters();
}

const logConsole = document.querySelector("[data-log-console]");
const copyLogButton = document.querySelector("[data-log-copy]");
const copyLogStatus = document.querySelector("[data-log-copy-status]");
if (logConsole && copyLogButton) {
  copyLogButton.addEventListener("click", async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(logConsole.innerText);
      if (copyLogStatus) copyLogStatus.textContent = "Copied visible text";
    } catch (_error) {
      if (copyLogStatus) copyLogStatus.textContent = "Copy failed";
    }
  });
}

const pageEditor = document.querySelector("[data-page-editor]");
const pageSlider = pageEditor?.querySelector("[data-page-slider]");
if (pageSlider) {
  const position = pageEditor.querySelector("[data-page-position]");
  let pageUrls = [];
  let pageLabels = [];
  try {
    pageUrls = JSON.parse(pageSlider.dataset.pageUrls || "[]");
    pageLabels = JSON.parse(pageSlider.dataset.pageLabels || "[]");
  } catch (_error) {
    pageSlider.disabled = true;
  }

  pageSlider.addEventListener("input", () => {
    const index = Number(pageSlider.value);
    if (position && pageLabels[index] !== undefined) {
      position.textContent = `Page ${index + 1} of ${pageUrls.length} · ${pageLabels[index]}`;
    }
  });
  pageSlider.addEventListener("change", () => {
    const destination = pageUrls[Number(pageSlider.value)];
    if (destination) window.location.assign(destination);
  });
}

const pageTextEditor = pageEditor?.querySelector("form.page-text-editor");
if (pageTextEditor) {
  let hasUnsavedChanges = false;
  pageTextEditor.querySelectorAll("textarea").forEach((textarea) => {
    textarea.addEventListener("input", () => {
      hasUnsavedChanges = true;
    });
  });
  pageTextEditor.addEventListener("submit", () => {
    hasUnsavedChanges = false;
  });
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsavedChanges) return;
    event.preventDefault();
    event.returnValue = "";
  });
}
