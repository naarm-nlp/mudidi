"use strict";

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;

  const kind = button.dataset.add;
  if (kind) {
    const template = document.querySelector(`#${kind}-template`);
    const rows = document.querySelector(`#${kind}-rows`);
    if (template && rows) rows.append(template.content.cloneNode(true));
    return;
  }

  if (button.hasAttribute("data-remove")) {
    const row = button.closest(".editor-row");
    if (row) row.remove();
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

document.querySelectorAll(".info-button").forEach((button) => {
  button.addEventListener("click", () => button.classList.toggle("is-open"));
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
synchronizePipeline();
synchronizeAgentic();
synchronizeManual();

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
