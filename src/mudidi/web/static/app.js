"use strict";

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;

  const kind = button.dataset.add;
  if (kind) {
    const template = document.querySelector(`#${kind}-template`);
    const rows = document.querySelector(`#${kind}-rows`);
    if (template && rows) rows.append(template.content.cloneNode(true));
    return;
  }

  if (button.hasAttribute("data-remove")) {
    const row = button.closest(".editor-row");
    if (row) row.remove();
  }
});
