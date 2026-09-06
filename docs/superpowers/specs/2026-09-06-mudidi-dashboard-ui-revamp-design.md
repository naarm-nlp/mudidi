# MUDIDI Dashboard UI Revamp Design

**Date:** 2026-09-06  
**Status:** Mockup approved; awaiting specification approval  
**Branch:** `features/brutalist-dashboard-reskin`

## Decision summary

MUDIDI will keep the approved black, white, and lavender brutalist visual language while replacing the current long New Run form with a focused five-step wizard. The same component hierarchy will be applied to every dashboard page and state.

The wizard steps are:

1. Input
2. Pipeline
3. Model
4. Agentic
5. Review

The approved Model step uses one Stage 1 configuration and one shared Stage 2 configuration by default. The shared Stage 2 model and reasoning values apply to both Stage 2 Pass 1 and Pass 2. An explicit **Advanced · split passes** action replaces the shared Stage 2 controls with separate Pass 1 and Pass 2 controls. It does not append a second copy below the shared controls.

The redesign is presentation and browser-interaction work. Existing run preparation, validation, credential storage, execution, review, and artifact endpoints remain authoritative.

## Goals

- Make New Run understandable without exposing every option simultaneously.
- Give required inputs stronger priority than optional expert controls.
- Preserve the established brutalist theme while improving spacing, typography, alignment, and information hierarchy.
- Make controls usable without overflow from 390-pixel mobile widths through wide desktop layouts.
- Apply one coherent component system to all fourteen rendered page templates.
- Explain Stage 2 Pass 1 and Pass 2 at the point where users configure them.
- Keep common Stage 2 configuration simple while retaining independent pass controls for expert use.
- Preserve keyboard access, semantic form grouping, focus visibility, and screen-reader labels.

## Non-goals

- No changes to extraction algorithms, prompts, model catalogs, or run-state transitions.
- No new remote service, account system, telemetry, analytics, or cloud persistence.
- No independent provider per pipeline stage. The existing run-level provider remains authoritative.
- No replacement of server-side Jinja rendering with a frontend framework.
- No persistence of incomplete wizard form state across browser restarts.
- No changes to the local credential encryption or reveal endpoints.
- No changes to theme storage behavior or the `mudidi:theme` key.

## Visual system

### Theme

Retain the existing light and dark token families:

- Neutral dotted canvas.
- Solid black or white ink and structural lines.
- White or zinc panels.
- Lavender primary accent.
- Green success, amber warning, and red destructive states.
- Square corners throughout.
- Three-pixel structural borders, two-pixel control borders, and hard offset shadows.

Hard shadows are reserved for primary panels, selected navigation, and actionable controls. Nested form groups use borders without redundant large shadows.

### Typography

- Use the existing sans-serif stack for headings, labels, controls, descriptions, and body text.
- Use the monospace stack only for run identifiers, timestamps, file paths, status metadata, step numbers, and compact eyebrow labels.
- Uppercase is limited to page headings, section headings, buttons, status labels, and eyebrows.
- Body copy, field labels, explanations, and user-entered values remain sentence case.
- Field labels use one consistent weight and size across all pages.

### Spacing and hierarchy

Use an eight-pixel-derived spacing rhythm. A page has four visual levels:

1. Page header
2. Primary task panel
3. Section or stage blocks
4. Fields and supporting copy

Required and frequently used controls appear before optional and advanced controls. Explanatory copy sits directly beneath the heading or control it explains.

## Application shell

The existing shared Jinja layout remains the single application shell:

- Persistent desktop sidebar.
- Compact horizontal or hidden sidebar treatment at tablet and mobile sizes.
- Page header containing an eyebrow, page title, concise description, and optional status or primary action.
- Centered, bounded content workspace.
- Existing synchronous theme bootstrap remains in `<head>` to avoid a light/dark flash.

Primary sidebar destinations remain New Run, Active Run, Run History, and Saved Presets. Run-specific secondary navigation remains inside the run workspace rather than expanding the global sidebar.

## New Run wizard

### State model

Steps 1–4 remain sections of one browser-owned form. Moving between these steps does not submit or serialize partial state. Back and Continue actions show or hide sections while preserving their live control values.

The final transition submits the complete form to the existing `/runs/preview` endpoint. The server continues to validate and prepare the review. The server-rendered Review Run page remains the authoritative Step 5 result.

Wizard state is not added to the backend schema. The browser tracks only the active visual step and whether Stage 2 uses shared or split controls.

