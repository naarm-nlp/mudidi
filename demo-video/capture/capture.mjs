// Captures every dashboard state the demo video shows, plus the element
// positions the cursor and camera aim at. Run against a throwaway copy of the
// MUDIDI data directory: it flips the copied run through its pipeline states.
//
//   node capture/capture.mjs <base-url> <sqlite-db-copy> <dictionary.pdf> <page-6-image>
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const [base, db, pdf, pageImage] = process.argv.slice(2);
const out = join(dirname(fileURLToPath(import.meta.url)), "..", "assets", "cap");
mkdirSync(out, { recursive: true });

const RUN = "run-11916598a55c";
const meta = { frames: {}, rects: {} };
const sql = (statement) => execFileSync("sqlite3", [db, statement]);
const setRun = (run, review) =>
  sql(
    `update runs set status='${run}'; update parse_rule_reviews set status='${review}', ` +
      `approved_at=${review === "approved" ? "'2026-10-09T10:06:44+00:00'" : "NULL"};`,
  );

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 });
await page.route("**/events**", (route) => route.abort());
await page.route("**/demo-source.png", (route) =>
  route.fulfill({ body: readFileSync(pageImage), contentType: "image/png" }),
);

async function fit(min = 720, max = 4000) {
  await page.setViewportSize({ width: 1280, height: 720 });
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: 1280, height: Math.max(min, Math.min(max, height)) });
  await page.waitForTimeout(150);
}
async function shot(name) {
  await page.waitForTimeout(80);
  await page.screenshot({ path: join(out, `${name}.jpg`), type: "jpeg", quality: 88 });
  meta.frames[name] = page.viewportSize();
}
async function rect(key, selector) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no box for ${key}: ${selector}`);
  meta.rects[key] = { x: box.x, y: box.y, w: box.width, h: box.height };
}
async function typeFrames(prefix, selector, text, chunks) {
  const field = page.locator(selector).first();
  const size = Math.ceil(text.length / chunks);
  for (let i = 0, n = 1; i < text.length; i += size, n += 1) {
    await field.pressSequentially(text.slice(i, i + size));
    await shot(`${prefix}-${n}`);
  }
}
const go = (panel) => page.evaluate((p) => document.querySelector(`[data-wizard-go="${p}"]`).click(), panel);

// ---- New run · step 1: input -------------------------------------------------
await page.goto(`${base}/`);
await page.waitForTimeout(600);
await fit(1000);
await rect("in.drop", "[data-dictionary-dropzone]");
await rect("in.pages", 'input[name="dictionary_pages"]');
await rect("tab.context", '[data-wizard-go="context"]');
await rect("tab.model", '[data-wizard-go="model"]');
await rect("tab.agentic", '[data-wizard-go="agentic"]');
await rect("card", "#wizard-input, [data-wizard-panel='input']");
await shot("in-0");
await page.evaluate(() => document.querySelector("[data-dictionary-dropzone]").classList.add("is-dragover"));
await shot("in-drag");
await page.evaluate(() => document.querySelector("[data-dictionary-dropzone]").classList.remove("is-dragover"));
await page.setInputFiles("#dictionary-pdf", pdf);
await page.waitForTimeout(300);
await shot("in-file");
await page.locator('input[name="dictionary_pages"]').click();
await typeFrames("in-pages", 'input[name="dictionary_pages"]', "6-8", 3);

// ---- New run · step 3: dictionary profile ------------------------------------
await go("context");
await page.waitForTimeout(300);
await fit();
const ctx = "[data-wizard-panel='context'] ";
await rect("cx.head", ctx + 'input[name="profile_headword_language"]');
await rect("cx.headScript", ctx + 'input[name="profile_headword_script"]');
await rect("cx.target", ctx + 'input[name="profile_target_languages"]');
await rect("cx.inventory", ctx + 'textarea[name="character_inventory"]');
await rect("cx.layout", ctx + 'textarea[name="profile_page_layout"]');
await rect("cx.types", ctx + ".profile-information-grid");
await shot("cx-0");
await typeFrames("cx-head", ctx + 'input[name="profile_headword_language"]', "Raga", 2);
await typeFrames("cx-hs", ctx + 'input[name="profile_headword_script"]', "Latin", 1);
await typeFrames("cx-tl", ctx + 'input[name="profile_target_languages"]', "English", 2);
await typeFrames("cx-ts", ctx + 'input[name="profile_target_scripts"]', "Latin", 1);
await typeFrames(
  "cx-inv",
  ctx + 'textarea[name="character_inventory"]',
  "Raga-Latin: a b e g h i k l m n o p q r s t u v w ngg th",
  4,
);
await typeFrames(
  "cx-lay",
  ctx + 'textarea[name="profile_page_layout"]',
  "Handwritten notebook, one column of entries on the right-hand page. Later additions sit in the left margin " +
    "and on the facing page. Headwords are underlined; struck-through text is deleted.",
  8,
);
let n = 0;
for (const value of ["translation", "gloss", "part_of_speech", "example", "usage_note", "cross_reference", "variant"]) {
  const box = ctx + `input[name="profile_information_types"][value="${value}"]`;
  await rect(`cx.type${++n}`, box);
  await page.locator(box).check({ force: true });
  await shot(`cx-type-${n}`);
}

// ---- New run · step 4: models -------------------------------------------------
await go("model");
await page.waitForTimeout(500);
await fit();
const card = '[data-subscription-card][data-subscription-provider="openai"] ';
await rect("md.card", card);
await rect("md.login", card + "[data-subscription-login]");
await rect("md.prov1", 'select[name="stage1_provider"]');
await rect("md.prov2", 'select[name="stage2_provider"]');
await rect("md.model1", 'select[name="stage1_model"]');
await rect("md.reason1", 'select[name="stage1_reasoning"]');
await rect("md.model2", '[data-wizard-panel="model"] select[data-model-stage="stage2"]');
await rect("md.reason2", '[data-wizard-panel="model"] .stage-settings:last-of-type select[data-reasoning-select], select[name="stage2_reasoning"], select[name="stage2_pass1_reasoning"]');
await shot("md-0");
// No real login happens here: the signed-in state is written into the page.
const patch = (step) =>
  page.evaluate((step) => {
    const panel = document.querySelector('[data-wizard-panel="model"]');
    const card = document.querySelector('[data-subscription-card][data-subscription-provider="openai"]');
    const pick = (select, label, value) => {
      select.innerHTML = "";
      select.append(new Option(label, value, true, true));
      select.disabled = false;
      select.closest("label")?.querySelectorAll("small").forEach((hint) => hint.remove());
    };
    const summary = (term, text) => {
      for (const row of document.querySelectorAll(".summary dl > div, .summary li, .summary div")) {
        const [dt, dd] = [row.querySelector("dt"), row.querySelector("dd")];
        if (dt && dd && dt.textContent.trim() === term) {
          dd.textContent = text;
          dd.className = "";
        }
      }
    };
    const models = [...panel.querySelectorAll("select[data-model-select]")].filter((s) => s.offsetParent);
    const reasons = [...panel.querySelectorAll("select[data-reasoning-select]")].filter((s) => s.offsetParent);
    if (step === "waiting") card.querySelector("[data-subscription-action-status]").textContent = "Waiting for browser sign-in…";
    if (step === "signed") {
      card.dataset.subscriptionAuthenticated = "true";
      const state = card.querySelector("[data-subscription-state]");
      state.dataset.state = "signed-in";
      state.textContent = "Signed in";
      card.querySelector("[data-subscription-account]").textContent = "you@example.com";
      card.querySelector("[data-subscription-action-status]").textContent = "";
      card.querySelector("[data-subscription-logout]").disabled = false;
      for (const el of document.querySelectorAll(".topbar *"))
        if (!el.children.length && el.textContent.includes("0 of 3")) el.textContent = el.textContent.replace("0 of 3", "1 of 3");
      panel.querySelector("[data-model-status]").textContent = "Models loaded from your OpenAI account";
    }
    if (step === "prov1") { pick(panel.querySelector('select[name="stage1_provider"]'), "OpenAI", "openai"); pick(models[0], "Choose a model", ""); }
    if (step === "prov2") { pick(panel.querySelector('select[name="stage2_provider"]'), "OpenAI", "openai"); pick(models[1], "Choose a model", ""); }
    if (step === "model1") { pick(models[0], "gpt-6.1-sol", "gpt-6.1-sol"); summary("Stage 1", "gpt-6.1-sol"); }
    if (step === "reason1") pick(reasons[0], "Low", "low");
    if (step === "model2") { pick(models[1], "gpt-6.1-sol", "gpt-6.1-sol"); summary("Stage 2", "gpt-6.1-sol"); }
    if (step === "reason2") pick(reasons[1], "High", "high");
  }, step);
// Each patch can shift the layout below it (hints disappear), so measure the
// control the cursor is about to click in the layout it will be clicked in.
const panelSel = '[data-wizard-panel="model"] ';
const clickTargets = {
  prov1: [panelSel + 'select[name="stage1_provider"]', 0],
  prov2: [panelSel + 'select[name="stage2_provider"]', 0],
  model1: [panelSel + "select[data-model-select]:visible", 0],
  reason1: [panelSel + "select[data-reasoning-select]:visible", 0],
  model2: [panelSel + "select[data-model-select]:visible", 1],
  reason2: [panelSel + "select[data-reasoning-select]:visible", 1],
};
for (const step of ["waiting", "signed", "prov1", "prov2", "model1", "reason1", "model2", "reason2"]) {
  if (clickTargets[step]) {
    const [selector, index] = clickTargets[step];
    const box = await page.locator(selector).nth(index).boundingBox();
    meta.rects[`md.${step}`] = { x: box.x, y: box.y, w: box.width, h: box.height };
  }
  await patch(step);
  await shot(`md-${step}`);
}
await rect("md.next", '[data-wizard-next="agentic"]');

// ---- New run · step 5: agentic verification -----------------------------------
await go("agentic");
await page.waitForTimeout(300);
const agentic = '[data-wizard-panel="agentic"] input[name="agentic"]';
await page.locator(agentic + '[value="true"]').check({ force: true });
await page.waitForTimeout(300);
// Toggling the loop makes the page recompute fields from the (unauthenticated)
// catalog, so restore the signed-in OpenAI values written in the model step.
const restore = () =>
  page.evaluate(() => {
    const one = (select, label) => {
      select.innerHTML = "";
      select.append(new Option(label, label, true, true));
      select.closest("label")?.querySelectorAll("small").forEach((hint) => hint.remove());
    };
    const panel = document.querySelector('[data-wizard-panel="agentic"]');
    panel.querySelectorAll("select[data-agentic-provider]").forEach((select) => one(select, "OpenAI"));
    panel.querySelectorAll("select[data-agentic-model]").forEach((select) => one(select, "gpt-6.1-sol"));
    for (const row of document.querySelectorAll(".summary dl > div")) {
      const [dt, dd] = [row.querySelector("dt"), row.querySelector("dd")];
      if (dt && dd && /^Stage [12]$/.test(dt.textContent.trim())) {
        dd.textContent = "gpt-6.1-sol";
        dd.className = "";
      }
    }
  });
await restore();
await fit();
await rect("ag.on", agentic + '[value="true"]');
await rect("ag.submit", "[data-wizard-submit]");
await shot("ag-on");
await page.locator(agentic + '[value="false"]').check({ force: true });
await page.waitForTimeout(200);
await restore();
await shot("ag-0");

// ---- Run overview: pipeline progress -------------------------------------------
const pipeline = (view) =>
  page.evaluate((view) => {
    const card = document.querySelector(".run-progress-card");
    card.querySelector(".progress-summary span").textContent = view.summary;
    card.querySelector(".progress-summary strong").textContent = `${view.pct}%`;
    card.querySelector(".progress-track span").style.width = `${view.pct}%`;
    const subtitle = document.querySelector(".topbar p, .page-heading p");
    if (subtitle) subtitle.textContent = view.subtitle;
    card.querySelectorAll(".pipeline-step").forEach((step, i) => {
      const state = view.steps[i];
      step.className = `pipeline-step ${state}`;
      step.querySelector(".pipeline-marker").textContent = state === "completed" ? "✓" : state === "running" ? "→" : "○";
      step.querySelector(".pipeline-state").textContent =
        state === "completed" ? "Complete" : state === "running" ? "In progress" : "Future";
      const detail = step.querySelector("p");
      if (detail && view.details[i] !== undefined) detail.textContent = view.details[i];
      step.querySelector(".pipeline-action")?.remove();
      if (view.action === i) {
        const link = document.createElement("a");
        link.className = "primary pipeline-action";
        link.textContent = "Review MDF parsing guide →";
        step.querySelector(".pipeline-step-content").append(link);
      }
    });
    card.querySelector(".run-actions")?.remove();
  }, view);
const C = "completed", R = "running", P = "pending";
const overview = [
  ["ov-s1-0", "running_stage1", "awaiting_review", { summary: "0 of 3 pages complete", pct: 0, subtitle: "Stage 1 — Transcription · 0 of 3 pages", steps: [R, P, P, P], details: ["0 of 3 pages complete", "Waiting for transcription", undefined, "0 of 3 pages complete"] }],
  ["ov-s1-1", "running_stage1", "awaiting_review", { summary: "1 of 3 pages complete", pct: 33, subtitle: "Stage 1 — Transcription · 1 of 3 pages", steps: [R, P, P, P], details: ["1 of 3 pages complete", "Waiting for transcription", undefined, "0 of 3 pages complete"] }],
  ["ov-s1-2", "running_stage1", "awaiting_review", { summary: "2 of 3 pages complete", pct: 67, subtitle: "Stage 1 — Transcription · 2 of 3 pages", steps: [R, P, P, P], details: ["2 of 3 pages complete", "Waiting for transcription", undefined, "0 of 3 pages complete"] }],
  ["ov-disc", "discovering_parse_rules", "awaiting_review", { summary: "3 of 3 pages transcribed", pct: 100, subtitle: "MDF parsing guide discovery", steps: [C, R, P, P], details: ["3 of 3 pages complete", "Inferring markers and entry structure", undefined, "0 of 3 pages complete"] }],
  ["ov-review", "awaiting_parse_rules_review", "awaiting_review", { summary: "0 of 3 pages complete", pct: 0, subtitle: "Review parsing guide · 0 of 3 pages", steps: [C, C, R, P], details: ["3 of 3 pages complete", "Guide ready for review", "Approval is required before MDF conversion", "0 of 3 pages complete"], action: 2 }],
  ["ov-s2-1", "running_stage2", "approved", { summary: "1 of 3 pages complete", pct: 33, subtitle: "Stage 2 — MDF conversion · 1 of 3 pages", steps: [C, C, C, R], details: ["3 of 3 pages complete", "Guide ready for review", undefined, "1 of 3 pages complete"] }],
  ["ov-s2-2", "running_stage2", "approved", { summary: "2 of 3 pages complete", pct: 67, subtitle: "Stage 2 — MDF conversion · 2 of 3 pages", steps: [C, C, C, R], details: ["3 of 3 pages complete", "Guide ready for review", undefined, "2 of 3 pages complete"] }],
];
try {
  for (const [name, runStatus, reviewStatus, view] of overview) {
    setRun(runStatus, reviewStatus);
    await page.goto(`${base}/runs/${RUN}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(400);
    await page.setViewportSize({ width: 1280, height: 900 });
    await pipeline(view);
    if (name === "ov-review") await rect("ov.action", ".pipeline-action");
    if (name === "ov-s1-0") await rect("ov.card", ".run-progress-card");
    await shot(name);
  }
  // The guide page is the real awaiting-review screen.
  setRun("awaiting_parse_rules_review", "awaiting_review");
  await page.goto(`${base}/runs/${RUN}/parse-rules`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(600);
  await page.setViewportSize({ width: 1280, height: 1500 });
  await page.waitForTimeout(200);
  await rect("gd.approve", "button:has-text('Approve and continue')");
  await shot("gd-0");
} finally {
  setRun("completed", "approved");
}
await page.goto(`${base}/runs/${RUN}`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(400);
await page.setViewportSize({ width: 1280, height: 900 });
await page.evaluate(() => document.querySelector(".run-actions")?.remove());
await rect("ov.pagesTab", '.detail-tabs a:has-text("Page Viewer")');
await shot("ov-done");

// ---- Page viewer and editor ------------------------------------------------------
await page.goto(`${base}/runs/${RUN}/pages/page_6`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(600);
await page.evaluate(() => {
  const frame = document.querySelector(".source-document, .source-preview");
  const image = document.createElement("img");
  image.src = "/demo-source.png";
  image.style.cssText = `display:block;width:100%;height:${frame.getBoundingClientRect().height}px;object-fit:cover;object-position:60% 30%`;
  frame.replaceWith(image);
  document.querySelector("details")?.remove();
});
await page.waitForTimeout(500);
await fit(900);
const mdf = "textarea[name*='stage2'], textarea[name*='mdf']";
await rect("pg.source", "img[src='/demo-source.png']");
await rect("pg.mdf", mdf);
await rect("pg.save", "button:has-text('Save page changes')");
await shot("pg-0");
const edit = (text) =>
  page.evaluate(([selector, text]) => {
    const area = document.querySelector(selector);
    area.value = area.value.replace(/\\ge (one ?)?(one che)?cheek|\\ge one\b.*/m, text);
  }, [mdf, text]);
await edit("\\ge one");
await shot("pg-edit-1");
await page.evaluate(([selector]) => {
  const area = document.querySelector(selector);
  area.value = area.value.replace("\\ge one", "\\ge one cheek");
}, [mdf]);
await shot("pg-edit-2");
// Saving is not performed: the copied run still points at the real output folder.

writeFileSync(join(out, "meta.json"), JSON.stringify(meta, null, 1));
await browser.close();
console.log(`captured ${Object.keys(meta.frames).length} frames`);
