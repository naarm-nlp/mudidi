"""MUDIDI command-line entry point."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path
from mudidi.llm.reasoning import REASONING_CHOICES


def _add_sparse_agentic_arguments(parser: argparse.ArgumentParser) -> None:
    """Register agentic YAML overrides without implicit defaults."""
    group = parser.add_argument_group("agentic verifier-rewriter options")
    group.add_argument(
        "--stage1-agentic",
        action=argparse.BooleanOptionalAction,
        dest="agentic_stage1",
        default=argparse.SUPPRESS,
        help="Enable or disable bounded Stage 1 verification and rewriting.",
    )
    group.add_argument(
        "--stage2-agentic",
        action=argparse.BooleanOptionalAction,
        dest="agentic_stage2",
        default=argparse.SUPPRESS,
        help="Enable or disable bounded Stage 2 verification and rewriting.",
    )
    group.add_argument(
        "--agentic-max-iterations",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum rewrite attempts for each enabled agentic stage.",
    )
    group.add_argument(
        "--agentic-evaluator-model",
        default=argparse.SUPPRESS,
        help="Model used for verifier calls; defaults to the current stage model.",
    )
    group.add_argument(
        "--agentic-rewriter-model",
        default=argparse.SUPPRESS,
        help="Model used for correction calls; defaults to the current stage model.",
    )
    group.add_argument(
        "--agentic-reasoning",
        choices=REASONING_CHOICES,
        default=argparse.SUPPRESS,
        help="Shared reasoning effort for verifier and rewriter calls.",
    )
    group.add_argument(
        "--agentic-evaluator-reasoning",
        choices=REASONING_CHOICES,
        default=argparse.SUPPRESS,
        help="Verifier reasoning effort; overrides --agentic-reasoning.",
    )
    group.add_argument(
        "--agentic-rewriter-reasoning",
        choices=REASONING_CHOICES,
        default=argparse.SUPPRESS,
        help="Rewriter reasoning effort; overrides --agentic-reasoning.",
    )
    group.add_argument(
        "--agentic-min-retry-confidence",
        type=float,
        default=argparse.SUPPRESS,
        help="Minimum verifier confidence required before a rewrite.",
    )
    group.add_argument(
        "--agentic-verifier-patches",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="Enable or disable exact verifier patches before model rewriting.",
    )
    group.add_argument(
        "--agentic-concrete-retry-gate",
        action=argparse.BooleanOptionalAction,
        dest="agentic_require_concrete_retry",
        default=argparse.SUPPRESS,
        help="Require or waive localized evidence before retrying.",
    )


def _add_sparse_run_arguments(
    parser: argparse.ArgumentParser, *, legacy_dictionary_languages: bool = False
) -> None:
    """Register common YAML overrides without implicit defaults."""
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument(
        "--auth-mode",
        choices=["api_key", "subscription"],
        default=argparse.SUPPRESS,
        dest="auth_mode",
    )
    parser.add_argument(
        "--auth-provider",
        "--provider",
        choices=["openai", "google", "claude"],
        default=argparse.SUPPRESS,
        dest="auth_provider",
        help="Subscription provider; requires --auth-mode subscription.",
    )
    parser.add_argument("--pages", default=argparse.SUPPRESS)
    parser.add_argument("--dict-pages", dest="dict_pages", default=argparse.SUPPRESS)
    parser.add_argument("--intro", default=argparse.SUPPRESS)
    parser.add_argument("--intro-pages", dest="intro_pages", default=argparse.SUPPRESS)
    parser.add_argument("--alphabet", default=argparse.SUPPRESS)
    parser.add_argument("--ocr-text", dest="ocr_text", default=argparse.SUPPRESS)
    if legacy_dictionary_languages:
        parser.add_argument(
            "--dictionary-languages",
            dest="dictionary_languages",
            default=argparse.SUPPRESS,
            help="Legacy benchmark language metadata file.",
        )
    parser.add_argument("--toolbox-pdf", dest="toolbox_pdf", default=argparse.SUPPRESS)
    parser.add_argument(
        "--stage-1-guides",
        dest="stage1_guides_path",
        default=argparse.SUPPRESS,
        help="Stage 1 instruction guide path.",
    )
    parser.add_argument(
        "--stage-1-guides-pages",
        dest="stage1_guides_pages",
        default=argparse.SUPPRESS,
        help="Selected pages when --stage-1-guides is a PDF.",
    )
    parser.add_argument(
        "--stage-2-guides",
        dest="stage2_guides_path",
        default=argparse.SUPPRESS,
        help="Stage 2 instruction guide path.",
    )
    parser.add_argument(
        "--stage-2-guides-pages",
        dest="stage2_guides_pages",
        default=argparse.SUPPRESS,
        help="Selected pages when --stage-2-guides is a PDF.",
    )
    parser.add_argument(
        "--stage-2-guides-scope",
        dest="stage2_guides_scope",
        choices=["pass1", "pass2", "both"],
        default=argparse.SUPPRESS,
        help="Stage 2 guide routing scope.",
    )
    parser.add_argument("--output-dir", dest="output_dir", default=argparse.SUPPRESS)
    parser.add_argument(
        "--stage",
        choices=["1", "2", "all", "2-pass-1", "2-pass-2"],
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--model", default=argparse.SUPPRESS)
    parser.add_argument(
        "--stage-1-model", dest="stage_1_model", default=argparse.SUPPRESS
    )
    parser.add_argument(
        "--stage-2-pass-1-model",
        dest="stage_2_pass_1_model",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--stage-2-pass-2-model",
        dest="stage_2_pass_2_model",
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--overwrite", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=False)
    _add_sparse_agentic_arguments(parser)


def _add_sparse_evaluation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--predicted", "-p", default=argparse.SUPPRESS)
    parser.add_argument("--gold", "-g", default=argparse.SUPPRESS)
    parser.add_argument("--dataset-dir", default=argparse.SUPPRESS)
    parser.add_argument("--pred-root", default=argparse.SUPPRESS)
    parser.add_argument("--samples-dir", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", "-o", default=argparse.SUPPRESS)
    parser.add_argument("--languages", nargs="+", default=argparse.SUPPRESS)
    parser.add_argument("--experiment-name", action="append", default=argparse.SUPPRESS)
    parser.add_argument(
        "--all-experiments", action="store_true", default=argparse.SUPPRESS
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the complete public MUDIDI command parser."""
    parser = argparse.ArgumentParser(
        prog="mudidi",
        description="Dictionary OCR and MDF extraction (inference and benchmark modes).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run production inference.")
    _add_sparse_run_arguments(run_parser)
    run_parser.set_defaults(_handler=_run_inference)

    auth = subparsers.add_parser(
        "auth",
        help="Manage local subscription authentication.",
        description=(
            "Manage credentials in MUDIDI's local encrypted subscription store. "
            "No API keys or tokens are accepted by these commands."
        ),
    )
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    for auth_command, help_text in (
        ("status", "Show safe status for one subscription provider."),
        ("login", "Open one provider's subscription login in the browser."),
        ("logout", "Delete one provider's local subscription credential."),
    ):
        auth_parser = auth_sub.add_parser(auth_command, help=help_text)
        auth_parser.add_argument(
            "provider_arg",
            nargs="?",
            choices=["openai", "google", "claude"],
            help="Subscription provider (openai, google, or claude).",
        )
        auth_parser.add_argument(
            "--provider",
            dest="provider",
            default=argparse.SUPPRESS,
            choices=["openai", "google", "claude"],
            help="Subscription provider (also accepted positionally).",
        )
        auth_parser.set_defaults(_handler=_run_auth)

    benchmark = subparsers.add_parser("benchmark", help="Benchmark workflows.")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_run = benchmark_sub.add_parser("run", help="Run benchmark extraction.")
    _add_sparse_run_arguments(benchmark_run, legacy_dictionary_languages=True)
    benchmark_run.add_argument("--dataset-dir", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--samples-dir", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--languages", nargs="+", default=argparse.SUPPRESS)
    benchmark_run.add_argument("--experiment-name", default=argparse.SUPPRESS)
    benchmark_run.set_defaults(_handler=_run_benchmark)

    benchmark_sweep = benchmark_sub.add_parser(
        "sweep", help="Run a typed benchmark experiment sweep."
    )
    benchmark_sweep.add_argument("--config", type=Path, required=True)
    benchmark_sweep.add_argument("--experiment", action="append")
    benchmark_sweep.add_argument("--select", action="append")
    benchmark_sweep.add_argument("--max-runs", type=int)
    benchmark_sweep.add_argument("--dry-run", action="store_true")
    benchmark_sweep.set_defaults(_handler=_run_benchmark_sweep)

    evaluate = benchmark_sub.add_parser("evaluate", help="Evaluate predictions.")
    evaluate_sub = evaluate.add_subparsers(dest="evaluation_stage", required=True)
    stage1_parser = evaluate_sub.add_parser("stage1")
    _add_sparse_evaluation_arguments(stage1_parser)
    stage1_parser.add_argument("--experiment-name-contains", default=argparse.SUPPRESS)
    stage1_parser.add_argument(
        "--include-vlm-ocr", action="store_true", default=argparse.SUPPRESS
    )
    stage1_parser.add_argument("--stage1-output-subdir", default=argparse.SUPPRESS)
    stage1_parser.add_argument(
        "--metrics", choices=["full", "minimal"], default=argparse.SUPPRESS
    )
    stage1_parser.add_argument(
        "--alignment-threshold", type=float, default=argparse.SUPPRESS
    )
    stage1_parser.add_argument(
        "--character-alignment",
        choices=["collapsed", "quick_match"],
        default=argparse.SUPPRESS,
    )
    stage1_parser.add_argument(
        "--per-language-script", action="store_true", default=argparse.SUPPRESS
    )
    stage1_parser.add_argument(
        "--overwrite", action="store_true", default=argparse.SUPPRESS
    )
    stage1_parser.add_argument("--workers", type=int, default=argparse.SUPPRESS)
    stage1_parser.set_defaults(_handler=_run_evaluation)

    stage2_parser = evaluate_sub.add_parser("stage2")
    _add_sparse_evaluation_arguments(stage2_parser)
    stage2_parser.add_argument("--baseline-summary", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--baseline-experiment", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--comparison-output", default=argparse.SUPPRESS)
    stage2_parser.add_argument(
        "--record-threshold", type=float, default=argparse.SUPPRESS
    )
    stage2_parser.add_argument(
        "--line-threshold", type=float, default=argparse.SUPPRESS
    )
    stage2_parser.add_argument("--marker-sub-list", default=argparse.SUPPRESS)
    stage2_parser.add_argument("--dictionary-languages", default=argparse.SUPPRESS)
    stage2_parser.set_defaults(_handler=_run_evaluation)

    config = subparsers.add_parser("config", help="Configuration utilities.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="Validate a YAML config.")
    validate.add_argument("config", type=Path)
    validate.set_defaults(_handler=_validate_config)

    web = subparsers.add_parser("web", help="Run the local production website.")
    web.add_argument(
        "--host",
        choices=["127.0.0.1", "localhost"],
        default="127.0.0.1",
        help="Loopback interface to bind (default: 127.0.0.1).",
    )
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--data-dir", type=Path)
    web.add_argument(
        "--max-request-bytes",
        type=int,
        help="Maximum raw HTTP request body size, including multipart framing.",
    )
    web.add_argument(
        "--max-upload-bytes",
        type=int,
        help="Maximum cumulative managed upload size per run.",
    )
    web.add_argument(
        "--container",
        action="store_true",
        help=(
            "Bind to the container network interface. Use only inside a container "
            "whose published port is restricted to host loopback."
        ),
    )
    web.add_argument(
        "--no-browser",
        action="store_false",
        dest="open_browser",
        default=True,
        help="Do not open the website in the default browser.",
    )
    web.set_defaults(_handler=_run_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one fully parsed MUDIDI command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args._handler(args, parser)


def _run_inference(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_resolved_command

    return run_resolved_command(args, parser=parser, kind="inference")


def _run_benchmark(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_resolved_command

    return run_resolved_command(args, parser=parser, kind="benchmark_run")


def _subscription_store_path() -> Path:
    """Return the configured local encrypted subscription-store directory."""

    for name in ("MUDIDI_SUBSCRIPTION_STORE", "MUDIDI_SUBSCRIPTION_STORE_PATH"):
        configured = os.getenv(name)
        if configured and configured.strip():
            return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "mudidi" / "subscriptions"


def _build_subscription_backend(provider: object) -> object:
    """Build one provider adapter over the dedicated local encrypted store."""

    from mudidi.llm.subscriptions import SubscriptionProvider
    from mudidi.llm.subscriptions.storage import SubscriptionStore

    try:
        selected = (
            provider
            if isinstance(provider, SubscriptionProvider)
            else SubscriptionProvider(provider)
        )
    except (TypeError, ValueError):
        raise ValueError("provider must be one of: openai, google, claude") from None

    store = SubscriptionStore(_subscription_store_path())
    if selected is SubscriptionProvider.OPENAI:
        from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend

        return OpenAICodexBackend(store=store)
    if selected is SubscriptionProvider.GOOGLE:
        from mudidi.llm.subscriptions.google_antigravity import (
            GoogleAntigravityBackend,
        )

        return GoogleAntigravityBackend(store=store)
    from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend

    return ClaudeResearchBackend(store=store)


def _subscription_status_category(status: object) -> str:
    """Derive a stable human-readable category from safe status metadata."""

    metadata = getattr(status, "metadata", {})
    if isinstance(metadata, Mapping):
        explicit = metadata.get("category")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
    if bool(getattr(status, "authenticated", False)):
        return "authenticated"
    if getattr(status, "expires_at", None) is not None:
        return "expired_session"
    return "missing"


def _print_subscription_status(
    provider: object,
    status: object,
    *,
    available: bool = True,
) -> None:
    """Print explicitly safe subscription status fields and no credential data."""

    provider_name = getattr(provider, "value", provider)
    authenticated = bool(getattr(status, "authenticated", False))
    account_label = getattr(status, "account_label", None)
    expires_at = getattr(status, "expires_at", None)
    print(f"Provider: {provider_name}")
    print(f"Available: {'yes' if available else 'no'}")
    print(f"Authenticated: {'yes' if authenticated else 'no'}")
    print(f"Account: {account_label if account_label else 'None'}")
    print(f"Expiry: {expires_at.isoformat() if expires_at is not None else 'None'}")
    print(f"Category: {_subscription_status_category(status)}")
    print("Billing: subscription")


def _run_auth(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Run one local subscription lifecycle command without exposing secrets."""

    from mudidi.llm.subscriptions import (
        SubscriptionError,
        SubscriptionProvider,
        SubscriptionStatus,
    )

    positional_provider = getattr(args, "provider_arg", None)
    flag_provider = getattr(args, "provider", None)
    if (
        positional_provider is not None
        and flag_provider is not None
        and positional_provider != flag_provider
    ):
        parser.error("positional provider and --provider must match")
    raw_provider = flag_provider or positional_provider
    if raw_provider is None:
        parser.error("auth commands require --provider (openai, google, or claude)")
    try:
        provider = (
            raw_provider
            if isinstance(raw_provider, SubscriptionProvider)
            else SubscriptionProvider(raw_provider)
        )
    except (TypeError, ValueError):
        parser.error("provider must be one of: openai, google, claude")

    try:
        backend = _build_subscription_backend(provider)
        backend_provider = getattr(backend, "provider", provider)
        if not isinstance(backend_provider, SubscriptionProvider):
            backend_provider = SubscriptionProvider(backend_provider)
        if backend_provider is not provider:
            raise ValueError("subscription backend provider does not match selection")

        command = getattr(args, "auth_command", None)
        if command == "status":
            probe_status = getattr(backend, "probe_status", None)
            status = (
                probe_status(force=True) if callable(probe_status) else backend.status()
            )
            if not isinstance(status, SubscriptionStatus):
                status = SubscriptionStatus.model_validate(status)
            _print_subscription_status(provider, status)
            return 0
        if command == "login":
            backend.login()
            status = backend.status()
            if not isinstance(status, SubscriptionStatus):
                status = SubscriptionStatus.model_validate(status)
            print(f"Logged in to {provider.value} subscription.")
            _print_subscription_status(provider, status)
            return 0
        if command == "logout":
            backend.logout()
            status = backend.status()
            if not isinstance(status, SubscriptionStatus):
                status = SubscriptionStatus.model_validate(status)
            print(f"Logged out of {provider.value} subscription.")
            _print_subscription_status(provider, status)
            return 0
    except SubscriptionError as exc:
        # Typed subscription errors already redact provider response details.
        parser.error(str(exc))
    except (OSError, TypeError, ValueError):
        # Avoid displaying arbitrary provider/storage exception text at a CLI
        # boundary where it could contain credential material.
        parser.error("subscription authentication command failed")
    return 2


def _run_benchmark_sweep(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    from mudidi.cli.run import run_benchmark_sweep_command

    return run_benchmark_sweep_command(args, parser=parser)


def _run_evaluation(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from mudidi.cli.run import run_evaluation_command

    kind = f"{args.evaluation_stage}_evaluation"
    return run_evaluation_command(args, parser=parser, kind=kind)


def _validate_config(args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    from mudidi.config.yaml_config import load_yaml_config, validate_config_paths

    config = load_yaml_config(args.config)
    validate_config_paths(config)
    print(f"Valid MUDIDI config: {config.kind} (version {config.version})")
    return 0


def _run_web(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Launch the optional single-process loopback web application."""

    try:
        from mudidi.web.server import run_server
    except ImportError:
        parser.error("web dependencies are missing; run: uv sync --extra web")
    try:
        return run_server(
            host=args.host,
            port=args.port,
            data_dir=args.data_dir,
            open_browser=args.open_browser,
            container_mode=args.container,
            max_request_bytes=args.max_request_bytes,
            max_upload_bytes=args.max_upload_bytes,
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
