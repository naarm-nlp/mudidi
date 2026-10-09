"""Tests for the native output-folder chooser."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mudidi.web.app import create_app
from mudidi.web.folder_picker import (
    FolderPickerUnavailable,
    display_path,
    existing_start_directory,
)


def _client(tmp_path: Path, **kwargs: object) -> tuple[object, TestClient]:
    app = create_app(data_dir=tmp_path / "app-data", **kwargs)
    app.state.folder_picker_available = lambda: True
    return app, TestClient(app)


def test_choose_endpoint_returns_the_selected_folder(tmp_path: Path) -> None:
    app, client = _client(tmp_path)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    seen: list[Path] = []

    def choose(initial: Path) -> Path:
        seen.append(initial)
        return chosen

    app.state.choose_directory = choose

    response = client.post(
        "/output-directory/choose", data={"current": str(tmp_path / "missing/child")}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "chosen", "path": display_path(chosen)}
    assert response.headers["cache-control"] == "no-store"
    assert seen == [tmp_path]


def test_choose_endpoint_reports_cancel_and_dialog_failure(tmp_path: Path) -> None:
    app, client = _client(tmp_path)
    app.state.choose_directory = lambda initial: None

    cancelled = client.post("/output-directory/choose", data={"current": ""})

    assert cancelled.status_code == 200
    assert cancelled.json() == {"status": "cancelled"}

    def fail(initial: Path) -> Path:
        raise FolderPickerUnavailable("no dialog")

    app.state.choose_directory = fail

    failed = client.post("/output-directory/choose", data={"current": ""})

    assert failed.status_code == 503


def test_choose_button_follows_picker_availability(tmp_path: Path) -> None:
    app, client = _client(tmp_path)

    assert "data-choose-output-directory" in client.get("/").text

    app.state.folder_picker_available = lambda: False

    assert "data-choose-output-directory" not in client.get("/").text
    assert client.post("/output-directory/choose").status_code == 404


def test_container_mode_has_no_folder_picker(tmp_path: Path) -> None:
    app, client = _client(tmp_path, container_mode=True)
    app.state.choose_directory = lambda initial: tmp_path

    assert "data-choose-output-directory" not in client.get("/").text
    assert client.post("/output-directory/choose").status_code == 404


def test_start_directory_falls_back_to_home_and_paths_abbreviate() -> None:
    home = Path.home()

    assert existing_start_directory("") == home
    assert existing_start_directory("relative/path") == home
    assert existing_start_directory("~") == home
    assert display_path(home / "Documents" / "MUDIDI-runs") == "~/Documents/MUDIDI-runs"
    assert display_path(Path("/")) == "/"
