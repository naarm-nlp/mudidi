"""CLI tests for launching the optional local website."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mudidi.cli.main import build_parser
from mudidi.web.server import run_server


def test_web_command_defaults_to_loopback() -> None:
    args = build_parser().parse_args(["web", "--no-browser"])

    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.open_browser is False
    assert args.container is False


def test_web_command_supports_explicit_container_mode() -> None:
    args = build_parser().parse_args(["web", "--container", "--no-browser"])

    assert args.container is True
    assert args.open_browser is False


def test_web_command_rejects_public_bind() -> None:
    parser = build_parser()

    try:
        parser.parse_args(["web", "--host", "0.0.0.0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("public bind should be rejected by argparse")


def test_web_handler_is_registered() -> None:
    args = build_parser().parse_args(["web"])

    assert isinstance(args, argparse.Namespace)
    assert args._handler.__name__ == "_run_web"


@pytest.mark.parametrize(
    ("host", "port", "message"),
    [
        ("0.0.0.0", 8000, "loopback"),
        ("127.0.0.1", 0, "port"),
        ("127.0.0.1", 65536, "port"),
    ],
)
def test_server_rejects_unsafe_address(
    host: str,
    port: int,
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_server(
            host=host,
            port=port,
            data_dir=tmp_path,
            open_browser=False,
        )


def test_server_uses_exactly_one_uvicorn_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(_app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = run_server(
        host="127.0.0.1",
        port=8123,
        data_dir=tmp_path,
        open_browser=False,
    )

    assert result == 0
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8123,
            "workers": 1,
            "log_level": "warning",
        }
    ]


def test_server_opens_and_advertises_localhost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened_urls: list[str] = []

    class ImmediateTimer:
        def __init__(
            self,
            _delay: float,
            callback: object,
            args: tuple[str],
        ) -> None:
            self.callback = callback
            self.args = args

        def start(self) -> None:
            self.callback(*self.args)  # type: ignore[operator]

    monkeypatch.setattr("mudidi.web.server.threading.Timer", ImmediateTimer)
    monkeypatch.setattr(
        "mudidi.web.server.webbrowser.open",
        lambda url: opened_urls.append(url),
    )
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: None)

    run_server(
        host="127.0.0.1",
        port=8123,
        data_dir=tmp_path,
        open_browser=True,
    )

    assert opened_urls == ["http://localhost:8123/"]
    assert capsys.readouterr().out == "MUDIDI dashboard: http://localhost:8123/\n"


def test_container_mode_binds_inside_container_without_opening_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(_app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = run_server(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path,
        open_browser=False,
        container_mode=True,
    )

    assert result == 0
    assert calls == [
        {
            "host": "0.0.0.0",
            "port": 8000,
            "workers": 1,
            "log_level": "warning",
        }
    ]


def test_container_mode_configures_the_application_for_docker_hosts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_calls: list[dict[str, object]] = []

    def fake_create_app(**kwargs: object) -> object:
        create_calls.append(kwargs)
        return object()

    monkeypatch.setattr("mudidi.web.app.create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: None)

    run_server(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path,
        open_browser=False,
        container_mode=True,
    )

    assert create_calls == [{"data_dir": tmp_path, "container_mode": True}]
