# MUDIDI Dashboard UI Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved five-step New Run wizard and apply the same accessible, responsive brutalist component system to every dashboard page without changing inference, validation, credential, or run-lifecycle semantics.

**Architecture:** Keep FastAPI and server-rendered Jinja authoritative. `home.html` remains one multipart form; `app.js` owns four client-side wizard panels and submits the complete form to `/runs/preview`, whose server-rendered response is Step 5. Shared Stage 2 controls synchronize the existing Pass 1 and Pass 2 fields instead of adding backend fields. Shared shell, table, status, progress, form, and empty-state primitives live in `app.css`; `theme.js` and its `mudidi:theme` contract remain unchanged.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, Pydantic, SQLite, plain JavaScript, plain CSS, pytest/TestClient, Chromium browser verification.

**Spec:** `docs/superpowers/specs/2026-09-06-mudidi-dashboard-ui-revamp-design.md`

## Global Constraints

- Work only in the `features/brutalist-dashboard-reskin` worktree.
- Preserve all existing endpoint paths, multipart field names, Pydantic validation, run-state transitions, model catalog behavior, credential encryption/reveal APIs, CSP, and local-only security boundaries.
- Keep exactly one successful `provider` form value. Stage-level provider selectors are synchronized browser presentations of that run-level value, not new provider fields.
- Preserve `sessionStorage` form restoration under `mudidi:new-run-form:v1`; do not store file or password values. Store only wizard presentation state separately if needed.
- Keep the synchronous theme bootstrap and `mudidi:theme` storage behavior unchanged.
- Keep wizard code in `src/mudidi/web/static/app.js`; do not add a frontend framework, bundler, or JavaScript test dependency.
- Preserve semantic headings, fieldsets, labels, native controls, focus order, and server-side validation. JavaScript augments these contracts; it does not replace them.
- Retain every one of the fourteen page templates and their `_layout.html` inheritance. Do not leave old and new markup variants or compatibility aliases after migration.
- Use LSP references before changing exported Python symbols such as `RunArtifact` or `UsageSummary`.
- Use `?v=dashboard-ui-1` consistently for `app.css` and every template that loads the changed `app.js`; do not leave page-specific stale cache keys.
- Follow red-green-refactor for observable contracts. For client-only behavior, use browser automation against the real dashboard as the behavioral check rather than source-text assertions.
- Run `git diff --check` before every commit. Commit each completed, verified task before starting the next.

---

### Task 1: Establish the shared shell and visual primitives

**Files:**
- Modify: `tests/web/test_theme.py`
- Modify: `src/mudidi/web/templates/_layout.html`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Tighten the shared-shell contract test**

Update `test_every_page_renders_the_shared_shell` and `test_nav_marks_only_the_current_section_active` so they require a named application shell, a skip link, and `aria-current="page"` on the active destination:

```python
assert 'class="skip-link" href="#main-content"' in response.text
assert 'class="app-shell"' in response.text
assert '<main id="main-content" tabindex="-1">' in response.text
assert 'href="/history" aria-current="page"' in response.text
```

Keep the existing assertions for the shared sidebar, primary navigation, local status, and theme toggle. Update the stylesheet assertion to continue guarding the three-pixel brutalist border, hard shadow, dark token family, and absence of rounded corners.

- [ ] **Step 2: Run the shell test and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_theme.py -q
```

Expected: the new skip-link, `app-shell`, `main-content`, or `aria-current` assertion fails against the current layout.

- [ ] **Step 3: Implement the semantic shell**

In `_layout.html`:

- add the skip link as the first focusable body child;
- wrap the sidebar and main landmark in `.app-shell`;
- add `id="main-content" tabindex="-1"` to `<main>`;
- emit `aria-current="page"` only on the active global destination;
- bump the stylesheet query string to `?v=dashboard-ui-1`;
- leave `theme.js?v=brutalist-1` synchronous in `<head>`.

Use the following shape rather than creating a second layout:

```html
<a class="skip-link" href="#main-content">Skip to content</a>
<div class="app-shell">
  <aside class="sidebar">…</aside>
  <main id="main-content" tabindex="-1">{% block main %}{% endblock %}</main>
</div>
```

- [ ] **Step 4: Add reusable CSS primitives**

Extend `app.css` without retaining parallel legacy aliases. Add an eight-pixel spacing scale, bounded page header/workspace classes, control defaults, contained table overflow, status variants, task cards, action bars, and empty states. Keep existing token names used by tests and pages.

The control baseline must include:

```css
button,
input,
select,
textarea {
  min-width: 0;
  font: inherit;
}

select {
  min-height: 42px;
  padding: 10px 48px 10px 12px;
  appearance: none;
  background-color: var(--color-panel);
  background-image:
    linear-gradient(45deg, transparent 50%, var(--color-ink) 50%),
    linear-gradient(135deg, var(--color-ink) 50%, transparent 50%);
  background-position:
    calc(100% - 21px) 50%,
    calc(100% - 16px) 50%;
  background-repeat: no-repeat;
  background-size: 5px 5px, 5px 5px;
}

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
}
```

Hard shadows belong on primary panels, selected navigation, and actionable controls; nested groups use structural borders only. Keep square corners everywhere.

- [ ] **Step 5: Verify shell behavior and appearance**

Run:

```bash
uv run --locked pytest tests/web/test_theme.py -q
```

Expected: all theme tests pass.

Start the real dashboard and inspect `/`, `/history`, `/presets`, and `/active` at 1568 and 390 pixels in both themes. Confirm the skip link becomes visible on focus, the active nav item is unambiguous, the compact navigation does not create document overflow, and theme switching does not flash the wrong palette.

- [ ] **Step 6: Commit**

```bash
git diff --check
git add tests/web/test_theme.py src/mudidi/web/templates/_layout.html src/mudidi/web/static/app.css
git commit -m "Build shared dashboard UI primitives"
```

---

### Task 2: Build the New Run wizard shell and step validation

**Files:**
- Modify: `tests/web/test_app.py`
- Modify: `tests/web/test_dashboard_simplification.py`
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add server-rendered wizard contract tests**

Update the home-page tests to require five step markers, four client-owned panels, and the existing server review action:

```python
assert 'data-new-run-wizard' in response.text
for step in ("input", "pipeline", "model", "agentic"):
    assert f'id="wizard-{step}"' in response.text
    assert f'data-wizard-panel="{step}"' in response.text
