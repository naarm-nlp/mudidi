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
    assert calls == [{"host": "127.0.0.1", "port": 8123, "workers": 1}]
