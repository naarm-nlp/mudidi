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

const pipelineSelect = document.querySelector("[data-pipeline-select]");
const providerSelect = document.querySelector("[data-provider-select]");
const modelSelects = [...document.querySelectorAll("[data-model-select]")];
const openRouterProvider = document.querySelector("[data-openrouter-provider]");
const pipelineStages = {
  complete: new Set(["stage1", "pass1", "pass2"]),
  transcription: new Set(["stage1"]),
  structure: new Set(["pass1", "pass2"]),
  discover_rules: new Set(["pass1"]),
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
  if (!pipelineSelect) return;
  const active = pipelineStages[pipelineSelect.value] || new Set();
  document.querySelectorAll("[data-stage-control]").forEach((control) => {
    const stages = control.dataset.pipelineStages.split(/\s+/);
    const visible = stages.some((stage) => active.has(stage));
    control.hidden = !visible;
    control.querySelectorAll("input, select").forEach((input) => {
      input.disabled = !visible;
    });
  });
  synchronizeModels();
};

modelSelects.forEach((select) => {
  select.addEventListener("change", () => synchronizeCustomModel(select));
});
if (providerSelect) {
  providerSelect.addEventListener("change", () => synchronizeModels(true));
}
if (pipelineSelect) pipelineSelect.addEventListener("change", synchronizePipeline);
synchronizePipeline();

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
