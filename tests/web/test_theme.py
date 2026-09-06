"""Integration tests for the shared dashboard theme shell."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.config.yaml_config import InferenceConfig
from mudidi.web.app import create_app
from mudidi.web.models import Provider
from mudidi.web.runs import RunStatus


class _SemanticParser(HTMLParser):
    """Collect response-level landmarks without depending on CSS classes."""

    _CONTROL_TAGS = {"button", "input", "select", "textarea"}

    def __init__(self) -> None:
        super().__init__()
        self._hidden_depth = 0
        self._label_depth = 0
        self._open_controls: list[int] = []
        self._open_headings: list[int] = []
        self._open_workspace_nav = 0
        self.mains = 0
        self.headings: list[tuple[int, bool]] = []
        self.controls: list[dict[str, object]] = []
        self.workspace_current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        hidden = self._hidden_depth > 0 or "hidden" in attributes
        if hidden:
            self._hidden_depth += 1
        if tag == "main":
            self.mains += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            index = len(self.headings)
            self.headings.append((int(tag[1]), not hidden))
            self._open_headings.append(index)
        if tag == "label":
            self._label_depth += 1
        if tag in self._CONTROL_TAGS:
            index = len(self.controls)
            self.controls.append(
                {
                    "tag": tag,
                    "hidden": hidden,
                    "label_depth": self._label_depth,
                    "aria_label": attributes.get("aria-label"),
                    "aria_labelledby": attributes.get("aria-labelledby"),
                    "type": attributes.get("type", ""),
                    "text": "",
                }
            )
            self._open_controls.append(index)
        if (
            tag in {"a", "span"}
            and self._open_workspace_nav
            and attributes.get("aria-current") == "page"
        ):
            self.workspace_current.append(tag)
        if tag == "nav" and attributes.get("aria-label") == "Run workspace":
            self._open_workspace_nav += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for index in self._open_controls:
            self.controls[index]["text"] = str(self.controls[index]["text"]) + data

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._open_workspace_nav:
            self._open_workspace_nav -= 1
        if tag in self._CONTROL_TAGS and self._open_controls:
            self._open_controls.pop()
        if tag == "label" and self._label_depth:
            self._label_depth -= 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._open_headings:
            self._open_headings.pop()
        if self._hidden_depth:
            self._hidden_depth -= 1


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
        assert (
            str(control["aria_label"] or "").strip()
            or str(control["aria_labelledby"] or "").strip()
            or int(control["label_depth"]) > 0
            or str(control["text"]).strip()
        ), control
    if run_workspace:
        assert len(parser.workspace_current) == 1


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

    responses = [
        ("/", client.get("/"), False),
        ("/active", client.get("/active"), False),
        ("/history", client.get("/history"), False),
        ("/presets", client.get("/presets"), False),
        ("/review", client.get("/runs/workspace-run/review"), True),
        ("/credential-required", credential, True),
        ("/parse-rules", client.get("/runs/review-run/parse-rules"), True),
        ("/pages", client.get("/runs/empty-pages-run/pages"), True),
        ("/page-detail", client.get("/runs/workspace-run/pages/page_1"), True),
        ("/logs", client.get("/runs/workspace-run/logs"), True),
        ("/outputs", client.get("/runs/workspace-run/outputs"), True),
        ("/usage", client.get("/runs/workspace-run/usage"), True),
    ]
    return responses


def test_every_reachable_template_variant_has_semantic_shell(tmp_path: Path) -> None:
    for path, response, run_workspace in _semantic_matrix(tmp_path):
        assert response.status_code in {200, 303, 409}, path
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

def test_theme_script_is_served_and_follows_the_os_preference(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/theme.js")

    assert response.status_code == 200
    assert "mudidi:theme" in response.text
    assert "prefers-color-scheme" in response.text
    assert "dataset.theme" in response.text


def test_stylesheet_exposes_the_brutalist_theme(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--border-width: 3px" in response.text
    assert '[data-theme="dark"]' in response.text
    assert "8px 8px 0 0" in response.text
    assert "border-radius" not in response.text