assert response.text.count("data-wizard-marker=") == 5
assert 'action="/runs/preview"' in response.text
assert 'data-wizard-submit' in response.text
```

Retain the existing assertions for all input names, retired-field absence, model catalog options, defaults, and help text. Replace assertions tied only to the old long-form `<details>` layout with assertions for the approved panel ownership and ordering.

Add one rejected-preview assertion proving a server error remains attached to the owning panel:

```python
assert response.status_code == 422
assert 'data-wizard-panel="input"' in response.text
assert 'data-field-error="dictionary_pages"' in response.text
```

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_app.py tests/web/test_dashboard_simplification.py -q
```

Expected: wizard-marker and panel assertions fail while existing form-field assertions remain green.

- [ ] **Step 3: Recompose `home.html` as one four-panel form**

Keep one `<form method="post" action="/runs/preview" enctype="multipart/form-data">`. Add a five-marker stepper above it and four panel sections inside it:

```html
<ol class="wizard-stepper" aria-label="New run progress">
  <li data-wizard-marker="input" aria-current="step">…</li>
  <li data-wizard-marker="pipeline">…</li>
  <li data-wizard-marker="model">…</li>
  <li data-wizard-marker="agentic">…</li>
  <li data-wizard-marker="review" aria-disabled="true">…</li>
</ol>

<form class="panel run-form" data-new-run-wizard …>
  <section id="wizard-input" data-wizard-panel="input" aria-labelledby="wizard-input-title">…</section>
  <section id="wizard-pipeline" data-wizard-panel="pipeline" aria-labelledby="wizard-pipeline-title" hidden>…</section>
  <section id="wizard-model" data-wizard-panel="model" aria-labelledby="wizard-model-title" hidden>…</section>
  <section id="wizard-agentic" data-wizard-panel="agentic" aria-labelledby="wizard-agentic-title" hidden>…</section>
</form>
```

Panel ownership:

- Input: dictionary PDF, output directory, dictionary pages, introduction pages, additional context, MDF inputs, Dictionary Profile.
- Pipeline: three task-card radios and existing output policy radios.
- Model: credentials, provider/model/reasoning, temperature, batch size.
- Agentic: Off/On task cards and advanced verification settings.
- Review: the marker only; `/runs/preview` renders the real Step 5 page.

Rebuild Dictionary Profile with a borderless semantic fieldset, explicit section headings, and `minmax(0, 1fr) minmax(0, 1fr) auto` language rows. Keep the existing `<template>` and field names so session restoration and add/remove behavior continue to work.

- [ ] **Step 4: Implement wizard navigation and current-step validation in `app.js`**

Preserve the existing form persistence, model synchronization, agentic controls, dynamic rows, and credential handlers. Add wizard state around them:

```javascript
const wizard = document.querySelector("[data-new-run-wizard]");
const wizardPanels = wizard ? [...wizard.querySelectorAll("[data-wizard-panel]")] : [];
const wizardOrder = ["input", "pipeline", "model", "agentic"];
let activeWizardStep = "input";

const setWizardStep = (step, { focus = true } = {}) => {
  if (!wizardOrder.includes(step)) return;
  activeWizardStep = step;
  wizardPanels.forEach((panel) => {
    panel.hidden = panel.dataset.wizardPanel !== step;
  });
  document.querySelectorAll("[data-wizard-marker]").forEach((marker) => {
    const markerIndex = [...marker.parentElement.children].indexOf(marker);
    const activeIndex = wizardOrder.indexOf(step);
    marker.toggleAttribute("aria-current", marker.dataset.wizardMarker === step);
    marker.classList.toggle("is-complete", markerIndex < activeIndex);
  });
  if (focus) document.querySelector(`#wizard-${step}-title`)?.focus();
};

const validateWizardPanel = (panel) => {
  const fields = [...panel.querySelectorAll("input, select, textarea")]
    .filter((field) => !field.disabled && !field.closest("[hidden]"));
  const firstInvalid = fields.find((field) => !field.checkValidity());
  if (!firstInvalid) return true;
  markWizardFieldInvalid(firstInvalid);
  firstInvalid.focus();
  return false;
};
```

Requirements:

- Continue validates only enabled, visible fields in the active panel.
- Back never validates or clears values.
- Successful navigation focuses the target panel heading, which has `tabindex="-1"`.
- Client validation creates or updates adjacent `.field-error-message` text from `validationMessage` and updates an active-panel summary when multiple controls fail.
- The first server-rendered `[data-field-error]` determines the initial panel after a 422 response.
- The form-level `invalid` capture handler opens the owning wizard panel before focusing an invalid field.
- `synchronizePipeline()` disables inactive pipeline controls but never disables a field merely because its wizard panel is hidden.
- Persist the active step in a separate session key such as `mudidi:new-run-wizard:v1`; never add file or password values to storage.

- [ ] **Step 5: Style the stepper, panels, profile rows, and mobile state**

Implement separate marker and label blocks so connector lines occupy only the gap between markers. Completed markers must include a non-color cue. At `max-width: 600px`, hide step titles/descriptions but keep all five numbered markers and their accessible names. Ensure wizard actions stack and the profile remove column stays within the panel at 390 pixels.

- [ ] **Step 6: Verify wizard behavior in the real browser**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_app.py tests/web/test_dashboard_simplification.py -q
```

