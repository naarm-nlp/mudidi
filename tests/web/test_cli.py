"""CLI tests for launching the optional local website."""

from __future__ import annotations

import argparse

from mudidi.cli.main import build_parser


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
