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

const synchronizeCustomModel = (select) => {
  const custom = select.parentElement.querySelector("[data-custom-model]");
  if (!custom) return;
  const active = !select.closest("[data-stage-control]").hidden;
  const customSelected = select.value === "__other__";
  custom.hidden = !customSelected;
  custom.disabled = !active || !customSelected;
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
      custom.placeholder = provider === "openrouter"
        ? "e.g. qwen/qwen3-235b-a22b"
        : "Enter a LiteLLM-compatible model name";
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

const manualChoices = [...document.querySelectorAll('input[name="mdf_manual_source"]')];
const customManual = document.querySelector("[data-custom-mdf-manual]");
const synchronizeManual = () => {
  if (!customManual) return;
  const selected = manualChoices.find((choice) => choice.checked);
  const visible = selected && selected.value === "upload" && !selected.disabled;
  customManual.hidden = !visible;
  customManual.querySelectorAll("input").forEach((input) => {
    input.disabled = !visible;
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
  runForm.addEventListener("input", persistRunForm);
  runForm.addEventListener("change", persistRunForm);
  runForm.addEventListener("submit", persistRunForm);
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
      const response = await window.fetch(`/providers/${provider}/credential/reveal`, {
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

const liveRun = document.querySelector('meta[name="mudidi-events"]');
if (liveRun && window.EventSource) {
  const eventSource = new EventSource(liveRun.content);
  ["parse_rules.generated", "run.completed", "run.failed"].forEach((eventName) => {
    eventSource.addEventListener(eventName, () => {
      eventSource.close();
      window.location.reload();
    });
  });
}
