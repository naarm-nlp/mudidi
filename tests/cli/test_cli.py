from __future__ import annotations

import pytest

from mudidi.cli.main import build_parser


@pytest.mark.parametrize("subcommand", ["status", "login", "logout"])
def test_auth_commands_accept_provider_flag(subcommand: str) -> None:
    args = build_parser().parse_args(
        ["auth", subcommand, "--provider", "openai"]
    )

    assert args.command == "auth"
    assert args.auth_command == subcommand
    assert args.provider == "openai"


def test_auth_commands_accept_provider_positional_alias() -> None:
    args = build_parser().parse_args(["auth", "status", "google"])

    assert args.provider_arg == "google"


def test_run_accepts_subscription_provider_alias() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--pages",
            "pages",
            "--output-dir",
            "output",
            "--auth-mode",
            "subscription",
            "--provider",
            "claude",
        ]
    )

    assert args.auth_mode == "subscription"
    assert args.auth_provider == "claude"


def test_run_rejects_unknown_subscription_provider() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "run",
                "--pages",
                "pages",
                "--output-dir",
                "output",
                "--auth-mode",
                "subscription",
                "--provider",
                "mystery",
            ]
        )
