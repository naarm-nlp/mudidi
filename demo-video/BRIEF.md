---
workflow: general-video
flow: companion
storyboard: yes
message: "MUDIDI turns a scanned handwritten dictionary into structured MDF, on your own machine."
aspect: "16:9"
length: "52.8s"
language: en
---

## Intent

A product walkthrough of the MUDIDI dashboard using the Raga (Lamalanga) notebook, PDF pages 6-8:
notebook pages, drag the PDF in, fill the dictionary profile, sign in with an OpenAI subscription,
choose gpt-6.1-sol (Stage 1 reasoning low, Stage 2 high), agentic verification off, run, review the
MDF parsing guide, then the page viewer and editor.

## Customizations

- Screens are real screenshots of the dashboard, captured by `capture/capture.mjs` from a throwaway
  copy of the data directory. The sign-in window is a mock and shows only `you@example.com`.
- Captions only, no voiceover. Music is synthesised by `capture/make_music.py` at 100 BPM
  (one beat = 0.6s); scene cuts sit on beats.
- `index.html` is generated: edit timings and captions in `capture/build.mjs`, then run
  `node capture/build.mjs`.
