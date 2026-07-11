(() => {
  const initializeTocPanel = () => {
    const sidebar = document.querySelector(".md-sidebar--secondary");
    if (!sidebar || sidebar.dataset.tocPanelInitialized === "true") {
      return;
    }

    const scrollArea = sidebar.querySelector(".md-sidebar__scrollwrap");
    const toc = sidebar.querySelector("[data-md-component='toc']");
    if (!scrollArea || !toc) {
      sidebar.hidden = true;
      return;
    }

    sidebar.dataset.tocPanelInitialized = "true";
    sidebar.classList.add("toc-panel");
    scrollArea.id = "page-table-of-contents";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "toc-panel__toggle";
    toggle.setAttribute("aria-controls", scrollArea.id);
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Show table of contents");
    toggle.innerHTML = [
      '<span class="toc-panel__label">On this page</span>',
      '<span class="toc-panel__chevron" aria-hidden="true">‹</span>',
    ].join("");

    const setOpen = (open) => {
      sidebar.classList.toggle("toc-panel--open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute(
        "aria-label",
        open ? "Hide table of contents" : "Show table of contents",
      );
    };

    toggle.addEventListener("click", () => {
      setOpen(!sidebar.classList.contains("toc-panel--open"));
    });
    toggle.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        toggle.focus();
      }
    });

    sidebar.prepend(toggle);
    setOpen(false);
  };

  if (typeof document$ !== "undefined") {
    document$.subscribe(initializeTocPanel);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeTocPanel);
  } else {
    initializeTocPanel();
  }
})();
