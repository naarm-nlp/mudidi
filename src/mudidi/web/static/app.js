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
  heading.textContent = "Complete the highlighted fields before reviewing the run.";
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
    const isActive = marker.dataset.wizardMarker === step;
    if (isActive) marker.setAttribute("aria-current", "step");
    else marker.removeAttribute("aria-current");
    marker.classList.toggle("is-active", isActive);
  });
  persistWizardStep();
  if (focus) document.querySelector(`#wizard-${step}-title`)?.focus();
};


const persistRunForm = () => {
  if (!runForm) return;
  if (stage2State?.mode === "shared") synchronizeSharedStage2();
  synchronizeInstructionPanels();
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
const restoreModelFieldValue = (field, value) => {
  if (
    field.dataset?.modelStage
    && field.options
    && ![...field.options].some((option) => option.value === value)
  ) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = `${value} — current selection`;
    option.dataset.modelProvider = value.split("/", 1)[0];
    field.append(option);
  }
  field.value = value;
  const restoredProvider = field.selectedOptions?.[0]?.dataset.modelProvider;
  if (field.dataset?.modelStage && restoredProvider && restoredProvider !== "other") {
    field.dataset.catalogProvider = restoredProvider;
  }
};


const restoreRunForm = () => {
  if (!runForm) return;
  // A validation-error re-render (POST /runs/preview -> 422) always re-derives
  // preset_state from the ORIGINAL saved preset, ignoring in-progress edits such
  // as switching a kept file attachment to Replace. sessionStorage instead holds
  // exactly what was live in the form the moment it was submitted (persisted by
  // the "submit" listener below), so prefer it whenever a `.form-error-summary`
  // shows this render is a recovery from a rejected submission.
  const recoveringFromValidationError = !!document.querySelector(".form-error-summary");
  const usingPresetState = Boolean(presetStateElement) && !recoveringFromValidationError;
  let state;
  try {
    state = usingPresetState
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
        if (usingPresetState && ["verify_stage1", "verify_stage2"].includes(name)) {
          field.dataset.userTouched = "true";
        }
      } else if (values[index] !== undefined) {
        restoreModelFieldValue(field, values[index]);
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

const dictionaryDropzone = document.querySelector("[data-dictionary-dropzone]");
const dictionaryFileInput = dictionaryDropzone?.querySelector("#dictionary-pdf");
const dictionaryFileStatus = dictionaryDropzone?.querySelector("[data-dictionary-file-status]");

if (dictionaryDropzone && dictionaryFileInput && dictionaryFileStatus) {
  let dragDepth = 0;

  const resetDictionaryDragState = () => {
    dragDepth = 0;
    dictionaryDropzone.classList.remove("is-dragover");
  };
  const showDictionaryFileError = (message) => {
    dictionaryFileInput.value = "";
    dictionaryFileInput.setCustomValidity(message);
    dictionaryDropzone.classList.add("is-drop-invalid");
    dictionaryFileStatus.textContent = message;
  };
  const validateDictionaryFiles = (files) => {
    if (files.length !== 1) return "Choose exactly one PDF file.";
    return files[0].name.toLowerCase().endsWith(".pdf")
      ? ""
      : "Choose a file with a .pdf extension.";
  };
  const updateDictionaryFileSelection = () => {
    const files = [...dictionaryFileInput.files];
    const error = files.length ? validateDictionaryFiles(files) : "";
    if (error) {
      showDictionaryFileError(error);
      return;
    }
    dictionaryFileInput.setCustomValidity("");
    dictionaryDropzone.classList.remove("is-drop-invalid");
    dictionaryFileStatus.textContent = files.length
      ? `Selected: ${files[0].name}`
      : dictionaryFileStatus.dataset.emptyLabel;
    clearWizardFieldInvalid(dictionaryFileInput);
  };

  dictionaryFileInput.addEventListener("change", updateDictionaryFileSelection);
  dictionaryDropzone.addEventListener("dragenter", (event) => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault();
    dragDepth += 1;
    dictionaryDropzone.classList.add("is-dragover");
  });
  dictionaryDropzone.addEventListener("dragover", (event) => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  dictionaryDropzone.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dictionaryDropzone.classList.remove("is-dragover");
  });
  dictionaryDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    resetDictionaryDragState();
    const files = [...(event.dataTransfer?.files || [])];
    const error = validateDictionaryFiles(files);
    if (error) {
      showDictionaryFileError(error);
      return;
    }
    try {
      dictionaryFileInput.files = event.dataTransfer.files;
      dictionaryFileInput.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (_error) {
      showDictionaryFileError("This browser could not attach the dropped PDF.");
    }
  });
}

const bindSingleFileStatus = (input, status) => {
  if (!input || !status) return;
  input.addEventListener("change", () => {
    const file = input.files[0];
    status.textContent = file
      ? `Selected: ${file.name}`
      : status.dataset.emptyLabel;
  });
};

