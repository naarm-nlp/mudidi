// Writes index.html (the HyperFrames composition) from the captured frames in
// assets/cap. Edit the timings and captions here, then run:
//
//   node capture/build.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const meta = JSON.parse(readFileSync(join(root, "assets/cap/meta.json"), "utf8"));

// ---------------------------------------------------------------------------
// The schedule. Each scene is a short script of steps; every step advances the
// clock `t`, so pacing is changed by editing a duration and nothing overlaps.
// The music is 150 BPM (one beat = 0.4s) and every scene cut is snapped to a beat.
// ---------------------------------------------------------------------------
const BEAT = 0.4;
const W = 1920;
const R = meta.rects;
const round = (n) => +n.toFixed(3);
const ops = []; // timeline operations replayed by the page script
const scenes = [];
const captions = [];
let t = 0;
let view = null; // current camera view: {x, y, w} in dashboard CSS pixels
let scene = null;

const centre = (target) => (typeof target === "string" ? [R[target].x + R[target].w / 2, R[target].y + R[target].h / 2] : target);
const tween = (kind, sel, a, b) => ops.push({ k: kind, sel, a, b, t: round(t) });
const wait = (seconds) => { t += seconds; };
const frame = (name) => { scene.frames.push(name); ops.push({ k: "frame", name, t: round(t) }); };
function cut(id, first, startView, fade = true) {
  scene = { id, start: round(t), frames: [first] };
  scenes.push(scene);
  view = startView;
  ops.push({ k: "cam", scene: id, view, d: 0, t: round(t) });
  if (fade) tween("fromTo", `#stage-${id}`, { opacity: 0 }, { opacity: 1, duration: 0.3, ease: "power1.out" });
}
function endScene() {
  t = Math.ceil(round(t) / BEAT - 1e-6) * BEAT;
  scene.end = round(t);
}
function pan(next, seconds, hold = true) {
  ops.push({ k: "cam", scene: scene.id, view: next, d: seconds, t: round(t) });
  view = next;
  if (hold) t += seconds;
}
// Move the cursor to a named control (it then stays on that control while the
// camera pans) or to [x, y] in dashboard pixels (it then rests in place).
function go(target, seconds = 0.7) {
  ops.push({ k: "cursor", to: centre(target), d: seconds, rest: typeof target !== "string", t: round(t) });
  t += seconds;
}
const goScreen = (x, y, seconds) => go([view.x + (x * view.w) / W, view.y + (y * view.w) / W], seconds);
const onScreen = (target) => { const [x, y] = centre(target); return { x: ((x - view.x) * W) / view.w, y: ((y - view.y) * W) / view.w }; };
// Settle on the control, press, and show what the press changed.
function click(result) {
  t += 0.18;
  ops.push({ k: "click", t: round(t) });
  t += 0.14;
  if (result) frame(result);
}
function caption(text, start, end) {
  captions.push([round(start), round(end), text]);
}
const inOut = "power2.inOut";

// ---- 0.0 Intro: a notebook page is scanned into MDF records (fixed 7.2s) --------
const intro = (time, kind, sel, a, b) => ops.push({ k: kind, sel, a, b, t: time });
intro(0.1, "from", "#scan-sheet", { opacity: 0, y: 70, duration: 0.7, ease: "power3.out" });
intro(0.5, "from", "#scan-eyebrow", { opacity: 0, duration: 0.5, ease: "power2.out" });
intro(0.9, "fromTo", "#scan-line", { y: -10, opacity: 0 }, { opacity: 1, duration: 0.2 });
intro(1.0, "to", "#scan-line", { y: 900, duration: 3.6, ease: "none" });
intro(4.5, "to", "#scan-line", { opacity: 0, duration: 0.3 });
// The line reaches each entry at 1.0 + 3.6 * (entry top / 900).
for (const [n, time, rows] of [[1, 1.95, ["a", "b", "c"]], [2, 2.7, ["a", "b"]], [3, 4.15, ["a", "b", "c"]]]) {
  intro(time, "fromTo", `#scan-box-${n}`, { opacity: 0, scaleX: 0.3 }, { opacity: 1, scaleX: 1, duration: 0.3, ease: "power3.out" });
  intro(time + 0.1, "to", `#scan-link-${n}`, { strokeDashoffset: 0, duration: 0.4, ease: inOut });
  intro(time + 0.3, "from", `#scan-record-${n}`, { opacity: 0, x: 40, duration: 0.35, ease: "power3.out" });
  rows.forEach((row, i) =>
    intro(round(time + 0.45 + i * 0.22), "fromTo", `#scan-r${n}${row}`, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: 0.3, ease: "steps(10)" }));
}
intro(5.3, "from", "#scan-title", { opacity: 0, y: 40, duration: 0.6, ease: "power3.out" });
intro(6.85, "to", ["#scan-sheet", "#scan-links", "#scan-copy"], { opacity: 0, duration: 0.3, ease: "power1.in" });
t = 7.2;
const cursorStart = t;