Expected: JavaScript syntax check and focused pytest files pass.

In Chromium:

1. Attempt to continue without a PDF and dictionary pages; confirm Input stays active, the first invalid control receives focus, and adjacent error text appears.
2. Enter values, move Input → Pipeline → Input, and confirm values remain.
3. Select each pipeline and confirm only applicable context/model controls are enabled.
4. Submit a server-invalid page range and confirm the 422 response reopens Input with the entered non-file values and inline server error visible.
5. At 390 pixels, assert `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

- [ ] **Step 7: Commit**

```bash
git diff --check
git add tests/web/test_app.py tests/web/test_dashboard_simplification.py src/mudidi/web/templates/home.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Build the New Run wizard shell"
```

---

### Task 3: Implement Model step synchronization and credential management

**Files:**
- Modify: `tests/web/test_app.py`
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add Model-step rendering contracts**

Require the approved structure while retaining all existing model and credential field assertions:

```python
assert 'name="provider"' in response.text
assert 'data-provider-value' in response.text
assert response.text.count("data-provider-choice") >= 2
assert 'data-stage2-mode="shared"' in response.text
assert 'data-stage2-toggle' in response.text
assert "Advanced · split passes" in response.text
assert 'data-stage2-pass="pass1"' in response.text
assert 'data-stage2-pass="pass2"' in response.text
assert 'data-selected-credential' in response.text
assert 'data-other-credentials' in response.text
```

Also retain the exact Stage 2 explanation assertions: Pass 1 infers the dictionary-specific MDF parsing guide from representative pages for review; Pass 2 applies the approved guide to each transcription.

- [ ] **Step 2: Run the focused test and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_app.py::test_home_page_exposes_primary_local_workflow tests/web/test_app.py::test_home_explains_each_pipeline_model_role -q
```

Expected: the new provider-mirror, shared-mode, and credential-disclosure assertions fail.

- [ ] **Step 3: Build synchronized run-level provider presentations**

Render exactly one successful provider field:

```html
<input type="hidden" name="provider" value="gemini" data-provider-value>
<select data-provider-choice aria-label="Stage 1 provider">…</select>
<select data-provider-choice aria-label="Stage 2 provider">…</select>
```

Only display a provider selector in a stage that the active pipeline uses. Every visible selector updates the hidden run-level value and every other selector; then call the existing model filtering and custom-model synchronization once. `new FormData(runForm).getAll("provider")` must always contain one item.

Place credential cards in one DOM collection with unique input IDs. Move the selected provider's card into `[data-selected-credential]`; move the remaining cards into the **Manage keys** disclosure. Moving nodes, rather than duplicating markup, preserves unique IDs, input state, reveal state, and status live regions.

- [ ] **Step 4: Implement shared and advanced Stage 2 state**


Refactor model activation so it depends on the selected pipeline stage, not on whether a pass card is visually hidden. In shared mode, a hidden Pass 2 card still needs enabled transport values; custom-model inputs must be enabled exactly when their synchronized model value is `__other__`.
Keep the existing named Pass 1 and Pass 2 controls. In shared mode, present the Pass 1 control as **Stage 2 model/reasoning**, hide the Pass 2 card, and continuously copy model, custom-model, and reasoning values into Pass 2 before persistence and submission. The hidden Pass 2 values remain coherent transport values, not independently editable pass state.

Use explicit cached presentation state:

```javascript
const stage2State = {
  mode: "shared",
  shared: readStage2Pass("pass1"),
  split: {
    pass1: readStage2Pass("pass1"),
    pass2: readStage2Pass("pass2"),
  },
};

const enterSplitStage2 = () => {
  stage2State.shared = readStage2Pass("pass1");
  writeStage2Pass("pass1", stage2State.shared);
  writeStage2Pass("pass2", stage2State.shared);
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
```

Additional invariants:

- Entering split mode copies the current shared model, custom model, and reasoning into both passes.
- Split mode permits independent Pass 1 and Pass 2 values.
- Returning to shared restores the previous shared values and immediately writes them to both backend fields.
- Returning to split restores the cached pass-specific values from the prior split visit.
- A restored preset/session with unequal Pass 1 and Pass 2 values starts in split mode so existing expert configuration is never collapsed.
- Persist only the `shared`/`split` presentation choice in the wizard session key; existing named field persistence continues through `persistRunForm()`.
- The toggle label and `aria-expanded` state change between **Advanced · split passes** and **Use one Stage 2 model**.
- The sticky summary shows **Shared model** plus one model in shared mode, or **Separate pass models** plus both models in split mode.

- [ ] **Step 5: Add credential deletion and selected-provider state**

Add a small destructive action only for stored keys:

```html
<button type="button" data-delete-key data-provider="{{ provider }}">
  Remove saved key
</button>
```