bindSingleFileStatus(
  document.querySelector("[data-mdf-guide-file-input]"),
  document.querySelector("[data-mdf-guide-file-status]"),
);
bindSingleFileStatus(
  document.querySelector("[data-mdf-manual-file-input]"),
  document.querySelector("[data-mdf-manual-file-status]"),
);

const instructionPanels = [...document.querySelectorAll("[data-instruction-source-panel]")];

const instructionPanelHasKeptPreset = (panel) => panel.dataset.instructionHasPreset === "true";
const instructionPanelPresetIsPdf = (panel) => panel.dataset.instructionPresetKind === "pdf";

const instructionPanelRefs = (panel) => ({
  sourceRadios: [...panel.querySelectorAll("[data-instruction-source-radio]")],
  typedPanel: panel.querySelector("[data-instruction-typed-panel]"),
  textarea: panel.querySelector("[data-instruction-typed-panel] textarea"),
  filePanel: panel.querySelector("[data-instruction-file-panel]"),
  keptFileContainer: panel.querySelector("[data-instruction-kept-file]"),
  keptRadios: [...panel.querySelectorAll("[data-instruction-kept-radio]")],
  uploadRow: panel.querySelector("[data-instruction-upload-row]"),
  fileInput: panel.querySelector("[data-instruction-file-input]"),
  fileStatus: panel.querySelector("[data-instruction-file-status]"),
  keepExisting: panel.querySelector("[data-instruction-keep-existing]"),
  pdfPagesField: panel.querySelector("[data-instruction-pdf-pages]"),
  pdfPagesInput: panel.querySelector("[data-instruction-pdf-pages] input"),
  pdfWarning: panel.querySelector("[data-instruction-pdf-warning]"),
});

const synchronizeInstructionPanel = (panel) => {
  if (panel.hidden) return;
  const refs = instructionPanelRefs(panel);
  const selected = refs.sourceRadios.find((radio) => radio.checked) || refs.sourceRadios[0];
  if (!selected) return;
  const isFile = selected.value === "file";

  if (refs.typedPanel) refs.typedPanel.hidden = isFile;
  if (refs.textarea) refs.textarea.disabled = isFile;

  if (refs.filePanel) refs.filePanel.hidden = !isFile;
  const hasKeptPreset = instructionPanelHasKeptPreset(panel);
  const keptSelected = refs.keptRadios.find((radio) => radio.checked);
  const isKeeping = hasKeptPreset && keptSelected?.value === "keep";
  refs.keptRadios.forEach((radio) => {
    radio.disabled = !isFile;
  });
  if (refs.keptFileContainer) refs.keptFileContainer.hidden = !hasKeptPreset;
  if (refs.uploadRow) refs.uploadRow.hidden = isFile && isKeeping;
  const selectedFile = refs.fileInput?.files?.[0] || null;
  if (refs.fileInput) {
    refs.fileInput.disabled = !isFile || isKeeping;
    refs.fileInput.required = isFile && !isKeeping;
  }
  if (refs.keepExisting) {
    refs.keepExisting.disabled = !isFile;
    refs.keepExisting.value = isFile && isKeeping ? "true" : "false";
  }
  if (refs.fileStatus) {
    refs.fileStatus.textContent = selectedFile ? `Selected: ${selectedFile.name}` : "No file selected";
  }
  const currentIsPdf = selectedFile
    ? selectedFile.name.toLowerCase().endsWith(".pdf")
    : isKeeping && instructionPanelPresetIsPdf(panel);
  const showPdfPages = isFile && currentIsPdf;
  if (refs.pdfPagesField) refs.pdfPagesField.hidden = !showPdfPages;
  if (refs.pdfPagesInput) refs.pdfPagesInput.disabled = !showPdfPages;
  if (refs.pdfWarning) refs.pdfWarning.hidden = !showPdfPages;
};

const synchronizeInstructionPanels = () => instructionPanels.forEach(synchronizeInstructionPanel);