### Stepper

- Each step has a fixed square number marker and a separate text block.
- Connector lines occupy only the space between markers and never pass under labels.
- Desktop displays number, title, and short description.
- Mobile displays the five numbered markers without labels.
- The active step uses lavender fill and a hard shadow.
- Completed steps are distinguishable from the current step without relying on color alone.
- The stepper never creates document-level horizontal overflow.

### Step validation

Continue validates only fields relevant to the current step and active pipeline:

- Invalid fields receive a red border, pale red fill, and adjacent text error.
- Focus moves to the first invalid control.
- An error summary appears at the top of the active panel when several fields fail.
- Back never validates or clears values.
- Server-side validation remains mandatory on preview submission.

### Step 1: Input

The first step groups controls in this order:

1. Dictionary PDF upload
2. Output directory
3. Required dictionary pages
4. Optional introduction pages
5. Additional context
6. Optional Dictionary Profile

The PDF upload and required page controls form the primary block. Character inventory, stage instructions, parsing-guide samples, existing guide upload, and MDF manual inputs are grouped as clearly labeled optional context rather than one undifferentiated grid.

#### Dictionary Profile

Replace border-crossing legends and generic editor rows with explicit sections:

- Headword language and script row.
- Translation/gloss/definition language rows.
- Page layout textarea.
- Compact information-type checkbox grid.

Language rows use `minmax(0, 1fr) minmax(0, 1fr) auto`. The remove action occupies a fixed action column and cannot expand outside the panel. On narrow screens, labels remain associated with each input and the action stays within the row.

Semantic fieldsets and legends may remain for assistive technology, but fieldset borders must not intersect visible legend text. Visual grouping may use an inner section header while the semantic fieldset itself has no border.

### Step 2: Pipeline

Present Complete Digitization, Transcription Only, and MDF Parsing as mutually exclusive task cards. Every card states:

- What it produces.
- Which stages will run.
- Whether an MDF guide review is required.

Existing artifact policy is a separate two-choice group beneath the pipeline cards. Pipeline selection continues to determine which later inputs and model controls are active.

### Step 3: Model

The page order is:

1. Credential status and management
2. Stage 1 model configuration when Stage 1 is active
3. Stage 2 shared configuration when either Stage 2 pass is active
4. Temperature and batch size

The run-level provider remains the backend source of truth. Repeated provider presentation in stage blocks must remain synchronized with that single value; the redesign does not introduce per-stage provider semantics.

#### Credentials

Show the selected provider prominently with its saved status, masked input, centered reveal control, and one Save or Update action. Other providers sit behind a **Manage keys** disclosure rather than four equal cards competing with model configuration.

The reveal button uses a square 44–48 pixel hit target with flex centering. The icon's visual center must match the button center on both axes.

#### Stage 1

Stage 1 exposes its model and reasoning settings when the active pipeline includes transcription. The existing model catalog, custom-model flow, and provider filtering remain unchanged.

#### Stage 2 shared mode

Shared mode is the default presentation. It exposes:

- Stage 2 model
- Stage 2 reasoning

The chosen model is written to both existing `stage2_pass1_model` and `stage2_pass2_model` controls. The chosen reasoning value is written to both existing `stage2_pass1_reasoning` and `stage2_pass2_reasoning` controls.

Supporting text states that the values apply to both passes. The run summary displays **Shared model** and the selected model name.

#### Stage 2 advanced mode

An **Advanced · split passes** action sits beside the Stage 2 heading. Activating it:

1. Copies the current shared model into Pass 1 and Pass 2.
2. Copies the current shared reasoning into Pass 1 and Pass 2.
3. Replaces the shared controls with two pass cards.
4. Changes the action to **Use one Stage 2 model**.
5. Updates the summary to **Separate pass models** and shows both model names.

The two cards explain their responsibilities:

- **Pass 1 — MDF field discovery:** examines representative pages to infer dictionary-specific MDF markers and entry structure. Its generated guide is reviewed before conversion.
- **Pass 2 — Per-page MDF extraction:** converts each authoritative Stage 1 transcription into final MDF using the approved Pass 1 guide. This is the high-volume page-processing pass.

Each card exposes its own model and reasoning controls. Provider remains the shared run-level provider.

Returning to shared mode restores the user's previous shared values. Pass-specific values may remain in browser memory for another advanced-mode visit, but they must be disabled or otherwise excluded from form submission while shared mode is active. Shared controls must likewise be excluded while advanced mode is active. Only one coherent set reaches server validation.