In `app.js`, call the existing `POST /credentials/{provider}/delete` endpoint, clear the input without revealing the deleted value, set its status region to `Not saved`, and update the selected-provider badge. Keep reveal and save requests same-origin and preserve the existing no-secret-in-markup behavior.

- [ ] **Step 6: Verify Model behavior with browser automation**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_app.py tests/web/test_models_credentials.py -q
```

Expected: focused tests pass.

In Chromium, automate these assertions on Step 3:

```javascript
const formData = new FormData(document.querySelector("form.run-form"));
const providers = formData.getAll("provider");
if (providers.length !== 1) throw new Error("provider must be singular");
```

Then:

1. Change the shared Stage 2 model/reasoning; assert both named Pass fields match.
2. Enter split mode; assert both passes received the shared values.
3. Make Pass 1 and Pass 2 different; assert the summary lists both.
4. Return to shared; assert the old shared values are restored and `FormData` contains equal Pass 1/Pass 2 values.
5. Return to split; assert the prior independent values reappear.
6. Change either visible provider selector; assert all provider presentations, credential card, model options, and the single submitted provider value synchronize.
7. Save, reveal, hide, and delete a disposable local key; confirm status text changes and no secret appears in page source or logs.
8. Confirm the eye icon button and glyph centers have zero horizontal and vertical delta.

- [ ] **Step 7: Commit**

```bash
git diff --check
git add tests/web/test_app.py src/mudidi/web/templates/home.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Add shared and split Stage 2 controls"
```

---

### Task 4: Complete Agentic, Review, credential-blocked, and error states

**Files:**
- Modify: `tests/web/test_app.py`
- Modify: `tests/web/test_production_routes.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `src/mudidi/web/templates/home.html`
- Modify: `src/mudidi/web/templates/review.html`
- Modify: `src/mudidi/web/templates/credential_required.html`
- Modify: `src/mudidi/web/templates/form_error.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add rendering tests for the remaining workflow states**

Update tests to require:

- Agentic **Off** and **On** task-card radios.
- Advanced verification hidden and disabled while Off.
- Review sections named Input, Pipeline, Model, and Agentic.
- A right-side readiness/checkpoint panel and secondary preset form.
- Credential-required markup with exactly one provider credential card, Save and Continue, and Return to Review.
- Validation/error pages with `role="alert"`, a concise issue list, and a safe return action.

Example review assertions:

```python
for heading in ("Input", "Pipeline", "Model", "Agentic"):
    assert f">{heading}<" in response.text
assert 'class="review-actions panel"' in response.text
assert "Start run" in response.text
assert "Save these non-secret settings as a preset" in response.text
```

Keep the existing assertions that credentials never appear in review HTML and that a missing provider key moves the run to `credentials_required`.

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_app.py tests/web/test_production_routes.py -q
```

Expected: new grouped-review and credential-blocked markup assertions fail; endpoint and state-transition tests still pass.

- [ ] **Step 3: Finish the Agentic wizard panel**

Present Off and On as semantic radio task cards. Keep Off checked. The existing `[data-agentic-settings]` panel must:

- become visible and enabled only when On is selected;
- retain evaluator/rewriter provider, model, custom model, reasoning, iteration, confidence, stage verification, and patch policy fields;
- re-run pipeline stage activation when opened;
- keep inactive-stage verification disabled even when agentic mode is enabled.

The final action row contains Back and a submit button labeled **Review run** with `data-wizard-submit`. Do not create a fake client-side Review panel.

- [ ] **Step 4: Group the authoritative Review page**

Replace the flat `<dl>` loop with four explicit groups over the existing non-secret summary keys:

```jinja2
{% set review_groups = [
  ('Input', ['input', 'output', 'dictionary_pages', 'parse_rule_pages', 'mdf_manual']),
  ('Pipeline', ['pipeline', 'mdf_parsing_guide']),
  ('Model', ['stage_1_model', 'stage_2_pass_1_model', 'stage_2_pass_2_model']),
  ('Agentic', ['agentic'])
] %}
```

Render compact two-column definition grids inside the primary panel. Render readiness, human checkpoint, local-data reassurance, and Start action in a separate `review-actions panel`. Keep preset naming below the configuration summary and visually secondary.

- [ ] **Step 5: Implement the credential-blocked recovery flow**

Add a read-only `GET /runs/{run_id}/review` route that loads the already prepared inference config, renders `_config_summary(config)`, and returns the existing `review.html`. It must not prepare, mutate, or start a run. Unknown or unprepared run IDs return the same safe 404 used by the other run pages.

Render only the required provider's unique credential input, reveal button, live status, Save and Continue action, and Return to Review link. Reuse the existing credential and start endpoints:

```html
<button
  type="button"
  data-save-key
  data-provider="{{ provider }}"
  data-continue-action="/runs/{{ run_id }}/start">
  Save and continue
</button>
<a href="/runs/{{ run_id }}/review">Return to review</a>
```

After a successful credential save, `app.js` creates and submits a same-origin POST form to `data-continue-action`. Do not expose or serialize the credential in the continuation form. The review route is a recovery view over the prepared config, not a second source of review data.

Restyle `form_error.html` as the same bounded error-state component even though wizard validation continues to re-render `home.html`.