const wireInstructionPanel = (panel) => {
  const refs = instructionPanelRefs(panel);
  panel.dataset.instructionActiveSource = (
    refs.sourceRadios.find((radio) => radio.checked) || refs.sourceRadios[0]
  )?.value || "typed";
  synchronizeInstructionPanel(panel);
  refs.sourceRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      const previousMode = panel.dataset.instructionActiveSource;
      const newMode = radio.value;
      if (previousMode === newMode) {
        synchronizeInstructionPanel(panel);
        return;
      }
      const clearing = previousMode === "typed"
        ? Boolean(refs.textarea?.value.trim())
        : Boolean(refs.fileInput?.files?.length) || instructionPanelHasKeptPreset(panel);
      if (clearing) {
        const label = previousMode === "typed" ? "the typed instructions" : "the uploaded instruction file";
        if (!window.confirm(`Switching sources clears ${label}. Continue?`)) {
          refs.sourceRadios.forEach((other) => {
            other.checked = other.value === previousMode;
          });
          synchronizeInstructionPanel(panel);
          return;
        }
        if (previousMode === "typed" && refs.textarea) refs.textarea.value = "";
        if (previousMode === "file" && refs.fileInput) refs.fileInput.value = "";
      }
      panel.dataset.instructionActiveSource = newMode;
      synchronizeInstructionPanel(panel);
      (newMode === "typed" ? refs.textarea : refs.fileInput)?.focus();
    });
  });
  refs.keptRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      if (radio.value === "keep" && refs.fileInput) refs.fileInput.value = "";
      synchronizeInstructionPanel(panel);
      if (radio.value === "replace") refs.fileInput?.focus();
    });
  });
  refs.fileInput?.addEventListener("change", () => synchronizeInstructionPanel(panel));
};

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
const providerChoices = [...document.querySelectorAll("[data-provider-choice]")];
const modelSelects = [...document.querySelectorAll("[data-model-select]")];
const openRouterProvider = document.querySelector("[data-openrouter-provider]");
const stage2Container = document.querySelector("[data-stage2-container]");
const stage2Toggle = document.querySelector("[data-stage2-toggle]");
const stage2Explanation = document.querySelector("[data-stage2-explanation]");
const pipelineStages = {
  complete: new Set(["stage1", "pass1", "pass2"]),
  transcription: new Set(["stage1"]),
  structure: new Set(["pass1", "pass2"]),
};
const apiCredentialEntry = document.querySelector("[data-api-credential-entry]");
const subscriptionEntry = document.querySelector("[data-subscription-entry]");
const customProviderNote = document.querySelector("[data-custom-provider-note]");
const modelRefresh = document.querySelector("[data-model-refresh]");
const modelStatus = document.querySelector("[data-model-status]");
let modelCatalogController = null;
let modelCatalogSequence = 0;
const providerLabels = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
  custom: "your selected provider",
};

const authModeChoices = [...document.querySelectorAll("[data-auth-mode-choice]")];
const authModeValue = () => authModeChoices.find((choice) => choice.checked)?.value || "api_key";
const authSummary = document.querySelector("[data-auth-summary]");
const billingSummary = document.querySelector("[data-billing-summary]");
const subscriptionProviders = {
  openai: "openai",
  gemini: "google",
  anthropic: "claude",
};
const customModelPlaceholder = (provider) => provider === "openrouter"
  ? "e.g. qwen/qwen3-235b-a22b"
  : `Enter a model name supported by ${providerLabels[provider] || "your selected provider"}`;

const credentialCards = [...document.querySelectorAll("[data-credential-card]")];
const subscriptionCards = [...document.querySelectorAll("[data-subscription-card]")];
const agenticModelGroups = [...document.querySelectorAll("[data-agentic-model-group]")];
const portableReasoningEfforts = ["minimal", "low", "medium", "high", "xhigh", "max"];
const reasoningLabel = (effort) => effort === "xhigh"
  ? "Extra high"
  : `${effort.charAt(0).toUpperCase()}${effort.slice(1)}`;

const populateReasoningControl = (select, profile = null) => {
  const reasoning = modelReasoningControl(select);
  if (!reasoning) return;
  const rawEfforts = Array.isArray(profile?.efforts)
    ? profile.efforts
    : portableReasoningEfforts;
  const efforts = portableReasoningEfforts.filter((effort) => rawEfforts.includes(effort));
  const previous = reasoning.value;
  const allowEmpty = [...reasoning.options].some((option) => option.value === "");
  reasoning.replaceChildren();
  if (allowEmpty) {
    const inherit = document.createElement("option");
    inherit.value = "";
    inherit.textContent = "Use default";
    reasoning.append(inherit);
  }
  efforts.forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = reasoningLabel(effort);
    reasoning.append(option);
  });
  if (efforts.length === 0) {
    const unavailable = document.createElement("option");
    unavailable.value = "";
    unavailable.textContent = "Not configurable";
    reasoning.append(unavailable);
    reasoning.value = "";
    reasoning.disabled = true;
    return;
  }
  if (allowEmpty && previous === "") {
    reasoning.value = "";
    return;
  }
  if (efforts.includes(previous)) {
    reasoning.value = previous;
    return;
  }
  const requestedRank = portableReasoningEfforts.indexOf(previous);
  const lower = requestedRank >= 0
    ? efforts.filter((effort) => portableReasoningEfforts.indexOf(effort) <= requestedRank)
    : [];
  const advertisedDefault = efforts.includes(profile?.default) ? profile.default : null;
  reasoning.value = lower.at(-1) || advertisedDefault || efforts[0];
};

const selectedReasoningProfile = (select) => {
  const raw = select.selectedOptions[0]?.dataset.reasoningProfile;
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_error) {
    return null;
  }
};

const synchronizeModelReasoning = (select) => {
  if (select.value === "__other__" || select.hidden) {
    populateReasoningControl(select);
    return;
  }
  populateReasoningControl(select, selectedReasoningProfile(select));
};