### Step 4: Agentic

Present Off and On as two task cards. Off remains the simple default. Enabling agentic verification reveals evaluator, rewriter, confidence, iteration, stage-selection, and patch-policy controls in a subordinate advanced panel.

Existing stage activation, model catalogs, custom-model behavior, and validation remain unchanged.

### Step 5: Review

The server-rendered review groups values by Input, Pipeline, Model, and Agentic decisions. Each group uses a compact two-column definition grid rather than one long undifferentiated list.

The right-side action panel contains:

- Ready or blocked status.
- Human checkpoint explanation.
- Start action.
- Local-data reassurance.

Preset naming remains below the configuration summary and visually secondary to starting the run.

## Other page designs

### Active Run

Use a task-focused progress panel with:

- Current stage and percentage.
- Page count and elapsed time.
- Bounded progress bar.
- Four-stage timeline with complete, running, and future states.
- Recent activity and compact run statistics.
- Separate Overview and destructive Cancel actions.

The empty state uses the same page header and a purposeful panel linking to Run History or New Run.

### Run History

Use a compact filter bar followed by a semantic table:

- Search
- Status
- Provider
- Run identifier and output path
- Status
- Page progress
- Provider
- Last update
- Fixed-width action group

View and Remove actions remain inside the final column. The table may scroll within its own container on narrow screens; the document itself must not overflow. Destructive bulk deletion stays in the page header and includes explanatory text or confirmation through the existing behavior.

### Saved Presets

Use responsive cards containing preset name, update time, provider, pipeline, primary model, and agentic state. **Use preset** is primary; Remove is a smaller destructive action. Empty state copy links users back to New Run.

### Run Overview

Keep progress as the primary panel and use a subordinate run-workspace navigation panel for Overview, MDF Guide, Page Editor, Logs, Artifacts, and Usage. The progress timeline uses the same stage component as Active Run. Long run identifiers wrap safely.

### MDF Guide Review

Separate Markers, Guide Rules, and Abbreviations with explicit section headings and Add actions. Editable rows use flexible content columns and a fixed 42–44 pixel remove column. The sticky approval bar distinguishes Save Draft from Approve and Continue. On mobile, the bar becomes static and stacked.

Approved snapshots use the same structure without editable controls and state clearly that the snapshot is immutable.

### Page Viewer empty state

Use a centered, bounded empty panel that explains when processed pages become available and links to active progress when applicable. Avoid an oversized empty canvas without a next action.

### Page Editor

Use:

- Page header and save status.
- Page navigation panel with Previous, slider, current position, and Next.
- Source document panel.
- Stage 1 and Stage 2 editor panels.
- Save bar associated with the text editors.
- Related artifacts disclosure below the workspace.

Desktop uses a source/editor split. Tablet and mobile stack the source before generated text. The source panel is sticky only when two columns fit without reducing either editor below its usable width.

### Live Logs

Use a bounded monospace console with a small toolbar for stream status, pause/resume, and copying visible text. Preserve redaction and bounded-log behavior. Empty logs use an inline console state rather than a large generic card.

### File Artifacts

Use a filterable semantic table containing relative path, stage, size, update time, and Download action. Long paths wrap or truncate within the file column. Mobile overflow is confined to the table container.

### Usage

Lead with Total Tokens, Estimated Cost, and Usage Files metric cards. Follow with a stage breakdown table when data exists. Preserve the existing unavailable-cost state.

### Credentials Required

Use one centered provider card beneath a clear blocking notice. The page shows only the required provider credential, a centered reveal action, Save and Continue, and Return to Review. Existing encryption and reveal behavior remain unchanged.

### Validation Error

Use a top-level error summary with links or focus targets for invalid controls. Re-render the relevant wizard step with entered values intact and only failed controls highlighted. Validation remains server-authoritative.

## Control specifications

### Select controls

Use a custom visual indicator rather than relying on the browser arrow:

- Remove native appearance where supported.
- Reserve at least 48 pixels of right padding.
- Position the arrow at least 16 pixels from the right border.
- Keep the indicator non-interactive so the entire select remains the hit target.
- Preserve native keyboard and assistive-technology behavior.

### Buttons

- Minimum interactive height: 42 pixels on desktop and 44 pixels for icon-only controls.
- Primary actions use lavender fill.
- Destructive actions use the danger-soft fill and explicit text where space permits.
- Icon-only destructive actions require an accessible name and tooltip/title.
- Disabled actions remain readable and are distinguishable without opacity alone.

