# Task 5 report

## Revision

- Base: `fa2f7a2`
- Head: `3cb8f46` (`Rebuild run tracking and preset pages`)

## Delivered

- Rebuilt active-run tracking with bounded progress, page counts, elapsed-time hook, four-stage timeline, recent activity, overview/cancel actions, and useful empty-state links.
- Rebuilt history as a semantic, responsive table with exact Run/Status/Progress/Provider/Last update/Actions headings, output-directory metadata, stale-config `Unavailable`, filters, review/resume/view actions, and table-local horizontal scrolling.
- Rebuilt preset cards with update/provider/pipeline/model/agentic metadata, primary `Use preset` links, confirmation-backed removal, and purposeful empty state.
- Added exact `RunStore.delete_preset` SQL/`KeyError` contract and POST deletion route that removes the managed input bundle and returns 303 (unknown IDs return 404).
- Preserved existing Agentic/Stage2 controls, continuation/review recovery, and theme behavior; `theme.js` was not changed.
- Terminal history progress now derives terminal-run progress from the latest persisted stage event, so completed runs render 100% rather than 0%.

## TDD and verification

Initial focused red run after adding contracts:

```text
uv run --locked pytest tests/web/test_run_routes.py tests/web/test_run_store.py tests/web/test_production_routes.py -q
5 failed, 39 passed, 6 warnings in 4.01s
```

Final focused verification:

```text
node --check src/mudidi/web/static/app.js
# passed (no output)

uv run --locked pytest tests/web/test_run_routes.py tests/web/test_run_store.py tests/web/test_production_routes.py tests/web/test_theme.py -q
48 passed, 6 warnings in 4.12s

git diff --check
# passed (no output)
```

Warnings are existing Starlette/httpx and SWIG deprecations.

## Browser evidence

Verified the running dashboard with the offline demo server at 1568, 1024, and 390 CSS-pixel viewports in light and dark themes. Checked active populated and empty states, history populated/filtered and empty states, and presets populated and empty states. Representative captured states include:

- Active empty light 1568: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f113f05ed30d.webp`
- Active empty dark 1568: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f11fb1ded30e.webp`
- Active populated light 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f0748725696a.webp`
- Active populated dark 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f0b94025696b.webp`
- History populated dark 1568 after final server reload: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f198ab1ed310.webp` (completed rows show 100%)
- History populated light 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574ef35e30a5df7.webp`
- History populated dark 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574ef3f088a5df8.webp`
- Presets populated light 1568: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f03922f755ba.webp`
- Presets populated dark 1568: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574f02a31f755b9.webp`
- Presets empty light 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574ef97d44a5dfb.webp`
- Presets empty dark 390: `/var/folders/n7/vf79z4pj643f3f630563gscr0000gn/T/omp-sshots-1574efa37c0a5dfc.webp`

Measured overflow behavior: at 390 the history table was 850px wide inside a 340px `.table-scroll` viewport while document/body width remained 390px; at 1024 it was 850px inside a 686px table viewport while document/body remained 1024px; at 1568 the table and viewport were both 1034px and document/body remained 1568px. Thus horizontal overflow is isolated to the table container.

Preset removal confirmation was exercised in the browser. Canceling `window.confirm` preserved the card and exposed: `Remove preset "Browser demo preset"? Its managed inputs will also be removed.` Confirming removed the card and managed bundle.

## Decisions and risks

- Stale history configuration is intentionally rendered as `Unavailable`; the helper catches only `KeyError`, `OSError`, and `ValidationError`.
- No new output-path endpoint or persistence schema was introduced; the prepared configuration is loaded through the existing controller.
- Completed/failed/cancelled history progress uses persisted stage events when no live status maps to a stage; runs without stage events remain at a bounded 0%.
- Browser stylesheet cache was bypassed only during verification with a query-string reload; source static URLs remain unchanged except for the existing dashboard cache-bust query.