const credentialStatusLabels = {
  persistent: "Stored",
  environment: "Available from environment",
  temporary: "Available for this session",
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
  return true;
};

const synchronizeCustomModel = (select) => {
  const custom = select.parentElement.querySelector(
    "[data-custom-model], [data-agentic-custom-model]",
  );
  if (!custom) return;
  const active = !select.closest("[data-stage-control]")?.hidden;
  const customSelected = select.value === "__other__";
  custom.hidden = !customSelected;
  custom.disabled = select.disabled || !active || !customSelected;
  custom.required = !select.disabled && active && customSelected;
};
const appendModelGroup = (select, label, items, provider) => {
  if (!Array.isArray(items) || items.length === 0) return;
  const group = document.createElement("optgroup");
  group.label = label;
  items.forEach((item) => {
    if (
      !item
      || typeof item.model_id !== "string"
      || typeof item.display_name !== "string"
    ) return;
    const option = document.createElement("option");
    option.value = item.model_id;
    option.textContent = item.display_name;
    option.dataset.modelProvider = provider;
    option.dataset.modelCompatibility = item.compatibility || "unverified";
    if (item.reasoning && typeof item.reasoning === "object") {
      option.dataset.reasoningProfile = JSON.stringify(item.reasoning);
    }
    group.append(option);
  });
  if (group.children.length) select.append(group);
};

const applyModelCatalog = (
  select,
  payload,
  provider,
  {preserveSelection = true} = {},
) => {
  if (payload.warning?.code === "authentication_required") {
    const instruction = providerAuthInstruction(provider);
    resetModelSelect(select, instruction);
    select.hidden = false;
    select.disabled = true;
    const custom = select.parentElement?.querySelector?.(
      "[data-custom-model], [data-agentic-custom-model]",
    );
    if (custom) {
      custom.value = "";
      custom.hidden = true;
      custom.disabled = true;
      custom.required = false;
    }
    const reasoning = typeof select.closest === "function"
      ? modelReasoningControl(select)
      : null;
    if (reasoning) reasoning.disabled = true;
    const hint = select.parentElement?.querySelector?.("[data-model-auth-hint]");
    if (hint) {
      hint.hidden = false;
      hint.textContent = instruction;
    }
    return;
  }
  const selectedValue = select.value;
  const selectedLabel = select.selectedOptions[0]?.textContent || selectedValue;
  const allowEmpty = select.hasAttribute("data-agentic-model");
  const subscription = authModeValue() === "subscription";
  select.replaceChildren();
  if (allowEmpty) {
    const inherit = document.createElement("option");
    inherit.value = "";
    inherit.textContent = "Use stage model";
    select.append(inherit);
  }
  if (subscription) {
    appendModelGroup(
      select,
      "Available from your subscription — newest first",
      [...(payload.recommended || []), ...(payload.available || [])],
      provider,
    );
  } else {
    appendModelGroup(select, "Recommended for this stage", payload.recommended, provider);
    appendModelGroup(
      select,
      "Available from your account — newest first",
      payload.available,
      provider,
    );
  }
  const catalogValues = new Set([...select.options].map((option) => option.value));
  if (
    !subscription
    && preserveSelection
    && selectedValue
    && selectedValue.startsWith(`${provider}/`)
    && !catalogValues.has(selectedValue)
  ) {
    appendModelGroup(
      select,
      "Current selection — no longer listed",
      [{
        model_id: selectedValue,
        display_name: selectedLabel.includes("current selection")
          ? selectedLabel
          : `${selectedLabel} (current selection)`,
        compatibility: "unverified",
      }],
      provider,
    );
  }
  if (!subscription) {
    const other = document.createElement("option");
    other.value = "__other__";
    other.textContent = "Other model…";
    other.dataset.modelProvider = "other";
    select.append(other);
  }
  select.dataset.catalogProvider = provider;

  const values = new Set([...select.options].map((option) => option.value));
  if ((preserveSelection || (allowEmpty && !selectedValue)) && values.has(selectedValue)) {
    select.value = selectedValue;
  } else {
    const next = [...select.options].find((option) => option.value !== "");
    select.value = next?.value || "";
  }
  synchronizeModelReasoning(select);
};

