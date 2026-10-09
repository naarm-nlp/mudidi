// Writes index.html (the HyperFrames composition) from the captured frames in
// assets/cap. Edit the timings and captions here, then run:
//
//   node capture/build.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const meta = JSON.parse(readFileSync(join(root, "assets/cap/meta.json"), "utf8"));

// The music is 100 BPM: one beat is 0.6s, one bar 2.4s. Scene cuts sit on beats.
const DURATION = 52.8;
const scenes = [
  { id: "input", start: 4.8, end: 10.8, frames: ["in-0", "in-drag", "in-file", "in-pages-1", "in-pages-2", "in-pages-3"] },
  {
    id: "context", start: 10.8, end: 18.0,
    frames: ["cx-0", "cx-head-1", "cx-head-2", "cx-hs-1", "cx-tl-1", "cx-tl-2", "cx-ts-1",
      ...[1, 2, 3, 4].map((i) => `cx-inv-${i}`), ...[1, 2, 3, 4, 5, 6, 7, 8].map((i) => `cx-lay-${i}`),
      ...[1, 2, 3, 4, 5, 6, 7].map((i) => `cx-type-${i}`)],
  },
  { id: "model", start: 18.0, end: 27.6, frames: ["md-0", "md-waiting", "md-signed", "md-prov1", "md-prov2", "md-model1", "md-reason1", "md-model2", "md-reason2"] },
  { id: "agentic", start: 27.6, end: 30.0, frames: ["ag-0"] },
  { id: "run1", start: 30.0, end: 34.8, frames: ["ov-s1-0", "ov-s1-1", "ov-s1-2", "ov-disc", "ov-review"] },
  { id: "guide", start: 34.8, end: 39.6, frames: ["gd-0"] },
  { id: "run2", start: 39.6, end: 42.0, frames: ["ov-s2-1", "ov-s2-2", "ov-done"] },
  { id: "pages", start: 42.0, end: 48.0, frames: ["pg-0", "pg-edit-1", "pg-edit-2"] },
];
const captions = [
  [5.0, 10.6, "Drop in the scanned PDF and pick the pages."],
  [11.0, 17.8, "Describe the dictionary: languages, layout, what an entry holds."],
  [18.2, 22.8, "Sign in with a subscription you already have."],
  [23.0, 27.4, "One model, tuned per stage: low reasoning to transcribe, high to parse."],
  [27.8, 29.8, "Agentic verification stays off for this run."],
  [30.2, 34.6, "Stage 1 transcribes each page, then infers an MDF parsing guide."],
  [35.0, 37.9, "You review the guide before anything is parsed."],
  [39.8, 41.8, "Stage 2 converts every page to MDF."],
  [42.2, 45.5, "Check each page against the scan, and fix what you see."],
];

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

      /* Intro */
      #intro-copy { position: absolute; left: 110px; top: 250px; width: 800px; }
      #intro-title { margin-top: 28px; font-size: 118px; line-height: 0.98; font-weight: 800; letter-spacing: -0.035em; }
      #intro-rule { margin-top: 44px; width: 100%; height: 4px; background: #231d18; transform-origin: 0 50%; }
      #intro-sub { margin-top: 36px; font-size: 44px; line-height: 1.25; color: #5b5048; max-width: 760px; }
      #intro-pages { position: absolute; left: 990px; top: 150px; width: 820px; height: 780px; }
      .sheet { position: absolute; top: 40px; width: 500px; height: 667px; background: #fffaf2; border: 1.5px solid #231d18; box-shadow: 0 30px 60px rgba(35, 29, 24, 0.28); overflow: hidden; }
      .sheet img { display: block; width: 100%; height: 100%; object-fit: cover; transform: scale(1.28); transform-origin: 62% 42%; }
      .sheet span { position: absolute; left: 0; bottom: 0; padding: 8px 14px; background: #231d18; color: #f5f1ea; font-family: "IBM Plex Mono", monospace; font-size: 22px; letter-spacing: 0.08em; }
      #sheet-6 { left: 0; }
      #sheet-7 { left: 170px; }
      #sheet-8 { left: 340px; }

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
      <section id="scene-intro" class="clip scene" data-start="0" data-duration="4.8" data-track-index="1">
        <div id="intro-copy">
          <p id="intro-eyebrow" class="eyebrow">Raga · North Pentecost, Vanuatu</p>
          <h1 id="intro-title">One handwritten dictionary.</h1>
          <div id="intro-rule"></div>
          <p id="intro-sub">101 notebook pages of entries, corrections and margin notes.</p>
        </div>
        <div id="intro-pages">
          <div id="sheet-6" class="sheet"><img src="assets/pages/p6.jpg" alt="" /><span>PAGE 6</span></div>
          <div id="sheet-7" class="sheet"><img src="assets/pages/p7.jpg" alt="" /><span>PAGE 7</span></div>
          <div id="sheet-8" class="sheet"><img src="assets/pages/p8.jpg" alt="" /><span>PAGE 8</span></div>
        </div>
      </section>
${sceneHtml}
      <section id="scene-outro" class="clip scene" data-start="48" data-duration="4.8" data-track-index="1">
        <div id="outro-copy">
          <p id="outro-word">MUDIDI</p>
          <div id="outro-rule"></div>
          <p id="outro-line">Scanned pages in. Structured MDF out.</p>
          <p id="outro-local"><span id="outro-dot"></span><span>Runs on your own machine</span></p>
        </div>
      </section>

      <div id="chip" class="clip" data-start="5" data-duration="2.6" data-track-index="2">
        <div id="chip-body">
          <div id="chip-icon">PDF</div>
          <div><p id="chip-name">Raga1.pdf</p><p id="chip-size">101 pages</p></div>
        </div>
      </div>
      <div id="popup" class="clip" data-start="19.1" data-duration="2.5" data-track-index="2">
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
      <div id="cursor" class="clip" data-start="4.8" data-duration="43.2" data-track-index="4">
        <div id="cursor-arrow">
          <div id="ripple"></div>
          <svg id="cursor-svg" viewBox="0 0 46 58" aria-hidden="true"><path d="M4 3 L4 45 L15 35 L23 54 L31 50.5 L23 32 L38 32 Z" fill="#231d18" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" /></svg>
        </div>
      </div>
      <audio id="music" src="assets/music.m4a" data-start="0" data-duration="${DURATION}" data-track-index="5" data-volume="0.85"></audio>
    </div>
    <script>
      const R = ${JSON.stringify(meta.rects)};
      const CAPTIONS = ${JSON.stringify(captions.map(([start, end]) => [start, end]))};
      const tl = gsap.timeline({ paused: true });
      const W = 1920;
      let cam = { x: 0, y: 0, w: 1280 };
      let parked = null;

      // A camera view is {x, y, w} in dashboard CSS pixels; its height is w * 9 / 16.
      const camProps = (view) => ({ x: (-view.x * W) / view.w, y: (-view.y * W) / view.w, scale: W / view.w });
      const onScreen = (view, point) => ({ x: ((point[0] - view.x) * W) / view.w, y: ((point[1] - view.y) * W) / view.w });
      const centre = (target) => (typeof target === "string" ? [R[target].x + R[target].w / 2, R[target].y + R[target].h / 2] : target);
      function camera(scene, t, view, duration) {
        const stage = "#stage-" + scene;
        if (duration) {
          tl.to(stage, { ...camProps(view), duration, ease: "power2.inOut" }, t);
          if (parked) tl.to("#cursor-arrow", { ...onScreen(view, parked), duration, ease: "power2.inOut" }, t);
        } else {
          tl.set(stage, camProps(view), t);
        }
        cam = view;
      }
      function move(t, target, duration) {
        parked = centre(target);
        tl.to("#cursor-arrow", { ...onScreen(cam, parked), duration: duration || 0.6, ease: "power2.inOut" }, t);
      }
      function moveScreen(t, x, y, duration) {
        parked = null;
        tl.to("#cursor-arrow", { x, y, duration: duration || 0.6, ease: "power2.inOut" }, t);
      }
      function click(t) {
        tl.to("#cursor-svg", { scale: 0.8, duration: 0.09, yoyo: true, repeat: 1, ease: "power1.inOut" }, t);
        tl.set("#ripple", { scale: 0.2, opacity: 0.9 }, t);
        tl.to("#ripple", { scale: 1.5, opacity: 0, duration: 0.5, ease: "power2.out" }, t + 0.01);
      }
      const frame = (t, name) => tl.set("#f-" + name, { opacity: 1 }, t);
      const fadeIn = (scene, t) => tl.fromTo("#stage-" + scene, { opacity: 0 }, { opacity: 1, duration: 0.3, ease: "power1.out" }, t);

      // ---- 0.0 Intro: the notebook ------------------------------------------------
      tl.from("#intro-eyebrow", { opacity: 0, y: 20, duration: 0.5, ease: "power2.out" }, 0.15);
      tl.from("#intro-title", { opacity: 0, y: 40, duration: 0.7, ease: "power3.out" }, 0.3);
      tl.from("#intro-rule", { scaleX: 0, duration: 0.7, ease: "power2.inOut" }, 0.7);
      tl.from("#intro-sub", { opacity: 0, y: 24, duration: 0.6, ease: "power2.out" }, 1.1);
      tl.fromTo("#sheet-6", { opacity: 0, y: 160, rotation: 0 }, { opacity: 1, y: 30, rotation: -7, duration: 0.8, ease: "power3.out" }, 0.5);
      tl.fromTo("#sheet-7", { opacity: 0, y: 160, rotation: 0 }, { opacity: 1, y: 0, rotation: 0, duration: 0.8, ease: "power3.out" }, 0.75);
      tl.fromTo("#sheet-8", { opacity: 0, y: 160, rotation: 0 }, { opacity: 1, y: 30, rotation: 7, duration: 0.8, ease: "power3.out" }, 1.0);
      tl.fromTo("#intro-pages", { scale: 1 }, { scale: 1.07, duration: 4.3, ease: "none" }, 0.5);
      tl.to("#intro-copy", { opacity: 0, duration: 0.3, ease: "power1.in" }, 4.45);
      tl.to("#intro-pages", { opacity: 0, duration: 0.3, ease: "power1.in" }, 4.45);

      // ---- 4.8 Upload the PDF and choose pages --------------------------------------
      camera("input", 4.8, { x: 200, y: 240, w: 1080 });
      fadeIn("input", 4.8);
      tl.set("#cursor-arrow", { x: 1500, y: 1000 }, 0);
      tl.fromTo("#chip-body", { x: 70, y: 730, opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.35, ease: "power2.out" }, 5.0);
      moveScreen(5.2, 250, 800, 0.6);
      click(5.9);
      const drop = onScreen(cam, centre("in.drop"));
      tl.to("#chip-body", { x: drop.x - 180, y: drop.y - 70, duration: 1.0, ease: "power2.inOut" }, 6.0);
      moveScreen(6.0, drop.x, drop.y, 1.0);
      parked = centre("in.drop");
      frame(6.7, "in-drag");
      tl.to("#chip-body", { opacity: 0, scale: 0.7, duration: 0.25, ease: "power2.in" }, 7.2);
      frame(7.3, "in-file");
      click(7.25);
      camera("input", 7.6, { x: 250, y: 330, w: 940 }, 0.7);
      move(7.7, "in.pages", 0.6);
      click(8.4);
      frame(8.7, "in-pages-1");
      frame(8.95, "in-pages-2");
      frame(9.2, "in-pages-3");
      camera("input", 9.5, { x: 200, y: 130, w: 1080 }, 0.6);
      move(9.9, "tab.context", 0.6);
      click(10.6);

      // ---- 10.8 Dictionary profile ----------------------------------------------------
      camera("context", 10.8, { x: 200, y: 130, w: 1080 });
      camera("context", 11.0, { x: 240, y: 440, w: 960 }, 0.8);
      move(11.3, "cx.head", 0.6);
      click(11.9);
      [["cx-head-1", 12.1], ["cx-head-2", 12.3], ["cx-hs-1", 12.75], ["cx-tl-1", 13.15], ["cx-tl-2", 13.35], ["cx-ts-1", 13.75],
       ["cx-inv-1", 14.15], ["cx-inv-2", 14.35], ["cx-inv-3", 14.55], ["cx-inv-4", 14.75]].forEach(([name, t]) => frame(t, name));
      move(12.4, "cx.headScript", 0.3);
      move(12.85, "cx.target", 0.3);
      move(13.8, "cx.inventory", 0.35);
      camera("context", 14.9, { x: 240, y: 760, w: 960 }, 0.6);
      move(15.0, "cx.layout", 0.5);
      for (let i = 1; i <= 8; i += 1) frame(15.45 + i * 0.14, "cx-lay-" + i);
      move(16.6, "cx.type1", 0.25);
      for (let i = 1; i <= 7; i += 1) frame(16.75 + i * 0.12, "cx-type-" + i);
      move(16.9, "cx.type7", 0.75);

      // ---- 18.0 Sign in and choose models ------------------------------------------------
      camera("model", 18.0, { x: 240, y: 640, w: 960 });
      fadeIn("model", 18.0);
      move(18.2, "md.login", 0.6);
      click(18.9);
      frame(19.0, "md-waiting");
      tl.fromTo("#popup-shade", { opacity: 0 }, { opacity: 1, duration: 0.25, ease: "power1.out" }, 19.1);
      tl.fromTo("#popup-window", { opacity: 0, scale: 0.92, y: 30 }, { opacity: 1, scale: 1, y: 0, duration: 0.35, ease: "power3.out" }, 19.15);
      tl.set("#popup-done", { opacity: 0 }, 0);
      moveScreen(19.5, 960, 580, 0.6);
      click(20.2);
      tl.to("#popup-choose", { opacity: 0, duration: 0.15 }, 20.3);
      tl.to("#popup-done", { opacity: 1, duration: 0.2 }, 20.45);
      tl.fromTo("#done-mark", { scale: 0.4 }, { scale: 1, duration: 0.4, ease: "back.out(2)" }, 20.45);
      frame(21.2, "md-signed");
      tl.to("#popup-window", { opacity: 0, scale: 0.95, duration: 0.25, ease: "power2.in" }, 21.3);
      tl.to("#popup-shade", { opacity: 0, duration: 0.25 }, 21.3);
      move(21.6, "md.prov1", 0.5);
      click(22.15);
      frame(22.25, "md-prov1");
      move(22.3, "md.prov2", 0.4);
      click(22.75);
      frame(22.85, "md-prov2");
      camera("model", 23.0, { x: 240, y: 1060, w: 960 }, 0.6);
      move(23.5, "md.model1", 0.45);
      click(24.0);
      frame(24.1, "md-model1");
      move(24.15, "md.reason1", 0.4);
      click(24.6);
      frame(24.7, "md-reason1");
      move(24.8, "md.model2", 0.45);
      click(25.3);
      frame(25.4, "md-model2");
      move(25.45, "md.reason2", 0.4);
      click(25.9);
      frame(26.0, "md-reason2");
      camera("model", 26.2, { x: 240, y: 1200, w: 960 }, 0.5);
      move(26.6, "md.next", 0.55);
      click(27.3);

      // ---- 27.6 Agentic verification stays off ------------------------------------------
      camera("agentic", 27.6, { x: 200, y: 209, w: 1080 });
      move(27.75, "ag.off", 0.5);
      click(28.3);
      move(28.6, "ag.submit", 0.6);
      click(29.5);

      // ---- 30.0 Run: stage 1 and guide discovery -------------------------------------------
      camera("run1", 30.0, { x: 180, y: 230, w: 1100 });
      fadeIn("run1", 30.0);
      move(30.1, [760, 520], 0.6);
      frame(30.9, "ov-s1-1");
      frame(31.8, "ov-s1-2");
      frame(32.7, "ov-disc");
      frame(33.6, "ov-review");
      move(33.7, "ov.action", 0.5);
      click(34.5);

      // ---- 34.8 Review the MDF parsing guide ---------------------------------------------------
      camera("guide", 34.8, { x: 200, y: 0, w: 1080 });
      fadeIn("guide", 34.8);
      move(34.9, [900, 400], 0.5);
      camera("guide", 35.4, { x: 200, y: 892, w: 1080 }, 2.6);
      move(38.1, "gd.approve", 0.6);
      click(39.1);

      // ---- 39.6 Run: stage 2 --------------------------------------------------------------------
      camera("run2", 39.6, { x: 180, y: 215, w: 1100 });
      fadeIn("run2", 39.6);
      move(39.7, [760, 560], 0.5);
      frame(40.3, "ov-s2-2");
      frame(41.0, "ov-done");
      move(41.05, "ov.pagesTab", 0.5);
      click(41.75);

      // ---- 42.0 Page viewer and editor ----------------------------------------------------------
      camera("pages", 42.0, { x: 240, y: 330, w: 1040 });
      fadeIn("pages", 42.0);
      move(42.2, [520, 640], 0.6);
      camera("pages", 43.0, { x: 240, y: 560, w: 1040 }, 1.0);
      move(44.2, [876, 960], 0.6);
      click(44.85);
      frame(45.1, "pg-edit-1");
      frame(45.4, "pg-edit-2");
      camera("pages", 45.6, { x: 240, y: 700, w: 1040 }, 0.5);
      move(46.0, "pg.save", 0.6);
      click(46.75);

      // ---- 48.0 Outro ----------------------------------------------------------------------------
      tl.from("#outro-word", { opacity: 0, y: 60, duration: 0.7, ease: "power3.out" }, 48.15);
      tl.from("#outro-rule", { scaleX: 0, duration: 0.8, ease: "power2.inOut" }, 48.5);
      tl.from("#outro-line", { opacity: 0, y: 24, duration: 0.6, ease: "power2.out" }, 48.9);
      tl.from("#outro-local", { opacity: 0, duration: 0.6, ease: "power2.out" }, 49.4);
      tl.to("#outro-copy", { opacity: 0, duration: 0.8, ease: "power1.in" }, 51.9);

      CAPTIONS.forEach(([start, end], i) => {
        tl.fromTo("#caption-text-" + i, { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" }, start);
        tl.to("#caption-text-" + i, { opacity: 0, duration: 0.2, ease: "power1.in" }, end - 0.2);
      });

      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
`;
writeFileSync(join(root, "index.html"), html);
console.log("wrote index.html");
