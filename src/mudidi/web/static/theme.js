"use strict";

(() => {
  const STORAGE_KEY = "mudidi:theme";
  const root = document.documentElement;

  const stored = (() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  })();

  const prefersDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;

  root.dataset.theme =
    stored === "dark" || stored === "light" ? stored : prefersDark ? "dark" : "light";

  const syncControls = () => {
    const isDark = root.dataset.theme === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((toggle) => {
      toggle.setAttribute("aria-pressed", isDark ? "true" : "false");
      const label = toggle.querySelector("[data-theme-label]");
      if (label) label.textContent = isDark ? "Light mode" : "Dark mode";
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    syncControls();
    document.addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-theme-toggle]");
      if (!toggle) return;
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch (_error) {
        // Private-mode storage denial must not break the toggle.
      }
      syncControls();
    });
  });
})();