const stageProviderChoice = (stage) => document.querySelector(
  `[data-provider-choice][data-provider-group="${stage}"]`,
);
const providerForModelSelect = (select) => {
  const group = select.dataset.modelStage === "stage1" ? "stage1" : "stage2";
  return stageProviderChoice(group)?.value || "";
};
const providerIsAuthenticated = (provider) => {
  if (provider === "custom") return true;
  if (authModeValue() === "api_key") {
    const card = credentialCards.find((candidate) => candidate.dataset.provider === provider);
    return card?.dataset.keyAvailable === "true";
  }
  const subscriptionProvider = subscriptionProviders[provider];
  const card = subscriptionCards.find(
    (candidate) => candidate.dataset.subscriptionProvider === subscriptionProvider,
  );
  return card?.dataset.subscriptionAuthenticated === "true";
};
const providerAuthInstruction = (provider) => {
  const label = providerLabels[provider] || "this provider";
  if (authModeValue() === "subscription") {
    const account = subscriptionProviders[provider];
    const accountLabel = account === "google"
      ? "Google"
      : account === "claude"
        ? "Claude"
        : account === "openai"
          ? "OpenAI"
          : label;
    return `Log in to ${accountLabel} to choose a model.`;
  }
  return `Add a ${label} API key to choose a model.`;
};
const resetModelSelect = (select, message) => {
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = message;
  select.append(placeholder);
  select.value = "";
  delete select.dataset.catalogProvider;
};
const modelReasoningControl = (select) => {
  if (typeof select.closest !== "function") return null;
  const pass = select.closest("[data-stage2-pass]");
  if (pass) return pass.querySelector('select[name$="_reasoning"]');
  const stage = select.closest(".stage1-settings");
  if (stage) return stage.querySelector('select[name$="_reasoning"]');
  const role = select.closest("[data-agentic-model-group]")?.dataset.agenticModelGroup;
  return role ? document.querySelector(`select[name="${role}_reasoning"]`) : null;
};
const gateModelSelect = (select, provider, {active = true, reset = false} = {}) => {
  const custom = select.parentElement.querySelector(
    "[data-custom-model], [data-agentic-custom-model]",
  );
  const hint = select.parentElement.querySelector("[data-model-auth-hint]");
  const reasoning = modelReasoningControl(select);
  if (reset && custom) custom.value = "";
  const authenticated = providerIsAuthenticated(provider);
  const instruction = providerAuthInstruction(provider);
  if (!authenticated) {
    resetModelSelect(select, instruction);
    select.hidden = false;
    select.disabled = true;
    if (custom) {
      custom.value = "";
      custom.hidden = true;
      custom.disabled = true;
      custom.required = false;
    }
    if (reasoning) reasoning.disabled = true;
    if (hint) {
      hint.hidden = false;
      hint.textContent = instruction;
    }
    return;
  }

  const manualEntry = provider === "custom";
  if (manualEntry) {
    resetModelSelect(select, "Enter a custom model below");
    select.hidden = true;
    select.disabled = true;
    if (custom) {
      custom.hidden = false;
      custom.disabled = !active;
      custom.required = active;
      custom.placeholder = customModelPlaceholder(provider);
    }
    if (reasoning) reasoning.disabled = !active;
    if (hint) hint.hidden = true;
    synchronizeModelReasoning(select);
    return;
  }

  const catalogReady = select.dataset.catalogProvider === provider;
  if (!catalogReady) resetModelSelect(select, `Loading ${providerLabels[provider]} models…`);
  select.hidden = false;
  select.disabled = !active || !catalogReady;
  if (custom) custom.placeholder = customModelPlaceholder(provider);
  if (reasoning) reasoning.disabled = !active || !catalogReady;
  if (hint) {
    hint.hidden = catalogReady;
    hint.textContent = catalogReady ? "" : `Loading ${providerLabels[provider]} models…`;
  }
  synchronizeCustomModel(select);
  synchronizeModelReasoning(select);
};

const modelCatalogTargets = () => {
  const targets = modelSelects.map((select) => ({
    select,
    provider: providerForModelSelect(select),
    stage: select.dataset.modelStage,
    authMode: authModeValue(),
  }));
  agenticModelGroups.forEach((group) => {
    if (group.closest("[data-agentic-settings]")?.hidden) return;
    const select = group.querySelector("[data-agentic-model]");
    const providerControl = group.querySelector("[data-agentic-provider]");
    if (!select) return;
    targets.push({
      select,
      provider: providerControl?.value,
      stage: select.dataset.modelStage,
      authMode: authModeValue(),
    });
  });
  return targets.filter((target) => (
    target.provider
    && target.provider !== "custom"
    && target.stage
    && providerIsAuthenticated(target.provider)
  ));
};

const catalogStatusMessage = (payloads) => {
  if (payloads.some((payload) => payload.stale)) {
    return "Provider unavailable — showing the last successful model list.";
  }
  if (payloads.some((payload) => payload.warning?.code === "authentication_required")) {
    return "Authenticate a provider to load models.";
  }
  if (payloads.some((payload) => payload.warning?.code === "provider_unavailable")) {
    return "Provider model discovery is unavailable.";
  }
  if (payloads.some((payload) => payload.source === "live")) {
    return "Models refreshed from your account.";
  }
  if (payloads.some((payload) => payload.source === "cached")) {
    return "Models loaded from the recent account cache.";
  }
  return "Authenticated model catalog loaded.";
};

const shouldPreserveModelSelection = (
  preserveSelection,
  resetProvider,
  targetProvider,
) => preserveSelection && resetProvider !== targetProvider;