- [ ] **Step 6: Verify the workflow states**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_app.py tests/web/test_production_routes.py tests/web/test_run_form.py -q
```

Expected: focused tests pass.

In Chromium:

1. Toggle Agentic Off → On → Off and confirm subordinate fields enable/disable without losing values.
2. Complete Steps 1–4 and submit; confirm Step 5 is the server-rendered Review response.
3. Confirm review groups, checkpoint, preset form, Back/Edit action, and Start action are keyboard reachable in task order.
4. Exercise a missing-key start, save the required key on the blocked page, and confirm Save and Continue retries `/runs/{id}/start` without placing the secret in the URL or HTML.
5. Confirm a server validation failure reopens the owning wizard step rather than displaying an isolated dead-end page.

- [ ] **Step 7: Commit**

```bash
git diff --check
git add tests/web/test_app.py tests/web/test_production_routes.py src/mudidi/web/app.py src/mudidi/web/templates/home.html src/mudidi/web/templates/review.html src/mudidi/web/templates/credential_required.html src/mudidi/web/templates/form_error.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Complete dashboard setup and review states"
```

---

### Task 5: Rebuild Active Run, Run History, and Saved Presets

**Files:**
- Modify: `tests/web/test_run_routes.py`
- Modify: `tests/web/test_production_routes.py`
- Modify: `tests/web/test_run_store.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `src/mudidi/web/runs.py`
- Modify: `src/mudidi/web/inputs.py`
- Modify: `src/mudidi/web/templates/active.html`
- Modify: `src/mudidi/web/templates/history.html`
- Modify: `src/mudidi/web/templates/presets.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add route-visible page contracts**

Extend existing route tests to require:

- Active Run progress percentage, page count, elapsed-time hook, four-stage timeline, recent activity, Overview, and Cancel actions for a live run.
- Run History semantic table headings: Run, Status, Progress, Provider, Last update, Actions.
- A prepared history row shows its output directory beneath the run ID; a record whose prepared config is unavailable shows `Unavailable` without failing the page.
- History filters and delete forms retain their existing query/action contracts.
- Saved Presets render cards with name, update time, provider, pipeline, primary model, agentic state, Use preset, and Remove.
- Purposeful empty states link to a next action.

Example history contract:

```python
for heading in ("Run", "Status", "Progress", "Provider", "Last update", "Actions"):
    assert f"<th scope=\"col\">{heading}</th>" in response.text
assert 'class="table-scroll"' in response.text
```

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_run_routes.py tests/web/test_run_store.py tests/web/test_production_routes.py -q
```

Expected: semantic-table, active metrics, preset metadata, or preset deletion assertions fail.

- [ ] **Step 3: Expose existing run timestamps and output paths to templates**

In `_run_view`, add the already persisted `updated_at` alongside `created_at`; do not query a new timestamp source or alter storage:

```python
"created_at": run.created_at,
"updated_at": run.updated_at,
```

For history only, add a small view helper that loads the already prepared config through `JobController` and returns `str(config.output.directory)`. Catch `KeyError`, `OSError`, and `ValidationError` and return `Unavailable` so stale historical records cannot break the page. Do not add output paths to the SQLite schema or expose paths through a new endpoint.

Keep percentage calculation presentation-only and bounded to `0..100` in the template. Render elapsed time from `data-elapsed-from="{{ run.created_at.isoformat() }}"` in `app.js`; use a stable fallback timestamp when JavaScript is unavailable.

- [ ] **Step 4: Recompose Active Run**

Use the existing `run.pipeline_steps`, `events`, current-stage label, page counters, and action availability. One active run becomes the primary progress panel with bounded bar, four-state timeline, compact statistics, recent activity, Overview, and destructive Cancel. If no worker is active, link to both Run History and New Run.

Load the existing event stream metadata and `app.js` for a live run so the page refreshes on persisted worker events without adding a polling API.

- [ ] **Step 5: Replace history rows with a semantic table**

Keep the filter GET form and delete POST forms. Wrap the table in `.table-scroll`; do not let the document become horizontally scrollable. Long run IDs and output metadata wrap inside their cells. Keep View/Review/Resume and Remove actions in a fixed action column.

- [ ] **Step 6: Add the approved preset Remove action**

Add `RunStore.delete_preset(preset_id)` using an exact `DELETE FROM presets WHERE preset_id = ?` and raise `KeyError` when no row is deleted. Add `POST /presets/{preset_id}/delete`; delete the metadata, discard the managed preset bundle through the existing safe `InputMaterializer.discard_preset`, and redirect to `/presets` with status 303. Unknown IDs return 404. Update `discard_preset`'s docstring to describe committed as well as uncommitted managed bundles; its path validation and best-effort recursive deletion remain unchanged.

Defend both layers:

```python
store.delete_preset(preset.preset_id)
with pytest.raises(KeyError):
    store.get_preset(preset.preset_id)
```

```python
response = client.post(
    f"/presets/{preset.preset_id}/delete",
    follow_redirects=False,
)
assert response.status_code == 303
assert response.headers["location"] == "/presets"
assert not preset_bundle.exists()
```

Render preset data as responsive cards. **Use preset** links to `/?preset={id}` and remains primary; Remove is a smaller POST action protected by a preset-specific confirmation in `app.js`. Preserve the current non-secret wording.

