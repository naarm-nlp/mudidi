"""Integration tests for the shared dashboard theme shell."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from mudidi.config.yaml_config import InferenceConfig
from mudidi.web.app import _TEMPLATES, create_app
from mudidi.web.models import Provider
from mudidi.web.runs import RunStatus


class _SemanticParser(HTMLParser):
    """Collect response-level landmarks without depending on CSS classes."""

    _CONTROL_TAGS = {"button", "input", "select", "textarea"}
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self._hidden_depth = 0
        self._label_depth = 0
        self._open_elements: list[dict[str, object]] = []
        self._labels: list[dict[str, object]] = []
        self._element_text: dict[str, str] = {}
        self._open_workspace_nav = 0
        self.mains = 0
        self.headings: list[tuple[int, bool]] = []
        self.controls: list[dict[str, object]] = []
        self.workspace_current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        own_hidden = (
            "hidden" in attributes or attributes.get("aria-hidden") == "true"
        )
        hidden = self._hidden_depth > 0 or own_hidden
        if tag == "main" and not hidden:
            self.mains += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append((int(tag[1]), not hidden))
        label_index: int | None = None
        if tag == "label":
            self._label_depth += 1
            label_index = len(self._labels)
            self._labels.append(
                {
                    "for": str(attributes.get("for") or ""),
                    "hidden": hidden,
                    "text": "",
                }
            )
        element_id = attributes.get("id")
        if element_id:
            self._element_text.setdefault(str(element_id), "")
        control_index: int | None = None
        if tag in self._CONTROL_TAGS:
            control_index = len(self.controls)
            nested_label_indices = tuple(
                int(element["label_index"])
                for element in self._open_elements
                if element["label_index"] is not None
            )
            self.controls.append(
                {
                    "tag": tag,
                    "hidden": hidden,
                    "label_depth": self._label_depth,
                    "nested_labels": nested_label_indices,
                    "id": element_id or "",
                    "aria_label": attributes.get("aria-label"),
                    "aria_labelledby": attributes.get("aria-labelledby"),
                    "type": str(attributes.get("type", "")).casefold(),
                    "text": "",
                }
            )
        if (
            tag in {"a", "span"}
            and not hidden
            and self._open_workspace_nav
            and attributes.get("aria-current") == "page"
        ):
            self.workspace_current.append(tag)
        if tag == "nav" and not hidden and attributes.get("aria-label") == "Run workspace":
            self._open_workspace_nav += 1
        if tag not in self._VOID_TAGS:
            self._open_elements.append(
                {
                    "tag": tag,
                    "own_hidden": own_hidden,
                    "hidden": hidden,
                    "id": str(element_id) if element_id else "",
                    "control_index": control_index,
                    "label_index": label_index,
                }
            )
            if own_hidden:
                self._hidden_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        for element in self._open_elements:
            element_id = str(element["id"])
            if element_id:
                self._element_text[element_id] += data
            label_index = element["label_index"]
            if label_index is not None:
                self._labels[int(label_index)]["text"] = (
                    str(self._labels[int(label_index)]["text"]) + data
                )
            control_index = element["control_index"]
            if (
                element["tag"] == "button"
                and control_index is not None
                and not bool(element["hidden"])
                and self._hidden_depth == 0
            ):
                self.controls[control_index]["text"] = (
                    str(self.controls[control_index]["text"]) + data
                )

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID_TAGS:
            return
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index]["tag"] != tag:
                continue
            closing_elements = self._open_elements[index:]
            del self._open_elements[index:]
            for element in closing_elements:
                if bool(element["own_hidden"]):
                    self._hidden_depth -= 1
                if element["tag"] == "label":
                    self._label_depth -= 1
                if element["tag"] == "nav" and self._open_workspace_nav:
                    self._open_workspace_nav -= 1
            return

    def labelledby_name(self, value: object) -> str:
        references = str(value or "").split()
        if not references or any(
            reference not in self._element_text for reference in references
        ):
            return ""
        name = " ".join(
            self._element_text[reference].strip() for reference in references
        )
        return name.strip()

    def explicit_label_name(self, value: object) -> str:
        target = str(value or "")
        if not target:
            return ""
        names = (
            _normalise_accessible_text(label["text"])
            for label in self._labels
            if str(label["for"]) == target and not bool(label["hidden"])
        )
        return " ".join(name for name in names if name)

    def nested_label_name(self, control: dict[str, object]) -> str:
        names: list[str] = []
        for index in tuple(control["nested_labels"]):
            label = self._labels[int(index)]
            if bool(label["hidden"]):
                continue
            name = _normalise_accessible_text(label["text"])
            if name:
                names.append(name)
        return " ".join(names)


def _normalise_accessible_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _assert_semantic_shell(response: object, *, run_workspace: bool = False) -> None:
    parser = _SemanticParser()
    parser.feed(getattr(response, "text"))
    assert parser.mains == 1
    visible_headings = [level for level, visible in parser.headings if visible]
    assert visible_headings and visible_headings[0] == 1
    assert visible_headings.count(1) == 1
    assert all(
        right - left <= 1
        for left, right in zip(visible_headings, visible_headings[1:])
    )
    for control in parser.controls:
        if control["hidden"] or control["type"] == "hidden":
            continue
        tag = str(control["tag"])
        aria_label = _normalise_accessible_text(control["aria_label"])
        aria_labelledby = parser.labelledby_name(control["aria_labelledby"])
        visible_text = _normalise_accessible_text(control["text"])
        if tag == "button":
            accessible_name = aria_label or aria_labelledby or visible_text
            assert accessible_name, control
            if visible_text and (aria_label or aria_labelledby):
                # Single-character glyphs such as "i", "×", and "＋" are
                # decorative button icons rather than visible text labels.
                visible_label = visible_text.lstrip("＋+×✕←→↑↓ ")
                if len(visible_label) > 1:
                    assert (
                        visible_label.casefold() in accessible_name.casefold()
                    ), control
            continue
        has_nested_label = bool(parser.nested_label_name(control))
        has_explicit_label = bool(parser.explicit_label_name(control["id"]))
        assert (
            aria_label
            or aria_labelledby
            or has_nested_label
            or has_explicit_label
        ), control
    if run_workspace:
        assert len(parser.workspace_current) == 1


def test_semantic_parser_handles_void_inputs_and_hidden_siblings() -> None:
    response = SimpleNamespace(
        text=(
            "<main><h1>Workspace</h1>"
            '<div hidden><input type="hidden"></div>'
            '<label for="name">Name</label><input id="name">'
            '<button aria-label="Save changes">Save</button>'
            "</main>"
        )
    )

    _assert_semantic_shell(response)


def test_semantic_shell_accepts_exact_explicit_label_association() -> None:
    response = SimpleNamespace(
        text=(
            "<main><h1>Workspace</h1>"
            '<label for="display-name"> Display \n name </label>'
            '<input id="display-name">'
            "</main>"
        )
    )

    _assert_semantic_shell(response)


@pytest.mark.parametrize(
    ("label_for", "control_id"),
    (
        (" display-name", "display-name"),
        ("display-name ", "display-name"),
        ("display-name", " display-name"),
        ("display-name", "display-name "),
    ),
)
def test_semantic_shell_rejects_whitespace_mismatched_explicit_label_association(
    label_for: str, control_id: str
) -> None:
    response = SimpleNamespace(
        text=(
            "<main><h1>Workspace</h1>"
            f'<label for="{label_for}">Display name</label>'
            f'<input id="{control_id}">'
            "</main>"
        )
    )

    with pytest.raises(AssertionError):
        _assert_semantic_shell(response)


def test_semantic_parser_does_not_leak_hidden_depth_from_void_inputs() -> None:
    response = SimpleNamespace(
        text=(
            "<main><h1>Workspace</h1>"
            '<div hidden><input type="hidden"></div>'
            '<input hidden type="hidden">'
            '<input id="leaked-hidden-depth">'
            "</main>"
        )
    )
    parser = _SemanticParser()
    parser.feed(response.text)

    assert [control["hidden"] for control in parser.controls] == [True, True, False]
    with pytest.raises(AssertionError):
        _assert_semantic_shell(response)


@pytest.mark.parametrize(
    "control",
    (
        '<input id="missing-label">',
        '<select id="missing-label"><option>Choose</option></select>',
        '<label for="empty-label"> \n </label><input id="empty-label">',
        '<label><input id="empty-wrapper"></label>',
    ),
)
def test_semantic_shell_rejects_unlabeled_form_controls(control: str) -> None:
    response = SimpleNamespace(
        text=f"<main><h1>Workspace</h1>{control}</main>"
    )

    with pytest.raises(AssertionError):
        _assert_semantic_shell(response)


def test_semantic_shell_rejects_mismatched_button_label_name() -> None:
    response = SimpleNamespace(
        text=(
            "<main><h1>Workspace</h1>"
            '<button aria-label="Delete item">Save item</button>'
            "</main>"
        )
    )

    with pytest.raises(AssertionError):
        _assert_semantic_shell(response)


def _semantic_matrix(tmp_path: Path) -> list[tuple[str, object, bool]]:
    app = create_app(data_dir=tmp_path / "app-data", offline_inference=True)
    client = TestClient(app)
    store = app.state.run_store

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_1.png").write_bytes(b"source")
    output = tmp_path / "output"
    config = InferenceConfig.model_validate(
        {
            "input": {"pages": pages},
            "output": {"directory": output},
            "pipeline": {"stage": "all"},
        }
    )
    app.state.job_controller.prepare_inference(
        "workspace-run",
        config=config,
        provider=Provider.ANTHROPIC,
    )
    stage1 = output / "stage-1/page_1"
    stage1.mkdir(parents=True)
    (stage1 / "page_1_stage1_flat.txt").write_text("source text", encoding="utf-8")
    stage2 = output / "stage-2/page_1"
    stage2.mkdir(parents=True)
    (stage2 / "page_1.mdf.txt").write_text("\\lx source", encoding="utf-8")
    (stage2 / "page_1_usage.json").write_text(
        '{"stage1":{"total_tokens":1},"stage2":{"total_tokens":2}}',
        encoding="utf-8",
    )
    store.create_preset("preset-one", name="One preset", provider="anthropic", config=config)

    empty_pages = tmp_path / "empty-pages"
    empty_pages.mkdir()
    empty_config = InferenceConfig.model_validate(
        {
            "input": {"pages": empty_pages},
            "output": {"directory": tmp_path / "empty-output"},
            "pipeline": {"stage": "1"},
        }
    )
    app.state.job_controller.prepare_inference(
        "empty-pages-run",
        config=empty_config,
        provider=Provider.ANTHROPIC,
    )

    store.create_run("review-run", provider="offline")
    store.transition("review-run", RunStatus.VALIDATED)
    store.transition("review-run", RunStatus.QUEUED)
    store.transition("review-run", RunStatus.DISCOVERING_PARSE_RULES)
    app.state.parse_rule_reviews.create_generated(
        "review-run",
        {
            "markers": [{"marker": "lx", "description": "Headword"}],
            "rules": ["Begin each entry with a headword."],
            "abbreviations": {},
        },
        sample_pages=["1"],
    )

    store.create_run("active-run", provider="offline")
    store.transition("active-run", RunStatus.VALIDATED)
    store.transition("active-run", RunStatus.QUEUED)
    store.transition("active-run", RunStatus.RUNNING_STAGE1)
    app.state.job_controller.prepare_inference(
        "credential-run",
        config=config,
        provider=Provider.ANTHROPIC,
    )
    credential = client.post("/runs/credential-run/start")

    @app.get("/form-error", response_class=HTMLResponse)
    async def form_error(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="form_error.html",
            context={
                "validation_errors": [
                    {
                        "field": "Pages",
                        "message": "Upload one dictionary PDF.",
                    }
                ]
            },
        )

    responses = [
        ("/", client.get("/"), False),
        ("/form-error", client.get("/form-error"), False),
        ("/active", client.get("/active"), False),
        ("/history", client.get("/history"), False),
        ("/presets", client.get("/presets"), False),
        ("/runs/workspace-run", client.get("/runs/workspace-run"), True),
        ("/review", client.get("/runs/workspace-run/review"), True),
        ("/credential-required", credential, True),
        ("/parse-rules", client.get("/runs/review-run/parse-rules"), True),
        ("/pages", client.get("/runs/empty-pages-run/pages"), True),
        ("/page-detail", client.get("/runs/workspace-run/pages/page_1"), True),
        ("/logs", client.get("/runs/workspace-run/logs"), True),
        ("/outputs", client.get("/runs/workspace-run/outputs"), True),
        ("/usage", client.get("/runs/workspace-run/usage"), True),
    ]
    assert len(responses) == 14
    return responses


def test_every_reachable_template_variant_has_semantic_shell(tmp_path: Path) -> None:
    for path, response, run_workspace in _semantic_matrix(tmp_path):
        assert response.status_code in {200, 303, 409, 422}, path
        if response.status_code == 303:
            continue
        _assert_semantic_shell(response, run_workspace=run_workspace)
def test_every_page_renders_the_shared_shell(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    for path in ("/", "/history", "/presets", "/active"):
        response = client.get(path)

        assert response.status_code == 200
        assert 'class="skip-link" href="#main-content"' in response.text
        assert 'class="app-shell"' in response.text
        assert '<main id="main-content" tabindex="-1">' in response.text
        assert 'class="sidebar"' in response.text
        assert 'aria-label="Primary navigation"' in response.text
        assert 'href="/presets"' in response.text
        assert "data-theme-toggle" in response.text
        assert 'class="standalone"' not in response.text

def test_nav_marks_only_the_current_section_active(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/history")

    assert response.status_code == 200
    assert 'class="active" href="/history"' in response.text
    assert 'href="/history" aria-current="page"' in response.text
    assert 'class="active" href="/"' not in response.text

def test_theme_script_is_served_without_os_preference_fallback(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/theme.js")

    assert response.status_code == 200
    assert "mudidi:theme" in response.text
    assert "prefers-color-scheme" not in response.text
    assert "dataset.theme" in response.text


def test_theme_script_defaults_to_light_and_honors_valid_storage() -> None:
    theme_js = Path(__file__).resolve().parents[2] / "src/mudidi/web/static/theme.js"
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const initialTheme = (storedValue, {storageThrows = false} = {}) => {
  const root = {dataset: {}};
  let domContentLoaded;
  const storage = {
    getItem(key) {
      assert(key === "mudidi:theme", `unexpected storage key: ${key}`);
      if (storageThrows) throw new Error("storage denied");
      return storedValue;
    },
    setItem() {},
  };
  const document = {
    documentElement: root,
    addEventListener(type, listener) {
      assert(type === "DOMContentLoaded", `unexpected event: ${type}`);
      domContentLoaded = listener;
    },
    querySelectorAll() {
      return [];
    },
  };
  const context = vm.createContext({
    document,
    window: {
      localStorage: storage,
      matchMedia: () => ({matches: true}),
    },
  });

  vm.runInContext(source, context);
  assert(typeof domContentLoaded === "function", "theme bootstrap did not register DOMContentLoaded");
  return root.dataset.theme;
};

assert(initialTheme(null) === "light", "unset storage should default to light");
assert(initialTheme("system") === "light", "invalid storage should default to light");
assert(initialTheme("dark") === "dark", "stored dark should win");
assert(initialTheme("light") === "light", "stored light should win");
assert(initialTheme(null, {storageThrows: true}) === "light", "storage errors should default to light");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(theme_js)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_stylesheet_exposes_the_brutalist_theme(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--border-width: 3px" in response.text
    assert '[data-theme="dark"]' in response.text
    assert "8px 8px 0 0" in response.text
    assert "border-radius" not in response.text
