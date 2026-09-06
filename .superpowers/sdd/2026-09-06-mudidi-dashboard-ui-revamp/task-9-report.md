# Task 9 — Final parity audit

## Result

The final dashboard parity pass is complete. The implementation keeps the existing route contracts and `theme.js` behavior intact while tightening the shared semantic shell, run-workspace navigation, responsive layout, control sizing, internal table/log scrolling, and reduced-motion behavior.

## Semantic and behavior coverage

- Added a reachable-template semantic matrix covering home, active, history, presets, review, credential-required, run detail, parse rules, page viewer/editor, logs, artifacts, and usage responses.
- Every audited response has one visible `<main>`, one visible `<h1>`, labeled controls, and an explicit current run-workspace location where applicable.
- Added wizard contract coverage for ordered named panels, current step state, and non-color validation/error states.
- Added accessible current-page markers to all run-workspace tab variants, including review and credential-required states.
- Stage 2 shared/split mode remains replace-not-stack; the browser audit confirms pass 2 is hidden in shared mode and visible in split mode. Dynamic Stage 2 labels and info-button accessible names now track the selected mode.
- Kept `theme.js` and the default theme path unchanged.

## Browser matrix

Live dashboard audit against the prepared fixture at `127.0.0.1:8765`:

- Routes: `/`, `/active`, `/history`, `/presets`, run review, credential-required, run detail, parse rules, page viewer/editor, logs, artifacts, and usage.
- Themes: explicit `light` and `dark` local-storage selections.
- Viewports: 1568px, 1024px, and 390px.
- Result: zero document-overflow failures, zero missing visible main/headings, and exactly one visible current run-workspace location on applicable run routes.
- Focus audit: keyboard tab order reaches the skip link, shell navigation, controls, and wizard fields with visible 3px focus outlines.
- Responsive audit: wizard marker connectors align with marker centers on desktop and mobile; mobile marker copy intentionally collapses without horizontal overflow.
- Control audit: reveal icons are centered in 48px key buttons; buttons, file controls, info buttons, tabs, and footer documentation links meet the intended 42px target height.
- Scroll audit: artifact and usage tables retain horizontal scrolling inside bounded wrappers (`document.scrollWidth` remains the viewport width); the page editor source panel is sticky on desktop and does not overlap the editable column.
- Motion audit: reduced-motion media emulation yields zero-duration transitions and effectively disabled animations.
- Theme audit: explicit light selection wins over dark OS preference, toggle switches to dark, and dark persists after reload.

## Verification

- `uv run --locked pytest tests/web -q` — **253 passed**, 6 existing dependency warnings.
- `node --check src/mudidi/web/static/app.js` — passed.
- `git diff --check` — passed.

## Fix round 1/5

Addressed all five review findings without changing the default theme path or `theme.js`:

- Restored one-column `.form-grid` and `.preset-grid` layouts at `max-width: 900px`.
- Restored stacked, stretched mobile `.wizard-actions` with a 12px gap, full-width children, and the status message ordered last.
- Applied the anchor current-tab treatment to the credential-required `span[aria-current="page"]`.
- Rebuilt the stdlib `HTMLParser` shell helper so void inputs never stay open, hidden depth follows real element nesting, only button descendants contribute button names, and input/select/textarea labels require nested/explicit labels or resolved ARIA labels. Added negative tests for unlabeled controls and mismatched button label-in-name.
- Expanded the semantic matrix to 14 template/state entries, including an actual rendered `form_error.html` variant and `/runs/workspace-run` with workspace-current assertions.

### Fix-round verification

- `uv run --locked pytest tests/web/test_theme.py -q` — **9 passed**, 6 existing dependency warnings.
- `uv run --locked pytest tests/web/test_theme.py tests/web/test_app.py tests/web/test_run_routes.py tests/web/test_parse_rule_routes.py tests/web/test_artifacts.py -q` — **90 passed**, 6 existing dependency warnings.
- `uv run --locked pytest tests/web -q` — **257 passed**, 6 existing dependency warnings.
- `uv run --locked ruff check tests/web/test_theme.py` — passed.
- `node --check src/mudidi/web/static/app.js` — passed.
- `git diff --check` — passed.

### Fix-round browser recheck

- At 390px, new-run form controls and wizard actions had zero document overflow; form grid was one column; actions were column/stretched with 12px gap and the status row remained last.
- At 390px, presets had one column and the credential-required span current tab matched anchor current-tab background, border, and 42px target height.
- At 390px, forced shared/split Stage 2 mode showed one visible pass in shared mode and both passes in split mode without overflow.
- At 1024px and 1568px, explicit light and dark theme selections persisted with zero document overflow; desktop form grids remained two columns.

### Concerns

- Browser fixture server reused the deterministic local fixture already running on port 8765; no production data was changed.