const loadModelCatalog = async ({
  force = false,
  preserveSelection = true,
  resetProvider = null,
} = {}) => {
  const sequence = ++modelCatalogSequence;
  modelCatalogController?.abort();
  const targets = modelCatalogTargets();
  if (!targets.length) {
    if (modelStatus) modelStatus.textContent = "Authenticate a provider to load models.";
    return;
  }
  modelCatalogController = new AbortController();
  if (modelStatus) modelStatus.textContent = "Loading models…";

  const requests = new Map();
  targets.forEach((target) => {
    const key = `${target.provider}:${target.stage}:${target.authMode}`;
    if (!requests.has(key)) requests.set(key, {...target, targets: []});
    requests.get(key).targets.push(target.select);
  });

  try {
    const responses = await Promise.all(
      [...requests.values()].map(async (request) => {
        const url = new URL(
          `/models/${encodeURIComponent(request.provider)}`,
          window.location.origin,
        );
        url.searchParams.set("stage", request.stage);
        url.searchParams.set("auth_mode", request.authMode);
        if (force && request.authMode === "api_key") url.searchParams.set("force", "true");
        const response = await window.fetch(url, {
          headers: {"Accept": "application/json"},
          signal: modelCatalogController.signal,
        });
        const payload = await response.json().catch(() => null);
        if (
          !response.ok
          || !payload
          || payload.provider !== request.provider
          || payload.stage !== request.stage
        ) {
          throw new Error("invalid model catalog response");
        }
        return {request, payload};
      }),
    );
    if (sequence !== modelCatalogSequence) return;
    responses.forEach(({request, payload}) => {
      request.targets.forEach((select) => {
        applyModelCatalog(select, payload, request.provider, {
          preserveSelection: shouldPreserveModelSelection(
            preserveSelection,
            resetProvider,
            request.provider,
          ),
        });
      });
    });
    synchronizeModels();
    agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
    migrateStage2CachesForProvider();
    updateStage2Summary();
    if (modelStatus) {
      modelStatus.textContent = catalogStatusMessage(
        responses.map(({payload}) => payload),
      );
    }
  } catch (error) {
    if (error?.name === "AbortError" || sequence !== modelCatalogSequence) return;
    if (modelStatus) modelStatus.textContent = "Could not update authenticated models.";
  }
};

const synchronizeCredentialPanels = () => {
  const subscription = authModeValue() === "subscription";
  if (apiCredentialEntry) {
    apiCredentialEntry.hidden = subscription;
    apiCredentialEntry.disabled = subscription;
  }
  if (subscriptionEntry) {
    subscriptionEntry.hidden = !subscription;
    subscriptionEntry.disabled = !subscription;
  }
  credentialCards.forEach((card) => {
    card.hidden = subscription;
    card.querySelectorAll("input, button").forEach((control) => {
      control.disabled = subscription;
    });
  });
  subscriptionCards.forEach((card) => {
    card.hidden = !subscription;
    const login = card.querySelector("[data-subscription-login]");
    const logout = card.querySelector("[data-subscription-logout]");
    if (login) login.disabled = !subscription || card.dataset.subscriptionAvailable !== "true";
    if (logout) logout.disabled = !subscription || card.dataset.subscriptionRemovable !== "true";
  });
  if (customProviderNote) {
    customProviderNote.hidden = subscription
      || !providerChoices.some((choice) => choice.value === "custom");
  }
  if (modelRefresh) modelRefresh.hidden = modelCatalogTargets().length === 0;
};

const synchronizeModels = (providerChanged = false, changedGroup = null) => {
  providerChoices.forEach((choice) => {
    const inactive = Boolean(choice.closest("[data-stage-control]")?.hidden);
    choice.disabled = inactive;
  });
  modelSelects.forEach((select) => {
    const group = select.dataset.modelStage === "stage1" ? "stage1" : "stage2";
    const provider = providerForModelSelect(select);
    const active = !select.closest("[data-stage-control]")?.hidden;
    gateModelSelect(select, provider, {
      active,
      reset: providerChanged && (!changedGroup || changedGroup === group),
    });
  });
  if (openRouterProvider) {
    const visible = authModeValue() === "api_key"
      && providerChoices.some((choice) => choice.value === "openrouter");
    openRouterProvider.hidden = !visible;
    openRouterProvider.querySelectorAll("input").forEach((input) => {
      input.disabled = !visible;
    });
  }
  synchronizeCredentialPanels();
};

const synchronizeProviderChoices = () => {
  const subscription = authModeValue() === "subscription";
  const controls = [
    ...providerChoices,
    ...agenticModelGroups.map((group) => group.querySelector("[data-agentic-provider]")),
  ].filter(Boolean);
  controls.forEach((control) => {
    [...control.options].forEach((option) => {
      const unavailable = subscription
        && !["anthropic", "openai", "gemini"].includes(option.value);
      option.hidden = unavailable;
      option.disabled = unavailable;
    });
    if (control.selectedOptions[0]?.disabled) {
      control.value = "gemini";
    }
  });
};

