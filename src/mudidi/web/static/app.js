"use strict";

const runForm = document.querySelector("form.run-form");
const runFormStorageKey = "mudidi:new-run-form:v1";
const presetStateElement = document.querySelector("#preset-form-state");

const persistRunForm = () => {
  if (!runForm) return;
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
const providerSelect = document.querySelector("[data-provider-select]");
const modelSelects = [...document.querySelectorAll("[data-model-select]")];
const openRouterProvider = document.querySelector("[data-openrouter-provider]");
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
const customModelPlaceholder = (provider) => provider === "openrouter"
  ? "e.g. qwen/qwen3-235b-a22b"
  : `Enter a model name supported by ${providerLabels[provider] || "your selected provider"}`;

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
  if (!providerSelect) return;
  const provider = providerSelect.value;
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
    control.querySelectorAll("input, select").forEach((input) => {
      input.disabled = !visible;
    });
  });
  [
    ["verify_stage1", active.has("stage1")],
    ["verify_stage2", active.has("pass1") || active.has("pass2")],
  ].forEach(([name, enabled]) => {
    const input = document.querySelector(`input[name="${name}"]`);
    if (!input) return;
    input.disabled = !enabled;
    if (!enabled) input.checked = false;
    else if (input.dataset.userTouched !== "true") input.checked = true;
  });
  synchronizeModels();
  if (typeof synchronizeManual === "function") synchronizeManual();
};

modelSelects.forEach((select) => {
  select.addEventListener("change", () => synchronizeCustomModel(select));
});
if (providerSelect) {
  providerSelect.addEventListener("change", () => synchronizeModels(true));
}
pipelineChoices.forEach((choice) => choice.addEventListener("change", synchronizePipeline));

document.querySelectorAll('input[name="verify_stage1"], input[name="verify_stage2"]').forEach((input) => {
  input.addEventListener("change", () => {
    input.dataset.userTouched = "true";
  });
});

const agenticChoices = [...document.querySelectorAll('input[name="agentic"]')];
const agenticSettings = document.querySelector("[data-agentic-settings]");
const synchronizeAgentic = () => {
  if (!agenticSettings) return;
  const enabled = agenticChoices.some((choice) => choice.checked && choice.value === "true");
  agenticSettings.hidden = !enabled;
  if (enabled) agenticSettings.open = true;
  agenticSettings.querySelectorAll("input, select, textarea").forEach((input) => {
    const stageDisabled = input.name === "verify_stage1" || input.name === "verify_stage2";
    input.disabled = !enabled || (stageDisabled && input.disabled);
  });
  if (enabled) {
    synchronizePipeline();
    agenticModelGroups.forEach((group) => synchronizeAgenticModelGroup(group));
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
if (presetStateElement && otherInformationToggle) {
  otherInformationToggle.dispatchEvent(new Event("change"));
}
synchronizePipeline();
synchronizeAgentic();
synchronizeManual();
if (runForm) {
  runForm.addEventListener("input", (event) => {
    persistRunForm();
    if (event.target.validity?.valid) {
      const section = event.target.closest(".dropzone, label, .input-row, fieldset");
      if (section && !section.hasAttribute("data-field-error")) {
        section.classList.remove("field-invalid");
      }
    }
  });
  runForm.addEventListener("change", persistRunForm);
  runForm.addEventListener("invalid", (event) => {
    runForm.classList.add("was-validated");
    const invalidSection = event.target.closest(".dropzone, label, .input-row, fieldset");
    if (invalidSection) invalidSection.classList.add("field-invalid");
    const details = event.target.closest("details");
    if (details) details.open = true;
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
      input.placeholder = "Saved key — leave blank to keep it";
      status.textContent = "Saved";
    } catch (_error) {
      status.textContent = "Could not save key";
    } finally {
      button.disabled = false;
    }
  });
});

const liveRun = document.querySelector('meta[name="mudidi-events"]');
if (liveRun && window.EventSource) {
  const eventSource = new EventSource(liveRun.content);
  ["stage.started", "page.started", "page.completed", "parse_rules.generated", "run.completed", "run.failed"].forEach((eventName) => {
    eventSource.addEventListener(eventName, () => {
      eventSource.close();
      window.location.reload();
    });
  });
}

const pageSlider = document.querySelector("[data-page-slider]");
if (pageSlider) {
  const position = document.querySelector("[data-page-position]");
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

const pageTextEditor = document.querySelector("form.page-text-editor");
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