- [ ] **Step 7: Verify list and task pages**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_run_routes.py tests/web/test_run_store.py tests/web/test_production_routes.py tests/web/test_theme.py -q
```

Expected: focused tests pass.

Use the existing deterministic offline demo endpoint to create a local active/completed run. Inspect active, empty-active, filtered history, empty-history, populated presets, and empty-presets states at 1568, 1024, and 390 pixels in light and dark themes. Confirm only table containers scroll horizontally.

- [ ] **Step 8: Commit**

```bash
git diff --check
git add tests/web/test_run_routes.py tests/web/test_run_store.py tests/web/test_production_routes.py src/mudidi/web/app.py src/mudidi/web/runs.py src/mudidi/web/inputs.py src/mudidi/web/templates/active.html src/mudidi/web/templates/history.html src/mudidi/web/templates/presets.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Rebuild run tracking and preset pages"
```

---

### Task 6: Unify Run Overview and MDF Guide Review

**Files:**
- Modify: `tests/web/test_run_routes.py`
- Modify: `tests/web/test_parse_rule_routes.py`
- Modify: `src/mudidi/web/templates/run_detail.html`
- Modify: `src/mudidi/web/templates/parse_rules.html`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add workspace and editor contracts**

Require the run overview to expose:

- a wrapping run identifier;
- primary progress panel and existing pipeline timeline;
- run-specific navigation for Overview, MDF Guide, Page Editor, Logs, Artifacts, and Usage;
- safe Cancel, Resume, and Delete actions according to existing state flags.

Require editable MDF review to expose explicit Markers, Guide Rules, and Abbreviations sections; Add buttons; fixed remove columns; Save Draft; and Approve and Continue. Keep the immutable snapshot assertions and absence of editable controls after approval.

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_run_routes.py tests/web/test_parse_rule_routes.py -q
```

Expected: new workspace-navigation or explicit editor-structure assertions fail.

- [ ] **Step 3: Refine Run Overview hierarchy**

Keep the existing progress calculation, timeline state, event stream, failure details, and run actions. Move navigation into a subordinate run-workspace panel. Use `overflow-wrap: anywhere` on identifiers and paths. Distinguish complete/running/future steps with text or symbols as well as color.

- [ ] **Step 4: Refine editable and immutable MDF review**

Keep the existing field names, template rows, draft endpoint, approval endpoint, and validation warning. Use visible section headers inside border-safe semantic groups. Each editable row uses flexible content columns and one 42–44 pixel destructive action column. The approval bar is sticky only when it cannot cover content; below the mobile breakpoint it becomes static and stacks.

Do not change the meaning of Save Draft or Approve and Continue, and do not make an approved snapshot editable.

- [ ] **Step 5: Verify dynamic rows and state variants**

Run:

```bash
uv run --locked pytest tests/web/test_run_routes.py tests/web/test_parse_rule_routes.py -q
```

Expected: focused tests pass.

In Chromium, add and remove marker, guide-rule, and abbreviation rows with long values at 390 pixels. Confirm remove actions remain inside the panel, labels remain associated, the approval bar does not cover fields, and the approved snapshot contains no editable controls.

- [ ] **Step 6: Commit**

```bash
git diff --check
git add tests/web/test_run_routes.py tests/web/test_parse_rule_routes.py src/mudidi/web/templates/run_detail.html src/mudidi/web/templates/parse_rules.html src/mudidi/web/static/app.css
git commit -m "Unify run overview and MDF review"
```

---

### Task 7: Finish Page Viewer and Page Editor surfaces

**Files:**
- Modify: `tests/web/test_artifacts.py`
- Modify: `src/mudidi/web/templates/pages.html`
- Modify: `src/mudidi/web/templates/page_detail.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Add page-surface contracts**

Extend existing artifact route tests to require:

- Page Viewer empty state with explanation and links to active progress/overview.
- Page Editor header, save status, Previous/Next controls, slider, current position, source panel, Stage 1 editor, Stage 2 editor, save bar, and related-artifacts disclosure.
- Existing iframe/image source behavior, editable text field names, source URL protection, redirect behavior, event metadata, and unsaved-change guard remain intact.

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_artifacts.py -q
```

Expected: one or more approved hierarchy/empty-state assertions fail while route security assertions remain green.

- [ ] **Step 3: Recompose the empty and populated page surfaces**

For `pages.html`, use one centered bounded empty panel, explain when processed pages appear, and link back to active progress when `is_active` is true or to run overview otherwise.

For `page_detail.html`, preserve current routes and field names while enforcing this order:

1. Page header and save status.
2. Previous / slider / current position / Next navigation panel.
3. Source document panel.
4. Stage 1 and Stage 2 editor panels.
5. Save bar associated with the text editor form.
6. Related artifacts disclosure.

Keep the source panel sticky only in the two-column layout. Switch to source-first stacking before either editor becomes narrower than its usable minimum.

- [ ] **Step 4: Preserve page interaction behavior**

Keep the current slider URL arrays parsed from data attributes, change navigation, event-source reload, and before-unload warning. Update selectors only where markup changed; do not create a second slider or dirty-state implementation.