const synchronizeAuth = (providerChanged = false) => {
  const subscription = authModeValue() === "subscription";
  synchronizeProviderChoices();
  if (authSummary) authSummary.textContent = subscription ? "Subscriptions" : "API keys";
  if (billingSummary) {
    billingSummary.textContent = subscription ? "Subscription billing" : "API-key billing";
  }
  synchronizeModels(providerChanged);
  agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group, providerChanged));
};

const synchronizeAgenticModelGroup = (group, providerChanged = false) => {
  const providerControl = group.querySelector("[data-agentic-provider]");
  const select = group.querySelector("[data-agentic-model]");
  if (!providerControl || !select) return;
  const agenticEnabled = !group.closest("[data-agentic-settings]")?.hidden;
  providerControl.disabled = !agenticEnabled;
  [...providerControl.options].forEach((option) => {
    const unavailable = authModeValue() === "subscription"
      && !["anthropic", "openai", "gemini"].includes(option.value);
    option.hidden = unavailable;
    option.disabled = unavailable;
  });
  gateModelSelect(select, providerControl.value, {
    active: agenticEnabled,
    reset: providerChanged,
  });
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
  if (stage2Explanation) stage2Explanation.hidden = !split;
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
    loadModelCatalog({preserveSelection: false});
  });
  group.querySelector("[data-agentic-model]")?.addEventListener("change", (event) => {
    synchronizeAgenticModelGroup(group);
    synchronizeModelReasoning(event.currentTarget);
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
  synchronizeInstructionPanels();
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
    synchronizeModelReasoning(select);
  });
});
providerChoices.forEach((choice) => {
  choice.addEventListener("change", () => {
    const group = choice.dataset.providerGroup;
    synchronizeModels(true, group);
    loadModelCatalog({preserveSelection: false});
    if (group === "stage2") migrateStage2CachesForProvider();
    updateStage2Summary();
  });
});