### Dynamic rows

Every repeatable row defines an explicit action column. Inputs use `min-width: 0`. Long content wraps rather than forcing the action out of the panel. Remove actions do not inherit generic button widths intended for text actions.

## Responsive behavior

### Wide desktop

- Sidebar remains fixed-width and sticky.
- Wizard and run pages may use a primary panel plus 300–320 pixel sticky summary.
- Page Editor may use two columns.

### Tablet

- Summary and secondary navigation move below the primary content when necessary.
- Form grids collapse before controls become cramped.
- Page Editor switches to one column at the existing safe breakpoint near 1340 pixels.
- History and artifact tables use contained horizontal scrolling when their semantic columns cannot collapse.

### Mobile

- Global navigation becomes compact or hidden according to the existing shell behavior.
- Stepper shows five numbered markers without label text.
- All form and pass grids become one column.
- Action bars become static and stack when needed.
- No page creates document-level horizontal overflow at 390 pixels.

## Accessibility

- Preserve semantic headings in order.
- Every input has a visible label or explicit accessible name.
- Fieldsets continue to group related radios and checkboxes even when their borders are removed visually.
- Step changes move focus to the active step heading after validation succeeds.
- Status changes use existing live regions where applicable.
- Selected, complete, running, warning, and error states use text or symbols in addition to color.
- All controls remain keyboard reachable with a visible high-contrast focus style.
- Motion is limited and respects `prefers-reduced-motion`.
- Light and dark theme text/control contrast must satisfy WCAG AA for normal text.

## Technical boundaries

### Templates

All fourteen page templates remain server-rendered and extend `_layout.html`. The redesign updates their structure to use the shared visual primitives described above. It does not create a second layout or retain obsolete markup aliases.

### Client JavaScript

`app.js` owns wizard navigation and existing form interactions. New browser state is limited to:

- Active wizard step.
- Shared versus split Stage 2 presentation.
- Synchronization of shared Stage 2 values into the existing Pass 1 and Pass 2 fields.
- Credential management disclosure.

The existing theme behavior remains in `theme.js`.

### Server and data contracts

Existing form field names, validation schema, prepared-run workflow, and endpoint paths remain unchanged. Shared Stage 2 mode is a browser presentation that submits equal values through the existing Pass 1 and Pass 2 fields. Advanced mode submits the two explicit existing values.

The current run-level `provider` field remains authoritative. The design must not imply that Pass 1 and Pass 2 can use independently authenticated providers unless a separate backend feature is explicitly designed later.

## Verification and acceptance criteria

### Automated behavior

- Existing `tests/web` behavior remains green after assertions are migrated to the new structure.
- Wizard Continue blocks invalid current-step input and focuses the first failure.
- Wizard Back preserves values without validation.
- Pipeline selection exposes only applicable later controls.
- Shared Stage 2 selection keeps Pass 1 and Pass 2 model values equal.
- Shared Stage 2 reasoning keeps both pass reasoning values equal.
- Entering advanced mode copies current shared values into both pass controls.
- Advanced mode permits different Pass 1 and Pass 2 models and reasoning values.
- Returning to shared mode restores the prior shared values and excludes inactive pass-specific controls from submission.
- Credential reveal, save, delete, and status behavior remains functional.
- Dynamic Dictionary Profile and MDF Guide rows add and remove without overflow.
- Server validation errors return users to the relevant wizard step with values and errors visible.

### Browser verification

Exercise every page in light and dark themes at representative widths:

- 1568-pixel desktop
- 1024-pixel tablet
- 390-pixel mobile

Confirm:

- No document-level horizontal overflow.
- Step labels never intersect connector lines.
- Select arrows retain at least 16 pixels of right inset.
- Reveal icons are visually centered.
- Dictionary Profile remove actions remain inside their rows.
- Shared and advanced Stage 2 modes replace one another correctly.
- Run History and File Artifacts constrain table overflow to their panels.
- Page Editor uses two columns only when both remain usable.
- Sticky summaries and action bars do not cover editable content.
- Keyboard focus and visible focus styles follow the task order.

## Delivery boundary

Implementation is complete only when the approved five-step wizard, shared/advanced Stage 2 interaction, all page templates, responsive behavior, theme parity, existing backend behavior, and changed-contract verification are delivered together. Partial conversion or a mixture of old and new page structures is not acceptable.