- [ ] **Step 5: Verify the editor in the real browser**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_artifacts.py tests/web/test_security.py -q
```

Expected: focused tests pass.

In Chromium, exercise image-backed and PDF-backed sources, move by Previous/Next and slider, edit both text areas, observe the unload warning, save, and confirm the saved status. Check 1568, 1024, and 390 pixel widths in both themes; the source is sticky only in the usable two-column layout and no action bar covers text.

- [ ] **Step 6: Commit**

```bash
git diff --check
git add tests/web/test_artifacts.py src/mudidi/web/templates/pages.html src/mudidi/web/templates/page_detail.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Finish page viewer and editor surfaces"
```

---

### Task 8: Build Logs, Artifacts, and Usage data surfaces

**Files:**
- Modify: `tests/web/test_artifacts.py`
- Modify: `tests/web/test_production_routes.py`
- Modify: `src/mudidi/web/artifacts.py`
- Modify: `src/mudidi/web/app.py`
- Modify: `src/mudidi/web/templates/logs.html`
- Modify: `src/mudidi/web/templates/outputs.html`
- Modify: `src/mudidi/web/templates/usage.html`
- Modify: `src/mudidi/web/static/app.js`
- Modify: `src/mudidi/web/static/app.css`

- [ ] **Step 1: Inspect exported view-model impact with LSP**

Run LSP references for `RunArtifact` and `UsageSummary` before editing. Confirm every constructor and consumer is accounted for. Do not proceed from text search alone.

- [ ] **Step 2: Add metadata and breakdown tests first**

Extend `test_artifacts.py` with observable service and route assertions:

```python
assert artifact.modified_at.tzinfo is not None
assert [row.stage for row in usage.breakdown] == [
    "Stage 1",
    "Stage 2 · field discovery",
    "Stage 2 · MDF extraction",
]
assert 'class="artifact-table"' in outputs.text
assert "Last updated" in outputs.text
assert "Usage by stage" in usage_page.text
```

Use representative per-page usage payloads containing `stage1`, `field_discovery`, and `stage2` dictionaries. Defend total-token and optional-cost aggregation, including an unavailable-cost row. Extend the log route test to require bounded console markup, stream status, pause/resume, and copy controls while retaining redaction and truncation assertions.

- [ ] **Step 3: Run focused tests and confirm the red state**

Run:

```bash
uv run --locked pytest tests/web/test_artifacts.py tests/web/test_production_routes.py -q
```

Expected: `modified_at`, usage breakdown, artifact table, or log toolbar assertions fail.

- [ ] **Step 4: Extend web-only artifact metadata safely**

Add timezone-aware modification time to `RunArtifact`, using one `stat()` result per file:

```python
@dataclass(frozen=True, slots=True)
class RunArtifact:
    relative_path: Path
    absolute_path: Path
    size_bytes: int
    modified_at: datetime
```

Add an immutable `UsageBreakdown` row and a tuple on `UsageSummary`. Aggregate known per-page keys into fixed labels:

- `stage1` → Stage 1
- `field_discovery` → Stage 2 · field discovery
- `stage2` → Stage 2 · MDF extraction
- agentic usage remains attributed to its owning stage or a clearly named Agentic row

Accept `cost_usd` or `total_cost_usd`, sum `total_tokens`, and preserve `None` when no cost exists. When `run_usage.json` is present, derive totals from its page records if a run-level token total is absent. Do not change generated usage files or extraction accounting.

- [ ] **Step 5: Implement the three data pages**

- Logs: bounded `<pre data-log-console>`, stream status, Pause/Resume, and Copy visible text. Continue rendering truncation and redacted failure warnings.
- Artifacts: filter controls plus semantic table columns Relative path, Stage, Size, Last updated, Download. Keep the existing safe download href unchanged.
- Usage: Total Tokens, Estimated Cost, and Usage Files metric cards followed by the stage breakdown table when rows exist. Keep `Unavailable` explicit rather than coercing missing costs to zero.

For live logs, add the existing event-stream metadata from persisted run state. Refactor the current EventSource setup into an idempotent `startLiveUpdates()`/`stopLiveUpdates()` pair so the logs Pause/Resume control owns only reload behavior. Copy uses `navigator.clipboard.writeText(logConsole.innerText)` with a visible success/failure status; it never sends log content to the server.

- [ ] **Step 6: Verify data behavior and security**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web/test_artifacts.py tests/web/test_production_routes.py tests/web/test_security.py -q
```

Expected: focused tests pass, including path traversal rejection, log redaction, bounded output, usage totals, and the new display metadata.

In Chromium, filter artifacts by path/stage, copy visible redacted logs, pause/resume live refresh, and inspect usage with and without provider cost. At 390 pixels, confirm only the artifact table and log console own internal overflow.

- [ ] **Step 7: Commit**

```bash
git diff --check
git add tests/web/test_artifacts.py tests/web/test_production_routes.py src/mudidi/web/artifacts.py src/mudidi/web/app.py src/mudidi/web/templates/logs.html src/mudidi/web/templates/outputs.html src/mudidi/web/templates/usage.html src/mudidi/web/static/app.js src/mudidi/web/static/app.css
git commit -m "Build dashboard log artifact and usage views"
```

---

### Task 9: Complete responsive, accessibility, and theme parity

**Files:**
- Modify: `tests/web/test_theme.py`
- Modify: `tests/web/test_app.py`
- Modify: `src/mudidi/web/templates/_layout.html`
- Modify: all fourteen page templates under `src/mudidi/web/templates/`
- Modify: `src/mudidi/web/static/app.css`
- Modify: `src/mudidi/web/static/app.js`

- [ ] **Step 1: Add final cross-page semantic assertions**

Expand the shared-shell test route matrix to cover every reachable page variant created by deterministic test fixtures. Require one `main` landmark, ordered page heading, visible or explicit accessible labels for controls, and current run-workspace location where applicable. Keep these response-level contracts semantic; do not assert incidental whitespace or full class strings.

- [ ] **Step 2: Run the cross-page tests and confirm any remaining red state**

Run:

```bash
uv run --locked pytest tests/web/test_theme.py tests/web/test_app.py tests/web/test_run_routes.py tests/web/test_parse_rule_routes.py tests/web/test_artifacts.py -q
```

Expected: any template not yet using the common page header/panel/status/action conventions fails its focused assertion.