// ---- Upload the PDF and choose pages -----------------------------------------------
cut("input", "in-0", { x: 200, y: 240, w: 1080 });
let mark = t;
wait(0.3);
const chipStart = t;
tween("fromTo", "#chip-body", { x: 70, y: 730, opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.35, ease: "power2.out" });
goScreen(250, 800, 0.7);
click();
wait(0.15);
const drop = onScreen("in.drop");
tween("to", "#chip-body", { x: drop.x - 180, y: drop.y - 70, duration: 1.1, ease: inOut });
go("in.drop", 1.1);
ops.push({ k: "frame", name: "in-drag", t: round(t - 0.35) });
scene.frames.push("in-drag");
wait(0.25);
tween("to", "#chip-body", { opacity: 0, scale: 0.7, duration: 0.25, ease: "power2.in" });
click("in-file");
const chipEnd = t + 0.3;
wait(0.7);
pan({ x: 250, y: 330, w: 940 }, 0.7, false);
go("in.pages", 0.8);
click();
for (const name of ["in-pages-1", "in-pages-2", "in-pages-3"]) { wait(0.22); frame(name); }
wait(0.8);
pan({ x: 200, y: 130, w: 1080 }, 0.7);
go("tab.context", 0.8);
click();
caption("Drop in the scanned PDF and pick the pages.", mark + 0.2, t - 0.1);
wait(0.6);
endScene();

// ---- Dictionary profile -------------------------------------------------------------
cut("context", "cx-0", { x: 200, y: 130, w: 1080 }, false);
mark = t;
wait(0.3);
pan({ x: 240, y: 440, w: 960 }, 0.8);
go("cx.head", 0.6);
click();
for (const name of ["cx-head-1", "cx-head-2"]) { wait(0.18); frame(name); }
go("cx.headScript", 0.4);
click("cx-hs-1");
go("cx.target", 0.45);
click();
for (const name of ["cx-tl-1", "cx-tl-2"]) { wait(0.18); frame(name); }
wait(0.3);
frame("cx-ts-1");
go("cx.inventory", 0.5);
click();
for (const name of [1, 2, 3, 4].map((i) => `cx-inv-${i}`)) { wait(0.2); frame(name); }
wait(0.4);
pan({ x: 240, y: 760, w: 960 }, 0.7);
go("cx.layout", 0.5);
click();
for (const name of [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `cx-lay-${i}`)) { wait(0.17); frame(name); }
wait(0.3);
go("cx.type1", 0.45);
ops.push({ k: "cursor", to: centre("cx.type7"), d: 1.2, rest: false, t: round(t + 0.1) });
for (let i = 1; i <= 7; i += 1) { wait(0.17); frame(`cx-type-${i}`); }
wait(0.2);
wait(0.5);
caption("Describe the dictionary: languages, layout, what an entry holds.", mark + 0.2, t);
pan({ x: 240, y: R["cx.next"].y - 380, w: 960 }, 1.0);
go("cx.next", 0.7);
click();
wait(0.6);
endScene();