authModeChoices.forEach((choice) => {
  choice.addEventListener("change", () => {
    synchronizeAuth(true);
    loadModelCatalog({preserveSelection: false});
    migrateStage2CachesForProvider();
    updateStage2Summary();
    persistRunForm();
  });
});
modelRefresh?.addEventListener("click", () => {
  loadModelCatalog({force: true});
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
  if (enabled) {
    synchronizePipeline();
    loadModelCatalog();
  }
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
instructionPanels.forEach(wireInstructionPanel);
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
synchronizeAuth();
synchronizePipeline();
synchronizeAgentic();
synchronizeManual();
loadModelCatalog();

const beginWizardInvalidAttempt = () => {
  firstInvalidWizardField = null;
  invalidWizardFields = [];
};

const scheduleWizardInvalidAttemptReset = () => {
  if (invalidAttemptResetScheduled) return;
  invalidAttemptResetScheduled = true;
  queueMicrotask(() => {
    wizardPanels.forEach((panel) => {
      const panelInvalidFields = invalidWizardFields.filter(
        (field) => wizardPanelForField(field) === panel,
      );
      updateWizardValidationSummary(panel, panelInvalidFields);
    });
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
  wizard.querySelectorAll("[data-wizard-go]").forEach((button) => {
    button.addEventListener("click", () => {
      const step = button.dataset.wizardGo;
      if (!wizardOrder.includes(step)) return;
      persistRunForm();
      setWizardStep(step);
    });
  });


  wizard.querySelectorAll("[data-wizard-next]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextStep = button.dataset.wizardNext;
      if (!wizardOrder.includes(nextStep)) return;
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
    button.addEventListener("click", beginWizardInvalidAttempt);
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
        synchronizeModels();
        agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
        loadModelCatalog({force: true});
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
    synchronizeModels();
    agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
    loadModelCatalog({resetProvider: provider});
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

const applySubscriptionStatus = (card, payload) => {
  if (!card || !payload || typeof payload !== "object") return false;
  const authenticated = payload.authenticated === true;
  const available = payload.available === true;
  const credentialPresent = payload.credential_present === true
    || authenticated
    || (typeof payload.expires_at === "string" && payload.expires_at.trim() !== "");
  const removable = payload.removable === true;
  const category = String(payload.category || "missing");
  card.dataset.subscriptionAuthenticated = String(authenticated);
  card.dataset.subscriptionCredentialPresent = String(credentialPresent);
  card.dataset.subscriptionRemovable = String(removable);
  card.dataset.subscriptionAvailable = String(available);
  const state = card.querySelector("[data-subscription-state]");
  if (state) {
    state.textContent = !available
      ? "Unavailable"
      : (authenticated ? "Authenticated" : (credentialPresent ? "Session expired" : "Log in required"));
  }
  const categoryElement = card.querySelector("[data-subscription-category]");
  if (categoryElement) categoryElement.textContent = category;
  const account = card.querySelector("[data-subscription-account]");
  if (account) account.textContent = String(payload.account_label || "None");
  const expiry = card.querySelector("[data-subscription-expiry]");
  if (expiry) expiry.textContent = String(payload.expires_at || "None");
  const login = card.querySelector("[data-subscription-login]");
  const logout = card.querySelector("[data-subscription-logout]");
  if (login) login.disabled = !available;
  if (logout) logout.disabled = !removable;
  return true;
};

const refreshSubscriptionCard = async (card, provider) => {
  try {
    const response = await window.fetch(`/subscriptions/${provider}/status`, {
      headers: { "Accept": "application/json" },
    });
    if (!response.ok) throw new Error("status unavailable");
    const payload = await response.json();
    if (!applySubscriptionStatus(card, payload)) throw new Error("invalid status");
    synchronizeCredentialPanels();
    synchronizeModels();
    agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
    return true;
  } catch (_error) {
    const status = card.querySelector("[data-subscription-action-status]");
    if (status) status.textContent = "Subscription status unavailable";
    return false;
  }
};

const waitForSubscriptionLogin = async (card, provider, attempts = 60) => {
  if (attempts <= 0) {
    const status = card.querySelector("[data-subscription-action-status]");
    if (status) status.textContent = "Login is still pending; refresh status to check it.";
    return;
  }
  await new Promise((resolve) => window.setTimeout(resolve, 1000));
  const completed = await refreshSubscriptionCard(card, provider);
  if (!completed) return;
  if (card.dataset.subscriptionAuthenticated === "true") {
    const status = card.querySelector("[data-subscription-action-status]");
    if (status) status.textContent = "Subscription authenticated.";
    loadModelCatalog();
    return;
  }
  await waitForSubscriptionLogin(card, provider, attempts - 1);
};

const subscriptionLoginErrorMessage = (
  payload,
  fallback = "Subscription login failed.",
) => {
  const message = payload?.message;
  return typeof message === "string" && message.trim() ? message : fallback;
};

subscriptionCards.forEach((card) => {
  const provider = card.dataset.subscriptionProvider;
  const actionStatus = card.querySelector("[data-subscription-action-status]");
  const login = card.querySelector("[data-subscription-login]");
  const logout = card.querySelector("[data-subscription-logout]");
  login?.addEventListener("click", async () => {
    if (!provider) return;
    login.disabled = true;
    if (actionStatus) actionStatus.textContent = "Opening provider login…";
    let popup;
    try {
      popup = window.open("about:blank", `mudidi-subscription-${provider}`);
    } catch (_error) {
      popup = null;
    }
    if (!popup) {
      if (actionStatus) actionStatus.textContent = "Allow pop-ups to continue provider login.";
      login.disabled = card.dataset.subscriptionAvailable !== "true";
      return;
    }
    try {
      popup.opener = null;
      const response = await window.fetch(`/subscriptions/${provider}/login`, {
        method: "POST",
        headers: { "Accept": "application/json" },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(subscriptionLoginErrorMessage(payload));
      }
      if (typeof payload?.launch_url !== "string" || !payload.launch_url.trim()) {
        throw new Error("Login URL unavailable.");
      }
      let launchUrl;
      try {
        launchUrl = new URL(
          payload.launch_url,
          window.location.origin,
        );
      } catch (_error) {
        throw new Error("Login URL unavailable.");
      }
      if (!["http:", "https:"].includes(launchUrl.protocol)) {
        throw new Error("Login URL is not a web URL");
      }
      if (launchUrl.origin !== window.location.origin) {
        throw new Error("Login URL must stay on this server");
      }
      popup.location.href = launchUrl.href;
      if (actionStatus) actionStatus.textContent = "Complete login in the browser window…";
      await waitForSubscriptionLogin(card, provider);
    } catch (error) {
      try {
        popup.close();
      } catch (_closeError) {
        // The provider may have closed the window already.
      }
      if (actionStatus) {
        actionStatus.textContent = error instanceof Error && error.message
          ? error.message
          : "Subscription login failed.";
      }
      login.disabled = card.dataset.subscriptionAvailable !== "true";
    }
  });
  logout?.addEventListener("click", async () => {
    if (!provider) return;
    logout.disabled = true;
    if (actionStatus) actionStatus.textContent = "Logging out…";
    try {
      const response = await window.fetch(`/subscriptions/${provider}/logout`, {
        method: "POST",
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) throw new Error("logout unavailable");
      const payload = await response.json();
      if (!applySubscriptionStatus(card, payload)) throw new Error("invalid status");
      if (actionStatus) actionStatus.textContent = "Subscription logged out.";
      loadModelCatalog();
      synchronizeModels();
      agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
    } catch (_error) {
      if (actionStatus) actionStatus.textContent = "Subscription logout failed.";
      logout.disabled = false;
    }
  });
});