- [ ] **Step 3: Remove the last layout inconsistencies**

Audit and update all fourteen page templates as one clean cutover:

1. `home.html`
2. `review.html`
3. `active.html`
4. `history.html`
5. `presets.html`
6. `run_detail.html`
7. `parse_rules.html`
8. `pages.html`
9. `page_detail.html`
10. `logs.html`
11. `outputs.html`
12. `usage.html`
13. `credential_required.html`
14. `form_error.html`

Every page uses the common header hierarchy, bounded workspace, panel levels, status language, empty-state actions, and minimum interactive sizes. Remove selectors and markup aliases that no longer have a consumer. Keep one CSS implementation per component.

- [ ] **Step 4: Complete breakpoint and motion rules**

Verify and adjust:

- desktop sidebar and optional sticky 300–320 pixel summary;
- tablet collapse before controls become cramped;
- Page Editor single-column switch near 1340 pixels;
- compact mobile global navigation;
- marker-only mobile wizard stepper;
- single-column form/pass grids;
- static stacked action bars on mobile;
- internal overflow for semantic tables and log console only;
- `prefers-reduced-motion` covering every translated/animated control.

Use `min-width: 0`, `overflow-wrap: anywhere`, and explicit fixed action columns at the source. Do not mask layout defects with `body { overflow-x: hidden; }`.

- [ ] **Step 5: Run the automated suite**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web -q
git diff --check
```

Expected: all web tests pass; JavaScript parses; no whitespace errors.

- [ ] **Step 6: Run the complete browser acceptance matrix**

Start the actual dashboard with a temporary local data directory. Use application endpoints and the deterministic offline demo to create empty, active, review, completed, failed/blocked, preset, editable-guide, immutable-guide, page, artifact, usage, and validation states. Exercise every page in light and dark themes at 1568, 1024, and 390 pixel widths.

For each state, assert:

```javascript
if (document.documentElement.scrollWidth !== document.documentElement.clientWidth) {
  throw new Error(`document overflow on ${location.pathname}`);
}
```

Also confirm:

- step labels never intersect connector lines;
- select arrows retain at least 16 pixels of right inset;
- reveal icons are centered;
- profile and MDF row actions remain inside their panels;
- shared/advanced Stage 2 modes replace rather than stack;
- tables confine horizontal overflow;
- sticky panels do not cover editable content;
- keyboard focus follows task order and remains visibly outlined;
- selected, complete, running, warning, and error states have non-color cues;
- theme choice persists exactly as before.

Fix every observed defect in the owning template/CSS/JavaScript and rerun that page at all three widths and both themes.

- [ ] **Step 7: Commit**

```bash
git diff --check
git add tests/web/test_theme.py tests/web/test_app.py src/mudidi/web/templates src/mudidi/web/static/app.css src/mudidi/web/static/app.js
git commit -m "Complete responsive dashboard theme parity"
```

---

### Task 10: Update operator documentation and perform final review

**Files:**
- Modify: `docs/production/local-web-app.md`
- Verify: all files changed since `bc156bb`

- [ ] **Step 1: Update the dashboard workflow documentation**

Revise **Create a run**, **Dictionary Profile**, **Models and providers**, **Agentic verification**, and **Credentials and local data** to describe:

- Input, Pipeline, Model, Agentic, and server-rendered Review steps;
- current-step client validation plus authoritative server validation;
- shared Stage 2 model/reasoning default;
- **Advanced · split passes** and the Pass 1/Pass 2 responsibilities;
- the single synchronized run-level provider;
- selected-provider credential card and **Manage keys** disclosure;
- unchanged encrypted local credential storage and unchanged field/endpoint semantics.

Do not claim that files or passwords are restored. State that non-file, non-password form values persist only in the current browser tab through the existing session storage behavior.

- [ ] **Step 2: Run final verification from a clean dashboard process**

Run:

```bash
node --check src/mudidi/web/static/app.js
uv run --locked pytest tests/web -q
uv run --locked pytest -q
git diff --check
```

Expected: JavaScript parses, web tests pass, the full repository suite passes, and the diff has no whitespace errors.

Smoke the actual CLI surface:

```bash
uv run --locked mudidi web --no-browser --port 8765 --data-dir /tmp/mudidi-dashboard-ui-final
```

Open `http://127.0.0.1:8765/`, complete a valid local preview through all four browser panels, inspect the server Review response, and start a deterministic/offline run path. Stop the server cleanly after verification.

- [ ] **Step 3: Request and address code review**

Use the `requesting-code-review` skill against the complete branch diff. Review specifically for:

- lost or duplicated form fields;
- provider or Stage 2 synchronization races;
- preset/session restoration regressions;
- disabled fields omitted unexpectedly from `FormData`;
- credential leakage or cross-origin requests;
- hidden invalid controls that cannot receive focus;
- semantic/table/accessibility regressions;
- document overflow and sticky-content overlap;
- obsolete selectors or mixed old/new components.

Apply every accepted finding, rerun its focused reproduction, then rerun the web suite and affected browser states. Do not accept speculative refactors outside the approved design.

- [ ] **Step 4: Commit documentation and review fixes**

```bash
git diff --check
git add docs/production/local-web-app.md
git add -u
git commit -m "Document the dashboard wizard workflow"
```

- [ ] **Step 5: Verify the committed branch is ready**

Run:

```bash
uv run --locked pytest tests/web -q
git status --short
```

Expected: all web tests pass and `git status --short` prints nothing. Record the exact test counts, browser matrix exercised, and final commit hashes in the delivery response.