// ---- Sign in, then choose a model and reasoning for each stage -----------------------
cut("model", "md-0", { x: 240, y: 640, w: 960 });
mark = t;
wait(0.4);
go("md.login", 0.8);
click("md-waiting");
wait(0.3);
const popupStart = t;
tween("fromTo", "#popup-shade", { opacity: 0 }, { opacity: 1, duration: 0.25, ease: "power1.out" });
tween("fromTo", "#popup-window", { opacity: 0, scale: 0.92, y: 30 }, { opacity: 1, scale: 1, y: 0, duration: 0.35, ease: "power3.out" });
wait(0.6);
goScreen(960, 580, 0.7);
click();
tween("to", "#popup-choose", { opacity: 0, duration: 0.15 });
wait(0.15);
tween("to", "#popup-done", { opacity: 1, duration: 0.2 });
tween("fromTo", "#done-mark", { scale: 0.4 }, { scale: 1, duration: 0.4, ease: "back.out(2)" });
wait(1.2);
frame("md-signed");
tween("to", "#popup-window", { opacity: 0, scale: 0.95, duration: 0.25, ease: "power2.in" });
tween("to", "#popup-shade", { opacity: 0, duration: 0.25 });
const popupEnd = t + 0.35;
wait(1.0);
caption("Sign in with a subscription you already have.", mark + 0.2, t);
go("md.prov1", 0.8);
click("md-prov1");
wait(0.5);
go("md.prov2", 0.6);
click("md-prov2");
wait(0.6);
mark = t;
pan({ x: 240, y: 1060, w: 960 }, 0.8);
wait(0.3);
for (const [control, result] of [["md.model1", "md-model1"], ["md.reason1", "md-reason1"], ["md.model2", "md-model2"], ["md.reason2", "md-reason2"]]) {
  go(control, 0.75);
  click(result);
  wait(0.75);
}
wait(0.3);
caption("One model, tuned per stage: low reasoning to transcribe, high to parse.", mark + 0.1, t);
pan({ x: 240, y: 1200, w: 960 }, 0.6);
go("md.next", 0.7);
click();
wait(0.5);
endScene();

// ---- Agentic loop on -------------------------------------------------------------------
cut("agentic", "ag-0", { x: 200, y: 330, w: 1080 }, false);
mark = t;
wait(0.4);
go("ag.on", 0.8);
click("ag-on");
wait(1.4);
pan({ x: 200, y: R["ag.submit"].y - 470, w: 1080 }, 1.4);
caption("Agentic loop on: a second model evaluates each page and re-iterates.", mark + 0.3, t);
wait(0.3);
go("ag.submit", 0.7);
click();
wait(0.5);
endScene();

// ---- Run: Stage 1 and guide discovery -----------------------------------------------------
cut("run1", "ov-s1-0", { x: 180, y: 230, w: 1100 });
mark = t;
go([760, 520], 0.6);
for (const name of ["ov-s1-1", "ov-s1-2", "ov-disc", "ov-review"]) { wait(0.9); frame(name); }
wait(0.4);
go("ov.action", 0.7);
click();
caption("Stage 1 transcribes each page, then infers an MDF parsing guide.", mark + 0.2, t);
wait(0.5);
endScene();

// ---- Review the MDF parsing guide ------------------------------------------------------------
cut("guide", "gd-0", { x: 200, y: 0, w: 1080 });
mark = t;
go([900, 400], 0.5);
wait(0.3);
pan({ x: 200, y: 892, w: 1080 }, 2.8);
caption("You review the guide before anything is parsed.", mark + 0.2, t);
wait(0.2);
go("gd.approve", 0.7);
click();
wait(0.5);
endScene();

// ---- Run: Stage 2 --------------------------------------------------------------------------------
cut("run2", "ov-s2-1", { x: 180, y: 215, w: 1100 });
mark = t;
go([760, 560], 0.5);
wait(0.5);
frame("ov-s2-2");
wait(0.8);
frame("ov-done");
wait(0.6);
caption("Stage 2 converts every page to MDF.", mark + 0.2, t);
go("ov.pagesTab", 0.7);
click();
wait(0.5);
endScene();

