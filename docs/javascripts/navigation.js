(() => {
  const initializeSidebarSearch = () => {
    const query = document.querySelector(".md-header .md-search__input");
    const toggle = document.querySelector("#__search");
    if (!query || !toggle || query.dataset.sidebarSearchInitialized === "true") {
      return;
    }

    query.dataset.sidebarSearchInitialized = "true";
    const activate = () => {
      if (!toggle.checked) {
        toggle.checked = true;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      }
    };
    query.addEventListener("focus", activate);
    query.addEventListener("input", activate);
  };

  const initializeExpandableNavigation = () => {
    const navigation = document.querySelector(".md-sidebar--primary");
    if (!navigation) {
      return;
    }

    navigation.querySelectorAll(".md-nav__item--nested").forEach((item) => {
      if (item.dataset.expandableNavigationInitialized === "true") {
        return;
      }

      const toggle = item.querySelector(":scope > input.md-nav__toggle");
      const panel = item.querySelector(":scope > nav.md-nav");
      const label = item.querySelector(":scope > label.md-nav__link");
      const indexLink = item.querySelector(
        ":scope > .md-nav__container > a.md-nav__link",
      );
      if (!toggle || !panel || (!label && !indexLink)) {
        return;
      }

      item.dataset.expandableNavigationInitialized = "true";
      panel.id ||= `${toggle.id}_panel`;
      const controls = [label, indexLink].filter(Boolean);

      const synchronize = () => {
        controls.forEach((control) => {
          control.setAttribute("aria-controls", panel.id);
          control.setAttribute("aria-expanded", String(toggle.checked));
        });
        panel.setAttribute("aria-expanded", String(toggle.checked));
      };

      const togglePanel = () => {
        toggle.checked = !toggle.checked;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      };

      if (label) {
        label.setAttribute("role", "button");
        label.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            togglePanel();
          }
        });
      }

      if (indexLink) {
        indexLink.setAttribute("role", "button");
        indexLink.addEventListener("click", (event) => {
          event.preventDefault();
          togglePanel();
        });
      }

      toggle.addEventListener("change", synchronize);
      synchronize();
    });
  };

  const initializeNavigation = () => {
    initializeSidebarSearch();
    initializeExpandableNavigation();
  };

  if (typeof document$ !== "undefined") {
    document$.subscribe(initializeNavigation);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeNavigation);
  } else {
    initializeNavigation();
  }
})();