// ---- Page viewer and editor ------------------------------------------------------------------------
cut("pages", "pg-0", { x: 240, y: 330, w: 1040 });
mark = t;
go([520, 640], 0.6);
wait(0.5);
pan({ x: 240, y: 560, w: 1040 }, 1.0);
wait(0.7);
go([876, 960], 0.7);
click();
wait(0.2);
frame("pg-edit-1");
wait(0.3);
frame("pg-edit-2");
wait(0.9);
caption("Check each page against the scan, and fix what you see.", mark + 0.2, t);
pan({ x: 240, y: 700, w: 1040 }, 0.6);
go("pg.save", 0.7);
click();
wait(1.0);
endScene();
const cursorEnd = t;

// ---- Outro (4.8s) -------------------------------------------------------------------------------------
const outroStart = t;
t += 0.15; tween("from", "#outro-word", { opacity: 0, y: 60, duration: 0.7, ease: "power3.out" });
t += 0.35; tween("from", "#outro-rule", { scaleX: 0, duration: 0.8, ease: inOut });
t += 0.4; tween("from", "#outro-line", { opacity: 0, y: 24, duration: 0.6, ease: "power2.out" });
t += 0.5; tween("from", "#outro-local", { opacity: 0, duration: 0.6, ease: "power2.out" });
t = outroStart + 3.9; tween("to", "#outro-copy", { opacity: 0, duration: 0.8, ease: "power1.in" });
const DURATION = round(outroStart + 4.8);
captions.forEach(([start, end], i) => {
  ops.push({ k: "fromTo", sel: `#caption-text-${i}`, a: { opacity: 0, y: 24 }, b: { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" }, t: start });
  ops.push({ k: "to", sel: `#caption-text-${i}`, a: { opacity: 0, duration: 0.2, ease: "power1.in" }, t: round(end - 0.2) });
});
writeFileSync(join(root, "assets/timing.json"), JSON.stringify({ duration: DURATION, outroStart: round(outroStart), drop: 7.2 }));
console.log(scenes.map((s) => `${s.id} ${s.start}-${s.end}`).join(" | "), "| total", DURATION);

const dur = (a, b) => +(b - a).toFixed(3);
const sceneHtml = scenes
  .map((scene) => {
    const height = meta.frames[scene.frames[0]].height;
    const frames = scene.frames
      .map((name, i) => `          <img id="f-${name}" class="frame" src="assets/cap/${name}.jpg" alt="" style="opacity: ${i ? 0 : 1}" />`)
      .join("\n");
    return `      <section id="scene-${scene.id}" class="clip scene" data-start="${scene.start}" data-duration="${dur(scene.start, scene.end)}" data-track-index="1">
        <div id="stage-${scene.id}" class="stage" data-layout-allow-overflow style="height: ${height}px">
${frames}
        </div>
      </section>`;
  })
  .join("\n");
const captionHtml = captions
  .map(
    ([start, end, text], i) => `      <div id="caption-${i}" class="clip caption-slot" data-start="${start}" data-duration="${dur(start, end)}" data-track-index="3">
        <p id="caption-text-${i}" class="caption">${text}</p>
      </div>`,
  )
  .join("\n");

const html = `<!doctype html>
<html lang="en" data-resolution="landscape">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <title>MUDIDI demo</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      @font-face { font-family: "Archivo"; font-weight: 400 800; src: url("assets/fonts/Archivo-400-800-latin.woff2") format("woff2"); }
      @font-face { font-family: "IBM Plex Mono"; font-weight: 400; src: url("assets/fonts/IBMPlexMono-400-latin.woff2") format("woff2"); }
      @font-face { font-family: "IBM Plex Mono"; font-weight: 500; src: url("assets/fonts/IBMPlexMono-500-latin.woff2") format("woff2"); }
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body { width: 1920px; height: 1080px; overflow: hidden; background: #f5f1ea; }
      #root { position: relative; width: 100%; height: 100%; overflow: hidden; background: #f5f1ea; color: #231d18; font-family: "Archivo", sans-serif; }
      .clip { position: absolute; inset: 0; }
      .scene { overflow: hidden; background: #f5f1ea; }
      .stage { position: absolute; top: 0; left: 0; width: 1280px; transform-origin: 0 0; }
      .frame { position: absolute; top: 0; left: 0; width: 1280px; display: block; }
      .eyebrow { font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 26px; letter-spacing: 0.12em; text-transform: uppercase; color: #a35c1a; }

      /* Intro: one notebook page becomes MDF records. The sheet shows a crop of the page photo. */
      #scan-sheet { position: absolute; left: 110px; top: 92px; width: 700px; height: 896px; overflow: hidden; background: #fffaf2; border: 1.5px solid #231d18; box-shadow: 0 30px 70px rgba(35, 29, 24, 0.3); }
      #scan-page { position: absolute; left: -187px; top: -37px; width: 933px; height: 1244px; display: block; }
      .scan-box { position: absolute; height: 40px; border: 3px solid #a35c1a; background: rgba(163, 92, 26, 0.16); transform-origin: 0 50%; }
      #scan-box-1 { left: 76px; top: 218px; width: 426px; }
      #scan-box-2 { left: 82px; top: 404px; width: 355px; }
      #scan-box-3 { left: 82px; top: 770px; width: 364px; }
      #scan-line { position: absolute; left: 0; top: 0; width: 100%; height: 6px; background: #a35c1a; box-shadow: 0 0 40px 18px rgba(163, 92, 26, 0.35); }
      #scan-links { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; }
      .scan-link { fill: none; stroke: #a35c1a; stroke-width: 3; stroke-dasharray: 1; stroke-dashoffset: 1; }
      #scan-copy { position: absolute; left: 1000px; top: 92px; width: 810px; }
      .scan-record { position: absolute; left: 0; width: 100%; padding: 18px 26px; background: #fffaf2; border: 1.5px solid #231d18; border-left: 10px solid #a35c1a; }
      #scan-record-1 { top: 88px; }
      #scan-record-2 { top: 328px; }
      #scan-record-3 { top: 526px; }
      .scan-row { font-family: "IBM Plex Mono", monospace; font-size: 38px; line-height: 1.35; color: #231d18; white-space: nowrap; }
      .scan-row b { font-weight: 500; color: #a35c1a; }
      #scan-title { position: absolute; left: 0; top: 742px; width: 100%; font-size: 76px; line-height: 1; font-weight: 800; letter-spacing: -0.03em; }

      /* Overlays drawn above the dashboard */
      #chip-body { position: absolute; left: 0; top: 0; display: flex; align-items: center; gap: 18px; width: 330px; padding: 18px 22px; background: #fffaf2; border: 1.5px solid #231d18; box-shadow: 0 18px 36px rgba(35, 29, 24, 0.3); }
      #chip-icon { width: 54px; height: 66px; flex: none; background: #a35c1a; color: #fffaf2; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 8px; font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 18px; }
      #chip-name { font-size: 30px; font-weight: 700; }
      #chip-size { margin-top: 2px; font-family: "IBM Plex Mono", monospace; font-size: 20px; color: #5b5048; }
      #popup-shade { position: absolute; inset: 0; opacity: 0; background: rgba(35, 29, 24, 0.45); }
      #popup-window { position: absolute; left: 540px; top: 200px; width: 840px; height: 560px; background: #ffffff; border: 1.5px solid #231d18; box-shadow: 0 40px 90px rgba(35, 29, 24, 0.45); overflow: hidden; }
      #popup-bar { display: flex; align-items: center; gap: 12px; height: 62px; padding: 0 22px; background: #ebe3d6; border-bottom: 1.5px solid #231d18; font-family: "IBM Plex Mono", monospace; font-size: 22px; color: #5b5048; }
      .dot { width: 14px; height: 14px; border-radius: 50%; background: #b9ab99; }
      .popup-body { position: absolute; left: 0; right: 0; top: 62px; bottom: 0; padding: 56px 70px; }
      .popup-title { font-size: 50px; font-weight: 800; letter-spacing: -0.02em; }
      .popup-note { margin-top: 12px; font-size: 28px; color: #5b5048; }
      #account-row { margin-top: 44px; display: flex; align-items: center; gap: 24px; padding: 24px 28px; border: 1.5px solid #231d18; background: #fffaf2; }
      #account-avatar { width: 68px; height: 68px; flex: none; border-radius: 50%; background: #231d18; color: #f5f1ea; display: flex; align-items: center; justify-content: center; font-size: 32px; font-weight: 700; }
      #account-mail { font-size: 34px; font-weight: 700; }
      #account-plan { margin-top: 4px; font-family: "IBM Plex Mono", monospace; font-size: 22px; color: #5b5048; }
      #popup-done { text-align: center; padding-top: 80px; }
      #done-mark { width: 110px; height: 110px; margin: 0 auto 30px; border-radius: 50%; background: #2f8f4e; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 64px; font-weight: 800; }
      .caption-slot { display: flex; align-items: flex-end; justify-content: center; padding-bottom: 64px; pointer-events: none; }
      .caption { max-width: 1560px; padding: 20px 38px 22px; background: #231d18; color: #f5f1ea; font-size: 42px; font-weight: 600; line-height: 1.2; letter-spacing: -0.01em; text-align: center; border-left: 10px solid #a35c1a; box-shadow: 0 16px 40px rgba(35, 29, 24, 0.35); }
      #cursor-arrow { position: absolute; left: 0; top: 0; width: 0; height: 0; }
      #cursor-svg { position: absolute; left: -4px; top: -3px; width: 46px; height: 58px; display: block; filter: drop-shadow(0 4px 6px rgba(0, 0, 0, 0.35)); transform-origin: 4px 3px; }
      #ripple { position: absolute; left: -45px; top: -45px; width: 90px; height: 90px; border-radius: 50%; border: 5px solid #a35c1a; background: rgba(163, 92, 26, 0.18); opacity: 0; }

      /* Outro */
      #scene-outro { background: #2a221c; color: #f5f1ea; }
      #outro-copy { position: absolute; left: 160px; right: 160px; top: 300px; }
      #outro-word { font-size: 230px; line-height: 0.9; font-weight: 800; letter-spacing: -0.04em; }
      #outro-rule { margin-top: 36px; width: 100%; height: 8px; background: #a35c1a; transform-origin: 0 50%; }
      #outro-line { margin-top: 44px; font-size: 56px; font-weight: 600; color: #f5f1ea; }
      #outro-local { margin-top: 26px; display: flex; align-items: center; gap: 16px; font-family: "IBM Plex Mono", monospace; font-size: 30px; letter-spacing: 0.08em; text-transform: uppercase; color: #d8cbb8; }
      #outro-dot { width: 18px; height: 18px; background: #3ec46d; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="${DURATION}" data-width="1920" data-height="1080">
      <section id="scene-intro" class="clip scene" data-start="0" data-duration="7.2" data-track-index="1">
        <div id="scan-sheet">
          <img id="scan-page" src="assets/pages/p7.jpg" alt="" />
          <div id="scan-box-1" class="scan-box"></div>
          <div id="scan-box-2" class="scan-box"></div>
          <div id="scan-box-3" class="scan-box"></div>
          <div id="scan-line"></div>
        </div>
        <svg id="scan-links" viewBox="0 0 1920 1080" aria-hidden="true">
          <path id="scan-link-1" class="scan-link" pathLength="1" d="M 612 330 C 800 330, 820 262, 1000 262" />
          <path id="scan-link-2" class="scan-link" pathLength="1" d="M 547 516 C 780 516, 800 480, 1000 480" />
          <path id="scan-link-3" class="scan-link" pathLength="1" d="M 556 882 C 800 882, 820 700, 1000 700" />
        </svg>
        <div id="scan-copy">
          <p id="scan-eyebrow" class="eyebrow">Raga notebook · page 7</p>
          <div id="scan-record-1" class="scan-record">
            <p id="scan-r1a" class="scan-row"><b>\\lx</b> bea</p>
            <p id="scan-r1b" class="scan-row"><b>\\ge</b> water, liquid, juice</p>
            <p id="scan-r1c" class="scan-row"><b>\\cf</b> Fl. bea</p>
          </div>
          <div id="scan-record-2" class="scan-record">
            <p id="scan-r2a" class="scan-row"><b>\\lx</b> beku</p>
            <p id="scan-r2b" class="scan-row"><b>\\ge</b> hole, grave</p>
          </div>
          <div id="scan-record-3" class="scan-record">
            <p id="scan-r3a" class="scan-row"><b>\\lx</b> bilau</p>
            <p id="scan-r3b" class="scan-row"><b>\\ge</b> to steal</p>
            <p id="scan-r3c" class="scan-row"><b>\\cf</b> pilau</p>
          </div>
          <h1 id="scan-title">Handwriting in. Structured MDF out.</h1>
        </div>
      </section>
${sceneHtml}
      <section id="scene-outro" class="clip scene" data-start="${round(outroStart)}" data-duration="4.8" data-track-index="1">
        <div id="outro-copy">
          <p id="outro-word">MUDIDI</p>
          <div id="outro-rule"></div>
          <p id="outro-line">Dictionary digitization, with you in the loop.</p>
          <p id="outro-local"><span id="outro-dot"></span><span>Runs on your own machine</span></p>
        </div>
      </section>

      <div id="chip" class="clip" data-start="${round(chipStart)}" data-duration="${dur(chipStart, chipEnd)}" data-track-index="2">
        <div id="chip-body">
          <div id="chip-icon">PDF</div>
          <div><p id="chip-name">Raga1.pdf</p><p id="chip-size">101 pages</p></div>
        </div>
      </div>
      <div id="popup" class="clip" data-start="${round(popupStart)}" data-duration="${dur(popupStart, popupEnd)}" data-track-index="2">
        <div id="popup-shade"></div>
        <div id="popup-window">
          <div id="popup-bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Sign in · OpenAI account</span></div>
          <div id="popup-choose" class="popup-body">
            <p class="popup-title">Choose an account</p>
            <p class="popup-note">to continue to MUDIDI</p>
            <div id="account-row">
              <div id="account-avatar">Y</div>
              <div><p id="account-mail">you@example.com</p><p id="account-plan">ChatGPT subscription</p></div>
            </div>
          </div>
          <div id="popup-done" class="popup-body">
            <div id="done-mark">✓</div>
            <p class="popup-title">Signed in</p>
            <p class="popup-note">You can close this window and return to MUDIDI.</p>
          </div>
        </div>
      </div>
${captionHtml}
      <div id="cursor" class="clip" data-start="${cursorStart}" data-duration="${dur(cursorStart, cursorEnd)}" data-track-index="4">
        <div id="cursor-arrow">
          <div id="ripple"></div>
          <svg id="cursor-svg" viewBox="0 0 46 58" aria-hidden="true"><path d="M4 3 L4 45 L15 35 L23 54 L31 50.5 L23 32 L38 32 Z" fill="#231d18" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" /></svg>
        </div>
      </div>
      <audio id="music" src="assets/music.m4a" data-start="0" data-duration="${DURATION}" data-track-index="5" data-volume="0.85"></audio>
    </div>
    <script>
      // Everything here replays the schedule computed in capture/build.mjs.
      const OPS = ${JSON.stringify(ops)};
      const tl = gsap.timeline({ paused: true });
      const W = 1920;
      const camEvents = [];
      const cursorEvents = [];
      // A camera view is {x, y, w} in dashboard CSS pixels; its height is w * 9 / 16.
      const camProps = (view) => ({ x: (-view.x * W) / view.w, y: (-view.y * W) / view.w, scale: W / view.w });
      gsap.set("#popup-done", { opacity: 0 });
      for (const op of OPS) {
        if (op.k === "cam") {
          const stage = "#stage-" + op.scene;
          if (op.d) tl.to(stage, { ...camProps(op.view), duration: op.d, ease: "power2.inOut" }, op.t);
          else tl.set(stage, camProps(op.view), op.t);
          camEvents.push({ t: op.t, props: camProps(op.view), duration: op.d });
        } else if (op.k === "cursor") {
          cursorEvents.push({ t: op.t, to: op.to, duration: op.d, rest: op.rest });
        } else if (op.k === "click") {
          tl.to("#cursor-svg", { scale: 0.8, duration: 0.09, yoyo: true, repeat: 1, ease: "power1.inOut" }, op.t);
          tl.set("#ripple", { scale: 0.2, opacity: 0.9 }, op.t);
          tl.to("#ripple", { scale: 1.5, opacity: 0, duration: 0.5, ease: "power2.out" }, op.t + 0.01);
        } else if (op.k === "frame") {
          tl.set("#f-" + op.name, { opacity: 1 }, op.t);
        } else if (op.k === "fromTo") {
          tl.fromTo(op.sel, op.a, op.b, op.t);
        } else {
          tl[op.k](op.sel, op.a, op.t);
        }
      }

      // The cursor's place on screen is its dashboard position seen through the camera.
      // Sampling that once per frame gives a single smooth track with no competing tweens.
      (function bakeCursor() {
        const FPS = 30;
        const start = ${cursorStart};
        const end = ${round(cursorEnd)};
        const ease = (p) => (p < 0.5 ? 2 * p * p : 1 - 2 * (1 - p) * (1 - p));
        const mix = (a, b, e) => a + (b - a) * e;
        const project = (props, point) => ({ x: point[0] * props.scale + props.x, y: point[1] * props.scale + props.y });
        const unproject = (props, screen) => [(screen.x - props.x) / props.scale, (screen.y - props.y) / props.scale];
        let props = { x: 0, y: 0, scale: 1.5 };
        let camTween = null;
        let page = null;
        let cursorTween = null;
        let screen = { x: 1500, y: 1000 };
        let resting = true;
        let ci = 0;
        let ui = 0;
        const propsAt = (t) => {
          if (!camTween) return props;
          const e = ease(Math.min(1, Math.max(0, (t - camTween.t) / camTween.duration)));
          return { x: mix(camTween.from.x, camTween.to.x, e), y: mix(camTween.from.y, camTween.to.y, e), scale: mix(camTween.from.scale, camTween.to.scale, e) };
        };
        const pageAt = (t) => {
          if (!cursorTween) return page;
          const e = ease(Math.min(1, Math.max(0, (t - cursorTween.t) / cursorTween.duration)));
          return [mix(cursorTween.from[0], cursorTween.to[0], e), mix(cursorTween.from[1], cursorTween.to[1], e)];
        };
        const points = [];
        for (let f = 0; f / FPS <= end; f += 1) {
          const t = f / FPS;
          while (ci < camEvents.length && camEvents[ci].t <= t) {
            const event = camEvents[ci++];
            const before = propsAt(event.t);
            if (event.duration) {
              props = before;
              camTween = { t: event.t, duration: event.duration, from: before, to: event.props };
            } else {
              // A cut to another scene: keep the cursor where it is on screen.
              const at = pageAt(event.t);
              const held = at ? project(before, at) : screen;
              props = event.props;
              camTween = null;
              page = unproject(props, held);
              cursorTween = null;
              resting = true;
            }
          }
          while (ui < cursorEvents.length && cursorEvents[ui].t <= t) {
            const event = cursorEvents[ui++];
            const from = pageAt(event.t) || unproject(propsAt(event.t), screen);
            page = from;
            cursorTween = { t: event.t, duration: event.duration, from, to: event.to, rest: event.rest };
            resting = false;
          }
          if (camTween && t >= camTween.t + camTween.duration) { props = camTween.to; camTween = null; }
          if (cursorTween && t >= cursorTween.t + cursorTween.duration) { page = cursorTween.to; resting = cursorTween.rest; cursorTween = null; }
          const at = pageAt(t);
          if (cursorTween) {
            screen = project(propsAt(t), at);
          } else if (at) {
            // Idle: a resting cursor keeps its place on screen; an attached one rides
            // with the page but is never carried out of the frame.
            const ride = resting ? screen : project(propsAt(t), at);
            screen = { x: Math.min(1860, Math.max(40, ride.x)), y: Math.min(1010, Math.max(40, ride.y)) };
            page = unproject(propsAt(t), screen);
          }
          if (t >= start - 1e-6) points.push({ x: Math.round(screen.x * 10) / 10, y: Math.round(screen.y * 10) / 10 });
        }
        tl.set("#cursor-arrow", points[0], 0);
        tl.to("#cursor-arrow", { keyframes: points.slice(1).map((point) => ({ ...point, duration: 1 / FPS, ease: "none" })), ease: "none" }, start);
      })();

      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
`;
writeFileSync(join(root, "index.html"), html);
console.log("wrote index.html");
