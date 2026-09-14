"""FastAPI application factory for the local MUDIDI website."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import secrets
import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from mudidi.llm.client import resolve_subscription_runtime
from mudidi.llm.subscriptions import (
    AuthMode,
    SubscriptionError,
    SubscriptionLifecycleBackend,
    SubscriptionLoginTransaction,
    SubscriptionProvider,
    SubscriptionModel,
    subscription_provider_for_model,
    SubscriptionStatus,
)
from mudidi.llm.subscriptions.storage import SubscriptionStore
from mudidi.llm.subscriptions.oauth import (
    LoopbackOAuthReceiver,
    OAuthCallback,
    is_allowed_callback_source,
    validate_loopback_redirect_uri,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mudidi.config.yaml_config import AuthConfig, InferenceConfig, validate_config_paths
from mudidi.instructions import read_instruction_text
from mudidi.web.credentials import (
    CredentialVault,
    PersistentCredentialStore,
    ResolvedCredential,
    subscription_store_path,
)
from mudidi.web.artifacts import ArtifactAccessError, ArtifactService
from mudidi.web.forms import (
    FormFieldError,
    NewRunForm,
    instruction_review_summary,
)
from mudidi.web.jobs import JobController
from mudidi.web.inputs import (
    InputMaterializer,
    InstructionMaterializationError,
    _MAX_UPLOAD_BYTES,
    read_managed_instruction_metadata,
    rebase_managed_config,
)
from mudidi.web.models import (
    CatalogAuthMode,
    CatalogStage,
    LiveModelOption,
    ModelCatalog,
    ModelCatalogService,
    ModelDiscoveryError,
    ModelDiscovery,
    Provider,
)
from mudidi.web.parse_rules import ParseRuleReviewService
from mudidi.web.runs import (
    can_delete_run,
    InvalidRunTransition,
    PresetRecord,
    RunRecord,
    RunStatus,
    RunStore,
)

_SUBSCRIPTION_LOGGER = logging.getLogger("mudidi.web.subscription")
_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_PACKAGE_DIR / "templates")
_MAX_REQUEST_BYTES = 110 * 1024 * 1024
_MAX_LOG_BYTES = 512_000

_SUBSCRIPTION_TRANSACTION_TTL = timedelta(minutes=10)
_SUBSCRIPTION_CALLBACK_PATHS = {
    SubscriptionProvider.OPENAI: "/auth/callback",
    SubscriptionProvider.GOOGLE: "/oauth2callback",
    SubscriptionProvider.CLAUDE: "/callback",
}
_ALLOW_SAME_ORIGIN_FRAME_HEADER = "X-MUDIDI-Allow-Same-Origin-Frame"
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CONTAINER_OUTPUT_ROOT = Path("/app/outputs")
_CONTAINER_DATA_ROOT = Path("/data")
_LIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.RUNNING_STAGE1,
    RunStatus.DISCOVERING_PARSE_RULES,
    RunStatus.RUNNING_STAGE2,
}

_TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


class _RequestBodyTooLarge(Exception):
    """Internal control flow for streamed request-size enforcement."""


def _validate_byte_limits(max_request_bytes: int, max_upload_bytes: int) -> None:
    """Validate HTTP and managed-upload byte limits."""

    if max_request_bytes < 1:
        raise ValueError("request byte limit must be positive")
    if max_upload_bytes < 1:
        raise ValueError("upload byte limit must be positive")
    if max_request_bytes <= max_upload_bytes:
        raise ValueError("request byte limit must exceed upload byte limit")


def _exception_group_contains(
    group: BaseExceptionGroup,
    expected: type[BaseException],
) -> bool:
    return any(
        isinstance(item, expected)
        or (
            isinstance(item, BaseExceptionGroup)
            and _exception_group_contains(item, expected)
        )
        for item in group.exceptions
    )


def _safe_subscription_diagnostic_token(value: object, *, default: str) -> str:
    if (
        isinstance(value, str)
        and 0 < len(value) <= 64
        and value.isascii()
        and all(character.isalnum() or character in {"_", "-"} for character in value)
    ):
        return value
    return default


def _default_subscription_backends(
    store: SubscriptionStore,
) -> dict[SubscriptionProvider, object]:
    """Build provider adapters over the dedicated local subscription store."""

    from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend
    from mudidi.llm.subscriptions.google_antigravity import (
        GoogleAntigravityBackend,
    )
    from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend

    return {
        SubscriptionProvider.OPENAI: OpenAICodexBackend(store=store),
        SubscriptionProvider.GOOGLE: GoogleAntigravityBackend(store=store),
        SubscriptionProvider.CLAUDE: ClaudeResearchBackend(store=store),
    }


def create_app(
    *,
    data_dir: Path | None = None,
    credential_vault: CredentialVault | None = None,
    subscription_store: SubscriptionStore | None = None,
    subscription_backends: Mapping[SubscriptionProvider | str, object] | None = None,
    offline_inference: bool = False,
    model_discovery: ModelDiscovery | None = None,
    container_mode: bool = False,
    max_request_bytes: int = _MAX_REQUEST_BYTES,
    max_upload_bytes: int = _MAX_UPLOAD_BYTES,
) -> FastAPI:
    """Create a loopback-oriented application without starting a server.

    Args:
        data_dir: Directory reserved for web metadata and managed uploads. The
            directory is created eagerly so startup fails before serving when
            it is not writable.
        container_mode: Permit the local host aliases used to reach the app
            through Docker Desktop. Arbitrary host headers remain rejected.
        max_request_bytes: Maximum raw HTTP request body size, including
            multipart framing.
        max_upload_bytes: Maximum cumulative managed upload size per run.

    Returns:
        Configured FastAPI application.
    """
    _validate_byte_limits(max_request_bytes, max_upload_bytes)

    resolved_data_dir = (data_dir or Path.home() / ".local/share/mudidi").resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="MUDIDI Local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.data_dir = resolved_data_dir
    app.state.max_request_bytes = max_request_bytes
    app.state.max_upload_bytes = max_upload_bytes
    app.state.credential_vault = credential_vault or CredentialVault(
        environ={},
        persistent_store=PersistentCredentialStore(
            database_path=resolved_data_dir / "mudidi-web.sqlite3",
            key_path=resolved_data_dir / ".credential-key",
        ),
    )
    app.state.subscription_store = subscription_store or SubscriptionStore(
        subscription_store_path(resolved_data_dir)
    )
    raw_subscription_backends = (
        subscription_backends
        if subscription_backends is not None
        else _default_subscription_backends(app.state.subscription_store)
    )
    app.state.subscription_backends = {}
    for raw_provider, backend in raw_subscription_backends.items():
        try:
            selected_provider = (
                raw_provider
                if isinstance(raw_provider, SubscriptionProvider)
                else SubscriptionProvider(raw_provider)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported subscription provider") from exc
        app.state.subscription_backends[selected_provider] = backend
    app.state.subscription_transactions = {}
    app.state.subscription_receivers = {}
    app.state.subscription_receiver_providers = {}
    app.state.subscription_transaction_lock = RLock()
    app.state.subscription_lifecycle_locks = {
        provider: RLock() for provider in SubscriptionProvider
    }

    def subscription_catalog_authenticated(provider: Provider) -> bool | str:
        subscription_provider = {
            Provider.OPENAI: SubscriptionProvider.OPENAI,
            Provider.GEMINI: SubscriptionProvider.GOOGLE,
            Provider.ANTHROPIC: SubscriptionProvider.CLAUDE,
        }.get(provider)
        if subscription_provider is None:
            return False
        backend = app.state.subscription_backends.get(subscription_provider)
        if backend is None:
            return False
        probe_status = getattr(backend, "probe_status", None)
        try:
            status = probe_status() if callable(probe_status) else backend.status()
        except Exception:
            return False
        if not isinstance(status, SubscriptionStatus) or not status.authenticated:
            return False
        identity = getattr(backend, "catalog_identity", None)
        if callable(identity):
            try:
                value = identity()
            except Exception:
                return False
            if isinstance(value, str) and value:
                return value
        return True

    def discover_subscription_models(
        provider: Provider,
    ) -> tuple[LiveModelOption, ...]:
        subscription_provider = {
            Provider.OPENAI: SubscriptionProvider.OPENAI,
            Provider.GEMINI: SubscriptionProvider.GOOGLE,
            Provider.ANTHROPIC: SubscriptionProvider.CLAUDE,
        }.get(provider)
        if subscription_provider is None:
            raise ModelDiscoveryError(
                f"{provider.value} subscription model discovery is unavailable"
            )
        backend = app.state.subscription_backends.get(subscription_provider)
        list_models = getattr(backend, "list_models", None)
        if not callable(list_models):
            raise ModelDiscoveryError(
                f"{provider.value} subscription model discovery is unavailable"
            )
        try:
            raw_models = list_models()
        except Exception:
            raise ModelDiscoveryError(
                f"{provider.value} subscription model discovery failed"
            ) from None
        prefix = {
            Provider.OPENAI: "openai",
            Provider.GEMINI: "gemini",
            Provider.ANTHROPIC: "anthropic",
        }[provider]
        models: list[LiveModelOption] = []
        for item in raw_models:
            if not isinstance(item, SubscriptionModel):
                raise ModelDiscoveryError(
                    f"{provider.value} subscription model discovery returned invalid data"
                )
            models.append(
                LiveModelOption(
                    model_id=f"{prefix}/{item.model_id}",
                    display_name=item.display_name,
                    provider=provider,
                    image_input=True,
                    reasoning=item.reasoning,
                    release_at=item.release_at,
                    provider_order=item.provider_order,
                )
            )
        return tuple(models)

    app.state.model_catalog = ModelCatalog.bundled()
    app.state.model_discovery = model_discovery or ModelDiscovery()
    app.state.model_catalog_service = ModelCatalogService(
        catalog=app.state.model_catalog,
        discovery=app.state.model_discovery,
        credential_resolver=app.state.credential_vault.resolve,
        subscription_auth_resolver=subscription_catalog_authenticated,
        subscription_discovery_resolver=discover_subscription_models,
    )
    app.state.run_store = RunStore(resolved_data_dir / "mudidi-web.sqlite3")
    app.state.parse_rule_reviews = ParseRuleReviewService(
        store=app.state.run_store,
        data_dir=resolved_data_dir,
    )
    app.state.job_controller = JobController(
        store=app.state.run_store,
        data_dir=resolved_data_dir,
        parse_rule_reviews=app.state.parse_rule_reviews,
    )
    app.state.offline_inference = offline_inference
    app.state.artifacts = ArtifactService(controller=app.state.job_controller)
    app.state.inputs = InputMaterializer(
        data_dir=resolved_data_dir,
        max_total_bytes=max_upload_bytes,
    )
    app.state.job_controller.reconcile_startup()
    app.state.inputs.reconcile({run.run_id for run in app.state.run_store.list_runs()})
    allowed_hosts = ["127.0.0.1", "localhost", "testserver"]
    if container_mode:
        allowed_hosts.extend(
            ["0.0.0.0", "host.docker.internal", "docker.for.mac.localhost"]
        )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def localhost_security(request: Request, call_next: object) -> object:
        """Enforce localhost mutation and browser hardening boundaries."""

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = PlainTextResponse("Invalid Content-Length", status_code=400)
                return _add_security_headers(response)
            if declared_length > max_request_bytes:
                response = PlainTextResponse("Request body too large", status_code=413)
                return _add_security_headers(response)
        received_bytes = 0
        original_receive = request._receive

        async def limited_receive() -> object:
            nonlocal received_bytes
            message = await original_receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_request_bytes:
                    raise _RequestBodyTooLarge
            return message

        request._receive = limited_receive  # type: ignore[method-assign]
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site")
            opaque_origin_is_same_site = (
                origin == "null" and fetch_site == "same-origin"
            )
            origin_is_invalid = origin is not None and not (
                opaque_origin_is_same_site
                or _origin_matches_host(origin, request.headers.get("host", ""))
            )
            if fetch_site == "cross-site" or origin_is_invalid:
                response = PlainTextResponse(
                    "Cross-origin request rejected", status_code=403
                )
                return _add_security_headers(response)
        try:
            response = await call_next(request)  # type: ignore[operator]
        except _RequestBodyTooLarge:
            response = PlainTextResponse("Request body too large", status_code=413)
        except BaseExceptionGroup as exc:
            if not _exception_group_contains(exc, _RequestBodyTooLarge):
                raise
            response = PlainTextResponse("Request body too large", status_code=413)
        return _add_security_headers(response)

    app.mount(
        "/static",
        StaticFiles(directory=_PACKAGE_DIR / "static"),
        name="static",
    )

    def home_context(
        request: Request,
        *,
        preset_id: str = "",
        validation_errors: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        """Build the dashboard context for initial and rejected submissions."""

        presets = app.state.run_store.list_presets()
        credential_statuses = {
            provider.value: app.state.credential_vault.status(provider)
            for provider in (
                Provider.GEMINI,
                Provider.OPENAI,
                Provider.ANTHROPIC,
                Provider.OPENROUTER,
            )
        }
        subscription_statuses = {
            provider.value: subscription_status_payload(
                provider,
                subscription_backend(provider, required=False),
            )
            for provider in SubscriptionProvider
        }
        selected_preset = None
        preset_state = None
        preset_assets: dict[str, object] = {}
        if preset_id:
            try:
                selected_preset = app.state.run_store.get_preset(preset_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="preset not found") from exc
            preset_state = _preset_form_state(
                selected_preset,
                known_models={model.model_id for model in _all_models(app)},
                presets_root=app.state.inputs.presets_root,
            )
            preset_assets = _preset_asset_links(
                selected_preset,
                presets_root=app.state.inputs.presets_root,
            )
        errors = validation_errors or []
        return {
            "request": request,
            "models": _all_models(app),
            "presets": presets,
            "selected_preset": selected_preset,
            "preset_state": preset_state,
            "preset_assets": preset_assets,
            "credential_statuses": credential_statuses,
            "subscription_statuses": subscription_statuses,
            "credential_ready_count": sum(
                status.available for status in credential_statuses.values()
            ),
            "validation_errors": errors,
            "errors_by_field": {error["key"]: error["message"] for error in errors},
            "output_directory_default": (
                "outputs" if container_mode else "~/Documents/MUDIDI-runs"
            ),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the local production-inference workspace."""

        preset_id = request.query_params.get("preset", "").strip()
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="home.html",
            context=home_context(request, preset_id=preset_id),
        )

    @app.get("/healthz")
    async def health() -> dict[str, str | int]:
        """Return a stable, non-secret liveness response."""

        return {"status": "ok", "protocol_version": 1}

    @app.get("/models/{provider_name}")
    def provider_models(
        provider_name: str,
        stage: CatalogStage,
        auth_mode: CatalogAuthMode,
        force: bool = False,
    ) -> JSONResponse:
        """Return a non-secret stage-aware model catalog."""

        try:
            provider = Provider(provider_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="unknown provider") from exc
        if provider is Provider.CUSTOM:
            raise HTTPException(
                status_code=422,
                detail="custom routing uses manual model entry",
            )
        if auth_mode is CatalogAuthMode.SUBSCRIPTION:
            if provider is Provider.OPENROUTER:
                raise HTTPException(
                    status_code=422,
                    detail="provider does not support subscription model discovery",
                )
            if force:
                raise HTTPException(
                    status_code=422,
                    detail="subscription catalogs cannot be refreshed",
                )
        result = app.state.model_catalog_service.list_models(
            provider,
            stage=stage,
            auth_mode=auth_mode,
            force_refresh=force,
        )
        return JSONResponse(
            result.to_payload(),
            headers={"Cache-Control": "no-store"},
        )

    def require_local_subscription_request(request: Request) -> None:
        """Reject subscription lifecycle requests outside local boundaries."""

        client = request.client
        client_host = client.host if client is not None else None
        trusted_test_or_loopback = client_host in {
            "127.0.0.1",
            "::1",
            "localhost",
            "testclient",
            "testserver",
        }
        trusted_container_gateway = container_mode and is_allowed_callback_source(
            client_host,
            container_mode=True,
        )
        if not (trusted_test_or_loopback or trusted_container_gateway):
            raise HTTPException(
                status_code=403, detail="subscription routes are local-only"
            )

    def subscription_provider(provider_name: str) -> SubscriptionProvider:
        try:
            return SubscriptionProvider(provider_name)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail="unknown subscription provider"
            ) from exc

    def subscription_backend(
        provider: SubscriptionProvider,
        *,
        required: bool = True,
    ) -> object | None:
        backend = app.state.subscription_backends.get(provider)
        if backend is None and required:
            raise HTTPException(
                status_code=404, detail="subscription provider unavailable"
            )
        return backend

    def subscription_category(
        status: SubscriptionStatus,
        *,
        provider: SubscriptionProvider,
    ) -> str:
        del provider
        metadata = status.metadata if isinstance(status.metadata, Mapping) else {}
        explicit = metadata.get("category")
        if isinstance(explicit, str) and explicit in {
            "authenticated",
            "missing",
            "expired_session",
            "policy",
            "authentication",
            "transport",
            "unsupported_capability",
        }:
            return explicit
        if status.authenticated:
            return "authenticated"
        if status.expires_at is not None:
            return "expired_session"
        return "missing"

    def subscription_credential_flags(
        status: SubscriptionStatus,
    ) -> tuple[bool, bool]:
        """Return safe stored-credential and removal state."""

        metadata = status.metadata if isinstance(status.metadata, Mapping) else {}
        explicit_presence = metadata.get("credential_present")
        if isinstance(explicit_presence, bool):
            credential_present = explicit_presence
        else:
            credential_present = bool(
                status.authenticated
                or status.expires_at is not None
                or (
                    isinstance(status.account_label, str)
                    and status.account_label.strip()
                )
                or metadata.get("account_id")
            )
        removable = credential_present
        return credential_present, removable

    def subscription_status_payload(
        provider: SubscriptionProvider,
        backend: object | None,
    ) -> dict[str, object]:
        """Build a status body from only explicitly safe identity fields."""

        if backend is None:
            return {
                "provider": provider.value,
                "authenticated": False,
                "credential_present": False,
                "removable": False,
                "account_label": None,
                "expires_at": None,
                "category": "unavailable",
                "available": False,
            }
        try:
            status = backend.status()  # type: ignore[attr-defined]
            if not isinstance(status, SubscriptionStatus):
                status = SubscriptionStatus.model_validate(status)
        except SubscriptionError as exc:
            category = subscription_error_category(exc)
            return {
                "provider": provider.value,
                "authenticated": False,
                "credential_present": False,
                "removable": False,
                "account_label": None,
                "expires_at": None,
                "category": category,
                "available": True,
            }
        except Exception:
            return {
                "provider": provider.value,
                "authenticated": False,
                "credential_present": False,
                "removable": False,
                "account_label": None,
                "expires_at": None,
                "category": "unavailable",
                "available": False,
            }
        credential_present, removable = subscription_credential_flags(status)
        payload: dict[str, object] = {
            "provider": provider.value,
            "authenticated": bool(status.authenticated),
            "credential_present": credential_present,
            "removable": removable,
            "account_label": status.account_label,
            "expires_at": (
                status.expires_at.isoformat() if status.expires_at is not None else None
            ),
            "category": subscription_category(
                status,
                provider=provider,
            ),
            "available": True,
        }
        return payload

    def subscription_error_category(exc: SubscriptionError) -> str:
        category = str(getattr(exc, "category", "authentication"))
        if category in {
            "authentication",
            "expired_session",
            "transport",
            "policy",
            "unsupported_capability",
        }:
            return category
        return "authentication"

    def subscription_error_message(
        exc: SubscriptionError,
        *,
        provider: SubscriptionProvider,
    ) -> str:
        del provider
        category = subscription_error_category(exc)
        return {
            "authentication": "Subscription authentication failed",
            "expired_session": "Subscription session expired; log in again",
            "transport": "Subscription provider is unavailable",
            "policy": "Subscription request was blocked by provider policy",
            "unsupported_capability": "Subscription request is unsupported by this provider",
        }.get(category, "Subscription request failed")

    def subscription_error_status(exc: SubscriptionError) -> int:
        category = subscription_error_category(exc)
        return {
            "policy": 403,
            "unsupported_capability": 422,
            "transport": 503,
            "expired_session": 401,
            "authentication": 409,
        }.get(category, 409)

    def subscription_response(
        payload: Mapping[str, object],
        *,
        status_code: int = 200,
    ) -> JSONResponse:
        return JSONResponse(
            dict(payload),
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    def subscription_lifecycle_error(
        provider: SubscriptionProvider,
        *,
        category: str,
        message: str,
        status_code: int,
    ) -> JSONResponse:
        """Return one uniformly safe lifecycle error response."""

        return subscription_response(
            {
                "provider": provider.value,
                "status": "error",
                "category": category,
                "message": message,
            },
            status_code=status_code,
        )

    def subscription_callback_uri(
        request: Request,
        provider: SubscriptionProvider,
    ) -> str:
        """Construct the adapter-specific loopback redirect URI."""

        port = request.url.port or 80
        if provider is SubscriptionProvider.GOOGLE:
            return f"http://127.0.0.1:{port}/oauth2callback"
        callback_path = _SUBSCRIPTION_CALLBACK_PATHS[provider]
        return f"http://127.0.0.1:{port}/subscriptions/{provider.value}{callback_path}"

    def subscription_callback_route_allowed(
        request: Request,
        provider: SubscriptionProvider,
    ) -> bool:
        callback_path = _SUBSCRIPTION_CALLBACK_PATHS[provider]
        allowed_paths = {
            f"/subscriptions/{provider.value}{callback_path}",
            f"/subscriptions/{provider.value}/callback",
            f"/auth/subscriptions/{provider.value}{callback_path}",
            f"/auth/subscriptions/{provider.value}/callback",
            f"/auth/subscription/{provider.value}{callback_path}",
            f"/auth/subscription/{provider.value}/callback",
        }
        if provider is SubscriptionProvider.GOOGLE:
            allowed_paths.add("/oauth2callback")
        return request.url.path in allowed_paths

    def save_subscription_transaction(
        provider: SubscriptionProvider,
        transaction: SubscriptionLoginTransaction,
    ) -> tuple[str, datetime]:
        handle = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + _SUBSCRIPTION_TRANSACTION_TTL
        with app.state.subscription_transaction_lock:
            app.state.subscription_transactions[handle] = (
                provider,
                transaction,
                expires_at,
            )
        return handle, expires_at

    def register_subscription_receiver(
        provider: SubscriptionProvider,
        handle: str,
        receiver: object,
    ) -> None:
        with app.state.subscription_transaction_lock:
            if handle in app.state.subscription_transactions:
                app.state.subscription_receivers[handle] = receiver
                app.state.subscription_receiver_providers[handle] = provider

    def close_subscription_receivers(receivers: list[object]) -> None:
        for receiver in receivers:
            close_receiver = getattr(receiver, "close", None)
            if callable(close_receiver):
                try:
                    close_receiver()
                except Exception:
                    pass

    def remove_subscription_receiver(handle: str, *, close: bool = False) -> None:
        with app.state.subscription_transaction_lock:
            receiver = app.state.subscription_receivers.pop(handle, None)
            app.state.subscription_receiver_providers.pop(handle, None)
        if close:
            close_subscription_receivers([receiver] if receiver is not None else [])

    def supersede_subscription_transactions(provider: SubscriptionProvider) -> None:
        receivers: list[object] = []
        with app.state.subscription_transaction_lock:
            for handle, (pending_provider, _transaction, _expires_at) in list(
                app.state.subscription_transactions.items()
            ):
                if pending_provider is provider:
                    del app.state.subscription_transactions[handle]
            for handle, receiver_provider in list(
                app.state.subscription_receiver_providers.items()
            ):
                if receiver_provider is not provider:
                    continue
                del app.state.subscription_receiver_providers[handle]
                receiver = app.state.subscription_receivers.pop(handle, None)
                if receiver is not None:
                    receivers.append(receiver)
        close_subscription_receivers(receivers)

    def discard_subscription_transaction(handle: str) -> None:
        with app.state.subscription_transaction_lock:
            app.state.subscription_transactions.pop(handle, None)

    def take_subscription_transaction(
        provider: SubscriptionProvider,
        state: str,
    ) -> tuple[str, SubscriptionLoginTransaction] | None:
        now = datetime.now(UTC)
        selected: tuple[str, SubscriptionLoginTransaction] | None = None
        expired_handles: list[str] = []
        with app.state.subscription_transaction_lock:
            for handle, (pending_provider, transaction, expires_at) in list(
                app.state.subscription_transactions.items()
            ):
                if expires_at <= now:
                    del app.state.subscription_transactions[handle]
                    expired_handles.append(handle)
                    continue
                state_matches = secrets.compare_digest(transaction.state, state)
                if pending_provider is provider and state_matches:
                    del app.state.subscription_transactions[handle]
                    selected = (handle, transaction)
                    break
        for handle in expired_handles:
            remove_subscription_receiver(handle, close=True)
        return selected

    def consume_subscription_transaction(
        provider: SubscriptionProvider,
        state: str,
    ) -> SubscriptionLoginTransaction | None:
        lifecycle_lock = app.state.subscription_lifecycle_locks[provider]
        with lifecycle_lock:
            selected = take_subscription_transaction(provider, state)
        return selected[1] if selected is not None else None

    def pending_subscription_transaction(
        provider: SubscriptionProvider,
    ) -> SubscriptionLoginTransaction | None:
        now = datetime.now(UTC)
        selected: SubscriptionLoginTransaction | None = None
        expired_handles: list[str] = []
        with app.state.subscription_transaction_lock:
            for handle, (pending_provider, transaction, expires_at) in list(
                app.state.subscription_transactions.items()
            ):
                if expires_at <= now:
                    del app.state.subscription_transactions[handle]
                    expired_handles.append(handle)
                    continue
                if pending_provider is provider:
                    selected = transaction
                    break
        for handle in expired_handles:
            remove_subscription_receiver(handle, close=True)
        return selected

    def complete_subscription_transaction(
        provider: SubscriptionProvider,
        backend: object | None,
        *,
        code: str,
        state: str,
        close_receiver: bool,
    ) -> None:
        lifecycle_lock = app.state.subscription_lifecycle_locks[provider]
        if not lifecycle_lock.acquire(blocking=False):
            raise SubscriptionError(
                "OAuth callback transaction is unavailable",
                provider=provider,
                category="authentication",
            )
        try:
            selected = take_subscription_transaction(provider, state)
            if selected is None:
                raise SubscriptionError(
                    "OAuth callback transaction is unavailable",
                    provider=provider,
                    category="authentication",
                )
            handle, transaction = selected
            try:
                if not isinstance(backend, SubscriptionLifecycleBackend):
                    raise SubscriptionError(
                        "OAuth callback transaction is unavailable",
                        provider=provider,
                        category="authentication",
                    )
                backend.complete_login(code, state, transaction)
            finally:
                if close_receiver:
                    remove_subscription_receiver(handle, close=True)
        finally:
            lifecycle_lock.release()

    def start_subscription_receiver(
        provider: SubscriptionProvider,
        backend: object,
        transaction: SubscriptionLoginTransaction,
        handle: str,
        redirect_uri: str,
    ) -> None:
        try:
            parsed = validate_loopback_redirect_uri(redirect_uri)
            if parsed.hostname is None or parsed.port is None:
                raise ValueError("OAuth callback URI is incomplete")
        except Exception:
            discard_subscription_transaction(handle)
            raise SubscriptionError(
                "OAuth callback URI is invalid",
                provider=provider,
                category="authentication",
            ) from None

        def callback_handler(callback: OAuthCallback) -> None:
            complete_subscription_transaction(
                provider,
                backend,
                code=callback.code,
                state=callback.state,
                close_receiver=False,
            )

        try:
            receiver = LoopbackOAuthReceiver(
                expected_state=transaction.state,
                pkce=transaction,
                host="0.0.0.0" if container_mode else "127.0.0.1",
                port=parsed.port,
                path=parsed.path,
                redirect_host=parsed.hostname,
                allow_fixed_port=True,
                container_mode=container_mode,
                timeout=_SUBSCRIPTION_TRANSACTION_TTL.total_seconds(),
                callback_handler=callback_handler,
            )
            register_subscription_receiver(provider, handle, receiver)
        except Exception:
            discard_subscription_transaction(handle)
            raise SubscriptionError(
                "OAuth callback listener is unavailable",
                provider=provider,
                category="transport",
            ) from None

        def wait_for_callback() -> None:
            try:
                receiver.receive()
            except BaseException as exc:
                if isinstance(exc, SubscriptionError):
                    category = _safe_subscription_diagnostic_token(
                        exc.category,
                        default="provider_error",
                    )
                    status = (
                        exc.status
                        if isinstance(exc.status, int) and 100 <= exc.status <= 599
                        else None
                    )
                    reason = _safe_subscription_diagnostic_token(
                        exc.metadata.get("reason"),
                        default="unknown",
                    )
                else:
                    category = "internal"
                    status = None
                    reason = "unexpected_error"
                _SUBSCRIPTION_LOGGER.warning(
                    "Subscription OAuth callback failed "
                    "provider=%s category=%s status=%s reason=%s",
                    provider.value,
                    category,
                    status,
                    reason,
                )
            finally:
                discard_subscription_transaction(handle)
                remove_subscription_receiver(handle, close=True)

        try:
            waiter = Thread(
                target=wait_for_callback,
                daemon=True,
                name="mudidi-subscription-oauth-callback",
            )
            waiter.start()
        except BaseException:
            discard_subscription_transaction(handle)
            remove_subscription_receiver(handle, close=True)
            raise SubscriptionError(
                "OAuth callback listener could not start",
                provider=provider,
                category="transport",
            ) from None

    @app.get("/subscriptions/status")
    @app.get("/auth/subscriptions/status")
    @app.get("/auth/subscription/status")
    async def subscription_status(request: Request) -> JSONResponse:
        require_local_subscription_request(request)
        providers = [
            subscription_status_payload(
                provider,
                subscription_backend(provider, required=False),
            )
            for provider in SubscriptionProvider
        ]
        return subscription_response({"providers": providers})

    @app.get("/subscriptions/providers")
    @app.get("/auth/subscriptions/providers")
    @app.get("/auth/subscription/providers")
    async def subscription_providers(request: Request) -> JSONResponse:
        require_local_subscription_request(request)
        providers: list[dict[str, object]] = []
        for provider in SubscriptionProvider:
            backend = subscription_backend(provider, required=False)
            payload = subscription_status_payload(provider, backend)
            if backend is not None:
                capabilities = getattr(backend, "capabilities", None)
                if capabilities is not None:
                    try:
                        values = capabilities.model_dump()
                    except AttributeError:
                        values = {}
                    if isinstance(values, Mapping):
                        payload["capabilities"] = {
                            str(key): bool(value) for key, value in values.items()
                        }
            providers.append(payload)
        return subscription_response({"providers": providers})

    @app.get("/subscriptions/{provider_name}/status")
    @app.get("/auth/subscriptions/{provider_name}/status")
    @app.get("/auth/subscription/{provider_name}/status")
    async def subscription_provider_status(
        request: Request,
        provider_name: str,
    ) -> JSONResponse:
        require_local_subscription_request(request)
        provider = subscription_provider(provider_name)
        backend = subscription_backend(provider)
        probe_status = getattr(backend, "probe_status", None)
        if provider is SubscriptionProvider.GOOGLE and callable(probe_status):
            probe_status(force=True)
        return subscription_response(subscription_status_payload(provider, backend))

    @app.get("/subscriptions/{provider_name}/login/launch")
    @app.get("/auth/subscriptions/{provider_name}/login/launch")
    @app.get("/auth/subscription/{provider_name}/login/launch")
    async def subscription_login_launch(
        request: Request,
        provider_name: str,
    ) -> Response:
        require_local_subscription_request(request)
        provider = subscription_provider(provider_name)
        subscription_backend(provider)
        with app.state.subscription_lifecycle_locks[provider]:
            transaction = pending_subscription_transaction(provider)
            if transaction is None:
                return subscription_lifecycle_error(
                    provider,
                    category="authentication",
                    message="Subscription authentication failed",
                    status_code=409,
                )
            return RedirectResponse(
                transaction.authorization_url,
                status_code=307,
                headers={"Cache-Control": "no-store"},
            )

    @app.post("/subscriptions/{provider_name}/login")
    @app.post("/auth/subscriptions/{provider_name}/login")
    @app.post("/auth/subscription/{provider_name}/login")
    async def subscription_login(
        request: Request,
        provider_name: str,
    ) -> JSONResponse:
        require_local_subscription_request(request)
        provider = subscription_provider(provider_name)
        backend = subscription_backend(provider)
        with app.state.subscription_lifecycle_locks[provider]:
            try:
                if not isinstance(backend, SubscriptionLifecycleBackend):
                    raise SubscriptionError(
                        "subscription login is unavailable",
                        provider=provider,
                        category="authentication",
                    )
                advertised_redirect_uri = getattr(backend, "login_redirect_uri", None)
                redirect_uri = (
                    advertised_redirect_uri
                    if isinstance(advertised_redirect_uri, str)
                    and advertised_redirect_uri
                    else subscription_callback_uri(request, provider)
                )
                transaction = backend.begin_login(redirect_uri)
                if not isinstance(transaction, SubscriptionLoginTransaction):
                    raise SubscriptionError(
                        "subscription login transaction is invalid",
                        provider=provider,
                        category="authentication",
                    )
                supersede_subscription_transactions(provider)
                handle, expires_at = save_subscription_transaction(
                    provider, transaction
                )
                if isinstance(advertised_redirect_uri, str) and advertised_redirect_uri:
                    start_subscription_receiver(
                        provider,
                        backend,
                        transaction,
                        handle,
                        advertised_redirect_uri,
                    )
                return subscription_response(
                    {
                        "provider": provider.value,
                        "status": "pending",
                        "launch_url": f"/subscriptions/{provider.value}/login/launch",
                        "expires_at": expires_at.isoformat(),
                    }
                )
            except SubscriptionError as exc:
                return subscription_lifecycle_error(
                    provider,
                    category=subscription_error_category(exc),
                    message=subscription_error_message(
                        exc,
                        provider=provider,
                    ),
                    status_code=subscription_error_status(exc),
                )
            except Exception:
                return subscription_lifecycle_error(
                    provider,
                    category="authentication",
                    message="Subscription authentication failed",
                    status_code=409,
                )

    @app.get("/subscriptions/{provider_name}/auth/callback")
    @app.get("/subscriptions/{provider_name}/oauth2callback")
    @app.get("/auth/subscriptions/{provider_name}/auth/callback")
    @app.get("/auth/subscriptions/{provider_name}/oauth2callback")
    @app.get("/auth/subscription/{provider_name}/auth/callback")
    @app.get("/auth/subscription/{provider_name}/oauth2callback")
    @app.get("/subscriptions/{provider_name}/callback")
    @app.get("/auth/subscriptions/{provider_name}/callback")
    @app.get("/auth/subscription/{provider_name}/callback")
    async def subscription_callback(
        request: Request,
        provider_name: str,
    ) -> JSONResponse:
        require_local_subscription_request(request)
        provider = subscription_provider(provider_name)
        backend = subscription_backend(provider, required=False)
        if backend is None:
            return subscription_lifecycle_error(
                provider,
                category="authentication",
                message="subscription provider unavailable",
                status_code=404,
            )
        if not subscription_callback_route_allowed(request, provider):
            return subscription_lifecycle_error(
                provider,
                category="authentication",
                message="OAuth callback path is invalid",
                status_code=400,
            )
        codes = request.query_params.getlist("code")
        states = request.query_params.getlist("state")
        if len(codes) != 1 or len(states) != 1 or not codes[0] or not states[0]:
            return subscription_lifecycle_error(
                provider,
                category="authentication",
                message="OAuth callback is invalid",
                status_code=400,
            )
        try:
            complete_subscription_transaction(
                provider,
                backend,
                code=codes[0],
                state=states[0],
                close_receiver=True,
            )
            payload = subscription_status_payload(provider, backend)
            payload["status"] = "authenticated"
            return subscription_response(payload)
        except SubscriptionError as exc:
            _SUBSCRIPTION_LOGGER.warning(
                "Subscription OAuth callback failed "
                "provider=%s category=%s status=%s reason=%s",
                provider.value,
                _safe_subscription_diagnostic_token(
                    exc.category,
                    default="provider_error",
                ),
                (
                    exc.status
                    if isinstance(exc.status, int) and 100 <= exc.status <= 599
                    else None
                ),
                _safe_subscription_diagnostic_token(
                    exc.metadata.get("reason"),
                    default="unknown",
                ),
            )
            return subscription_lifecycle_error(
                provider,
                category=subscription_error_category(exc),
                message=subscription_error_message(
                    exc,
                    provider=provider,
                ),
                status_code=subscription_error_status(exc),
            )
        except Exception:
            return subscription_lifecycle_error(
                provider,
                category="authentication",
                message="OAuth callback could not be completed",
                status_code=400,
            )

    @app.get("/oauth2callback")
    async def google_subscription_callback(request: Request) -> JSONResponse:
        return await subscription_callback(request, "google")

    @app.post("/subscriptions/{provider_name}/logout")
    @app.delete("/subscriptions/{provider_name}/logout")
    @app.post("/auth/subscriptions/{provider_name}/logout")
    @app.delete("/auth/subscriptions/{provider_name}/logout")
    @app.post("/auth/subscription/{provider_name}/logout")
    @app.delete("/auth/subscription/{provider_name}/logout")
    async def subscription_logout(
        request: Request,
        provider_name: str,
    ) -> JSONResponse:
        require_local_subscription_request(request)
        provider = subscription_provider(provider_name)
        backend = subscription_backend(provider)
        with app.state.subscription_lifecycle_locks[provider]:
            supersede_subscription_transactions(provider)
            try:
                logout = getattr(backend, "logout", None)
                if not callable(logout):
                    raise SubscriptionError(
                        "subscription logout is unavailable",
                        provider=provider,
                        category="authentication",
                    )
                logout()
                payload = subscription_status_payload(provider, backend)
                payload["status"] = "logged_out"
                return subscription_response(payload)
            except SubscriptionError as exc:
                return subscription_lifecycle_error(
                    provider,
                    category=subscription_error_category(exc),
                    message=subscription_error_message(
                        exc,
                        provider=provider,
                    ),
                    status_code=subscription_error_status(exc),
                )
            except Exception:
                return subscription_lifecycle_error(
                    provider,
                    category="authentication",
                    message="Subscription logout failed",
                    status_code=409,
                )

    @app.post("/runs/preview", response_class=HTMLResponse)
    async def preview_run(request: Request) -> HTMLResponse:
        """Validate browser form state and render a non-secret run review."""

        submitted = await request.form()
        upload_fields = {
            "dictionary_pdf",
            "page_files",
            "page_directory",
            "introduction_file",
            "introduction_directory",
            "alphabet_file",
            "existing_mdf_guide_file",
            "custom_mdf_manual",
            "stage1_instruction_file",
            "stage2_instruction_file",
        }
        retired_dashboard_fields = {
            "page_limit",
            "media_reference",
            "prompt_cache",
            "stage1_instruction_keep_existing",
            "stage2_instruction_keep_existing",
        }
        client_only_fields = {
            # Browser-only Keep/Replace radio for a preset's saved attachment;
            # process_instruction_stage() derives keep_existing itself and
            # NewRunForm has no such field (extra="forbid").
            "stage1_instruction_kept_choice",
            "stage2_instruction_kept_choice",
        }
        payload = {
            key: value
            for key, value in submitted.items()
            if key not in upload_fields
            and key not in retired_dashboard_fields
            and key not in client_only_fields
            and key != "pages"
            and isinstance(value, str)
            and value.strip() != ""
        }
        if payload.get("output_policy") == "new":
            # Migrate browser-tab state saved before the output-policy redesign.
            payload["output_policy"] = "resume"
        preset_id = str(payload.pop("preset_id", "")).strip()
        parse_rule_pages = [
            page.strip()
            for value in submitted.getlist("parse_rules_pages")
            for page in str(value).split(",")
            if page.strip()
        ]
        if parse_rule_pages:
            payload["parse_rules_pages"] = parse_rule_pages
        for field_name in (
            "profile_target_languages",
            "profile_target_scripts",
            "profile_information_types",
        ):
            values = [
                str(value).strip()
                for value in submitted.getlist(field_name)
                if str(value).strip()
            ]
            if values:
                payload[field_name] = values
        run_id = f"run-{uuid4().hex[:12]}"
        dictionary_pdfs = [
            value
            for value in submitted.getlist("dictionary_pdf")
            if getattr(value, "filename", "")
        ]
        retired_page_uploads = [
            value
            for field in ("page_files", "page_directory")
            for value in submitted.getlist(field)
            if getattr(value, "filename", "")
        ]

        def uploaded(field: str) -> list[object]:
            return [
                value
                for value in submitted.getlist(field)
                if getattr(value, "filename", "")
            ]

        def instruction_scalar(
            field: str,
            *,
            default: str,
            error_field: str,
        ) -> tuple[str, bool]:
            values = submitted.getlist(field)
            if len(values) > 1:
                raise FormFieldError(
                    error_field,
                    f"Submit only one value for {field.replace('_', ' ')}.",
                )
            if not values:
                return default, False
            value = values[0]
            if not isinstance(value, str):
                raise FormFieldError(
                    error_field,
                    f"{field.replace('_', ' ').title()} must be a text value.",
                )
            return value, True

        async def process_instruction_stage(stage: str, *, active: bool) -> None:
            """Apply one stage's typed/file instruction state to ``payload``."""

            source_field = f"{stage}_instruction_source"
            file_field = f"{stage}_instruction_file"
            text_field = f"{stage}_additional_instructions"
            pages_field = f"{stage}_instruction_pdf_pages"
            keep_field = f"{stage}_instruction_keep_existing"
            guide_field = f"{stage}_guides"
            source_raw, _source_submitted = instruction_scalar(
                source_field,
                default="typed",
                error_field=file_field,
            )
            source = source_raw.strip() or "typed"
            text_raw, _text_submitted = instruction_scalar(
                text_field,
                default="",
                error_field=file_field,
            )
            text = text_raw.strip()
            page_raw, page_was_submitted = instruction_scalar(
                pages_field,
                default="",
                error_field=pages_field,
            )
            page_spec = page_raw.strip() or None
            keep_raw, _keep_submitted = instruction_scalar(
                keep_field,
                default="",
                error_field=file_field,
            )
            keep_value = keep_raw.strip().lower()
            if keep_value not in {"", "true", "false"}:
                raise FormFieldError(
                    file_field,
                    "Instruction keep-existing state is invalid.",
                )
            keep_existing = keep_value == "true"
            scope = None
            if stage == "stage2":
                scope_raw, _scope_submitted = instruction_scalar(
                    "stage2_instruction_scope",
                    default="both",
                    error_field=file_field,
                )
                scope = scope_raw.strip() or "both"
                if scope not in {"pass1", "pass2", "both"}:
                    raise FormFieldError(
                        file_field,
                        "Stage 2 instruction scope is invalid.",
                    )
            payload[source_field] = source
            payload[text_field] = text or None
            payload[pages_field] = page_spec
            payload[keep_field] = keep_existing
            if stage == "stage2":
                payload["stage2_instruction_scope"] = scope
            files = uploaded(file_field)
            relevant = bool(
                files
                or text
                or page_spec
                or keep_existing
                or source != "typed"
                or (stage == "stage2" and scope not in {None, "both"})
            )
            if source not in {"typed", "file"}:
                raise FormFieldError(
                    file_field,
                    "Choose typed or file instructions.",
                )
            if not active:
                if relevant:
                    raise FormFieldError(
                        file_field,
                        f"{stage.title()} instructions are not used by this pipeline.",
                    )
                return

            inherited = (
                getattr(preset_config.pipeline, guide_field)
                if preset_config is not None
                else None
            )
            inherited_page_spec = (
                str(
                    getattr(
                        preset_config.pipeline,
                        f"{stage}_guides_pages",
                        "",
                    )
                    or ""
                ).strip()
                or None
                if preset_config is not None
                else None
            )
            run_bundle = app.state.inputs.bundle(run_id).resolve()

            def managed_inherited_file() -> bool:
                if inherited is None or inherited.is_symlink():
                    return False
                try:
                    resolved = inherited.expanduser().resolve()
                except OSError:
                    return False
                if not resolved.is_relative_to(run_bundle) or not resolved.is_file():
                    return False
                metadata = read_managed_instruction_metadata(resolved)
                return bool(metadata and metadata.get("source_mode") == "file")

            def discard_inherited() -> None:
                if inherited is None or inherited.is_symlink():
                    return
                try:
                    resolved = inherited.expanduser().resolve()
                except OSError:
                    return
                instructions_root = run_bundle / "instructions"
                stage_dir = instructions_root / stage
                if resolved.is_relative_to(stage_dir) and stage_dir.is_dir():
                    shutil.rmtree(stage_dir, ignore_errors=True)
                elif resolved.is_relative_to(instructions_root) and resolved.is_file():
                    resolved.unlink(missing_ok=True)

            if source == "typed":
                if files:
                    raise FormFieldError(
                        file_field,
                        "Remove the uploaded file before using typed instructions.",
                    )
                if page_spec is not None:
                    raise FormFieldError(
                        pages_field,
                        "Instruction PDF page selection requires a PDF source.",
                    )
                if text:
                    try:
                        payload[guide_field] = app.state.inputs.materialize_instruction(
                            run_id,
                            stage,
                            text,
                            replace=preset_config is not None,
                            stage2_scope=scope,
                        )
                    except ValueError as exc:
                        raise FormFieldError(file_field, str(exc)) from exc
                elif preset_config is not None:
                    discard_inherited()
                    payload[guide_field] = None
                return

            if text:
                raise FormFieldError(
                    file_field,
                    "Clear typed instructions before using an uploaded file.",
                )
            if len(files) > 1:
                raise FormFieldError(file_field, "Upload exactly one instruction file.")
            if files:
                try:
                    payload[
                        guide_field
                    ] = await app.state.inputs.materialize_instruction_upload(
                        run_id,
                        stage,
                        files[0],
                        page_spec=page_spec,
                        stage2_scope=scope,
                        replace=preset_config is not None,
                    )
                except InstructionMaterializationError as exc:
                    error_field = pages_field if exc.category == "pages" else file_field
                    raise FormFieldError(error_field, str(exc)) from exc
                except ValueError as exc:
                    raise FormFieldError(file_field, str(exc)) from exc
                return
            if keep_existing and managed_inherited_file():
                effective_page_spec = (
                    page_spec if page_was_submitted else inherited_page_spec
                )
                try:
                    payload[guide_field] = app.state.inputs.refresh_managed_instruction(
                        run_id,
                        stage,
                        inherited,
                        page_spec=effective_page_spec,
                        stage2_scope=scope,
                    )
                except InstructionMaterializationError as exc:
                    error_field = pages_field if exc.category == "pages" else file_field
                    raise FormFieldError(error_field, str(exc)) from exc
                except ValueError as exc:
                    raise FormFieldError(file_field, str(exc)) from exc
                payload[pages_field] = effective_page_spec
                return
            raise FormFieldError(
                file_field,
                "Upload exactly one instruction file, or explicitly keep the saved file.",
            )

        try:
            preset_config = None
            if preset_id:
                try:
                    preset = app.state.run_store.get_preset(preset_id)
                except KeyError as exc:
                    raise ValueError("the selected preset no longer exists") from exc
                preset_bundle = app.state.inputs.presets_root / preset_id / "inputs"
                run_bundle = app.state.inputs.copy_preset_to_run(preset_id, run_id)
                preset_config = rebase_managed_config(
                    preset.config,
                    source=preset_bundle,
                    destination=run_bundle,
                )
            forbidden_paths = {
                "introduction",
                "alphabet",
                "toolbox_pdf",
                "parse_rules_file",
                "stage1_guides",
                "stage2_guides",
            }
            if forbidden_paths.intersection(submitted.keys()):
                raise ValueError(
                    "dashboard input paths must be selected in the browser"
                )
            if str(submitted.get("pages", "")).strip():
                raise ValueError("select dictionary files in the browser")
            if retired_page_uploads:
                raise FormFieldError("pages", "Upload one PDF dictionary file.")
            if len(dictionary_pdfs) > 1:
                raise FormFieldError("pages", "Upload exactly one dictionary PDF.")
            if dictionary_pdfs:
                filename = str(getattr(dictionary_pdfs[0], "filename", ""))
                if Path(filename).suffix.lower() != ".pdf":
                    raise FormFieldError("pages", "Upload one PDF dictionary file.")
                payload["pages"] = await app.state.inputs.materialize_pages(
                    run_id,
                    dictionary_pdfs,
                    replace=preset_config is not None,
                )
            elif preset_config is not None:
                payload["pages"] = preset_config.input.pages
            else:
                raise FormFieldError("pages", "Upload one PDF dictionary file.")

            pipeline_value = str(payload.get("pipeline", "complete"))
            runs_stage1 = pipeline_value in {"complete", "transcription"}
            runs_stage2 = pipeline_value in {"complete", "structure"}

            character_inventory = str(payload.pop("character_inventory", "")).strip()
            if character_inventory and runs_stage1:
                payload["alphabet"] = app.state.inputs.materialize_instruction(
                    run_id,
                    "character_inventory",
                    character_inventory,
                    replace=preset_config is not None,
                )
            elif preset_config is not None and runs_stage1:
                payload["alphabet"] = preset_config.input.alphabet

            guide_files = uploaded("existing_mdf_guide_file")
            if len(guide_files) > 1:
                raise ValueError("select exactly one existing MDF parsing guide")
            if guide_files and not runs_stage2:
                raise ValueError(
                    "an MDF parsing guide requires an MDF parsing pipeline"
                )
            if guide_files:
                payload[
                    "parse_rules_file"
                ] = await app.state.inputs.materialize_mdf_guide(
                    run_id,
                    guide_files[0],
                    replace=preset_config is not None,
                )
            elif preset_config is not None and runs_stage2:
                payload["parse_rules_file"] = preset_config.pipeline.parse_rules_file

            await process_instruction_stage("stage1", active=runs_stage1)
            await process_instruction_stage("stage2", active=runs_stage2)

            manual_source = str(payload.get("mdf_manual_source", "none"))
            manual_files = uploaded("custom_mdf_manual")
            if not runs_stage2:
                if manual_files:
                    raise ValueError("an MDF manual requires an MDF parsing pipeline")
                manual_source = "none"
                payload["mdf_manual_source"] = "none"
            if manual_source == "upload":
                if len(manual_files) == 1:
                    payload[
                        "toolbox_pdf"
                    ] = await app.state.inputs.materialize_mdf_manual(
                        run_id,
                        manual_files[0],
                        replace=preset_config is not None,
                    )
                elif preset_config is not None and preset_config.input.toolbox_pdf:
                    payload["toolbox_pdf"] = preset_config.input.toolbox_pdf
                else:
                    raise ValueError("upload exactly one custom MDF manual PDF")
            elif manual_files:
                raise ValueError("select the custom MDF manual option before uploading")

            if container_mode and "output_directory" in payload:
                payload["output_directory"] = _container_output_directory(
                    str(payload["output_directory"])
                )
            run_form = NewRunForm.model_validate(payload)
            config = run_form.to_inference_config()
            validate_config_paths(config)
            if config.auth.mode is AuthMode.SUBSCRIPTION:
                for provider in config.auth.providers:
                    backend = subscription_backend(provider, required=False)
                    if backend is None:
                        raise FormFieldError(
                            "auth_mode",
                            f"Log in to the {provider.value} subscription before "
                            "starting a run.",
                        )
                    try:
                        resolve_subscription_runtime(
                            AuthConfig(
                                mode=AuthMode.SUBSCRIPTION,
                                providers=(provider,),
                            ),
                            backend=backend,
                        )
                    except SubscriptionError as exc:
                        message = (
                            f"Log in to the {provider.value} subscription before "
                            "starting a run."
                            if exc.metadata.get("reason") == "missing_credential"
                            else subscription_error_message(exc, provider=provider)
                        )
                        raise FormFieldError("auth_mode", message) from exc
                    except Exception as exc:
                        raise FormFieldError(
                            "auth_mode",
                            f"{provider.value} subscription authentication failed",
                        ) from exc
                    status_payload = subscription_status_payload(provider, backend)
                    if not bool(status_payload.get("authenticated")):
                        category = str(status_payload.get("category", "authentication"))
                        if category == "expired_session":
                            message = "Subscription session expired; log in again"
                        elif category == "transport":
                            message = "Subscription provider is unavailable"
                        else:
                            message = (
                                f"Log in to the {provider.value} subscription before "
                                "starting a run."
                            )
                        raise FormFieldError("auth_mode", message)
                    web_provider = {
                        SubscriptionProvider.OPENAI: Provider.OPENAI,
                        SubscriptionProvider.GOOGLE: Provider.GEMINI,
                        SubscriptionProvider.CLAUDE: Provider.ANTHROPIC,
                    }[provider]
                    catalog_result = app.state.model_catalog_service.list_models(
                        web_provider,
                        stage=CatalogStage.STAGE1,
                        auth_mode=CatalogAuthMode.SUBSCRIPTION,
                    )
                    if catalog_result.source in {
                        "authentication_required",
                        "unavailable",
                    }:
                        raise FormFieldError(
                            "auth_mode",
                            f"{provider.value} subscription model catalog is unavailable",
                        )
                    allowed_models = {
                        item.model_id
                        for item in (
                            *catalog_result.recommended,
                            *catalog_result.available,
                        )
                    }
                    configured_models: list[tuple[str, str | None]] = [
                        ("model", config.models.default),
                        ("stage1_model", config.models.stage1),
                        ("stage2_pass1_model", config.models.stage2_pass1),
                        ("stage2_pass2_model", config.models.stage2_pass2),
                    ]
                    if config.agentic.stage1 or config.agentic.stage2:
                        configured_models.extend(
                            (
                                ("evaluator_model", config.agentic.evaluator_model),
                                ("rewriter_model", config.agentic.rewriter_model),
                            )
                        )
                    for field, model_id in configured_models:
                        if (
                            model_id is not None
                            and subscription_provider_for_model(model_id) is provider
                            and model_id not in allowed_models
                        ):
                            raise FormFieldError(
                                field,
                                f"Select a model currently available from the "
                                f"{provider.value} subscription.",
                            )
        except (ValidationError, ValueError) as exc:
            app.state.inputs.discard(run_id)
            validation_errors = _validation_errors(exc)
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="home.html",
                context=home_context(
                    request,
                    preset_id=preset_id,
                    validation_errors=validation_errors,
                ),
                status_code=422,
            )
        try:
            app.state.job_controller.prepare_inference(
                run_id,
                config=config,
                provider=_primary_provider_for_config(config),
            )
        except Exception:
            app.state.inputs.discard(run_id)
            raise
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "summary": _config_summary(config),
                "run_id": run_id,
                "continuation_action": f"/runs/{run_id}/start",
                "continuation_label": "Start run",
            },
        )

    @app.get("/runs/{run_id}/review", response_class=HTMLResponse)
    async def review_prepared_run(
        request: Request,
        run_id: str,
        preset_saved: bool = False,
    ) -> HTMLResponse:
        """Render the persisted non-secret review for a prepared run."""

        try:
            run = app.state.run_store.get_run(run_id)
            config = app.state.job_controller.load_inference_config(run_id)
        except (KeyError, OSError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        continuation_action, continuation_label = _review_continuation(run)
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "summary": _config_summary(config),
                "run_id": run_id,
                "continuation_action": continuation_action,
                "continuation_label": continuation_label,
                "preset_saved": preset_saved,
            },
        )

    @app.post("/runs/{run_id}/start")
    async def start_prepared_run(request: Request, run_id: str) -> HTMLResponse:
        """Start a validated production run after resolving its provider key."""

        try:
            run = app.state.run_store.get_run(run_id)
            config = app.state.job_controller.load_inference_config(run_id)
        except (KeyError, OSError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        subscription = config.auth.mode is AuthMode.SUBSCRIPTION
        credentials, missing = (
            ((), ())
            if subscription
            else _resolve_api_credentials(config, app.state.credential_vault)
        )
        if missing:
            provider = missing[0]
            if run.status is RunStatus.VALIDATED:
                app.state.run_store.transition(run_id, RunStatus.CREDENTIALS_REQUIRED)
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="credential_required.html",
                context={
                    "run_id": run_id,
                    "provider": provider.value,
                    "continue_action": f"/runs/{run_id}/start",
                },
                status_code=409,
            )
        try:
            app.state.job_controller.start_inference(
                run_id,
                credentials=credentials,
                offline_executor=app.state.offline_inference,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/credentials/{provider_name}")
    async def set_provider_credential(
        request: Request,
        provider_name: str,
    ) -> JSONResponse:
        """Encrypt and persist one provider credential locally."""

        try:
            provider = Provider(provider_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="unknown provider") from exc
        submitted = await request.form()
        api_key = str(submitted.get("api_key", ""))
        try:
            app.state.credential_vault.set_persistent(provider, api_key)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="API key cannot be empty"
            ) from exc
        app.state.model_catalog_service.invalidate(provider)
        return JSONResponse(
            {"status": "saved", "provider": provider.value},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/credentials/{provider_name}/reveal")
    async def reveal_provider_credential(provider_name: str) -> JSONResponse:
        """Reveal a saved key only after an explicit same-origin action."""

        try:
            provider = Provider(provider_name)
            api_key = app.state.credential_vault.reveal_persistent(provider)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="saved key not found") from exc
        return JSONResponse(
            {"api_key": api_key},
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/credentials/{provider_name}/delete",
    )
    async def delete_provider_credential(
        provider_name: str,
    ) -> JSONResponse:
        """Delete one encrypted provider credential."""

        try:
            provider = Provider(provider_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="unknown provider") from exc
        app.state.credential_vault.clear_persistent(provider)
        app.state.model_catalog_service.invalidate(provider)
        status = app.state.credential_vault.status(provider)
        return JSONResponse(
            {
                "status": "deleted",
                "provider": provider.value,
                "available": status.available,
                "source": status.source.value,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/runs/demo")
    async def start_demo(request: Request) -> RedirectResponse:
        """Start a deterministic offline run to exercise the complete run UI."""

        submitted = await request.form()
        try:
            page_count = int(str(submitted.get("page_count", "3")))
            delay_seconds = float(str(submitted.get("delay_seconds", "0.08")))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="invalid demo settings"
            ) from exc
        run_id = f"demo-{uuid4().hex[:12]}"
        app.state.run_store.create_run(run_id, provider="offline")
        app.state.run_store.transition(run_id, RunStatus.VALIDATED)
        app.state.run_store.transition(run_id, RunStatus.QUEUED)
        try:
            app.state.job_controller.start_fake(
                run_id,
                page_count=page_count,
                delay_seconds=delay_seconds,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/active", response_class=HTMLResponse)
    async def active_run(request: Request) -> HTMLResponse:
        """Render the currently active worker, if any."""

        active = [
            view
            for run in app.state.run_store.list_active_runs()
            if (view := _run_view(app.state.run_store, run))["is_active"]
        ]
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="active.html",
            context={"runs": active},
        )

    @app.get("/history", response_class=HTMLResponse)
    async def run_history(
        request: Request,
        q: str = "",
        status: str = "",
        provider: str = "",
    ) -> HTMLResponse:
        """Render durable newest-first local run history."""

        all_runs = app.state.run_store.list_runs()
        runs = all_runs
        query = q.strip().casefold()
        if query:
            runs = [run for run in runs if query in run.run_id.casefold()]
        if status:
            runs = [run for run in runs if run.status.value == status]
        if provider:
            runs = [run for run in runs if run.provider == provider]
        history_views = []
        for run in runs:
            view = _run_view(app.state.run_store, run)
            view["output_directory"] = _history_output_directory(
                app.state.job_controller, run.run_id
            )
            history_views.append(view)
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "runs": history_views,
                "filters": {"q": q, "status": status, "provider": provider},
                "statuses": tuple(RunStatus),
                "status_label": _status_label,
                "providers": tuple(
                    provider for provider in Provider if provider is not Provider.CUSTOM
                ),
                "has_deletable_runs": any(
                    can_delete_run(run.status) for run in all_runs
                ),
            },
        )

    @app.get("/presets", response_class=HTMLResponse)
    async def presets(request: Request) -> HTMLResponse:
        """List reusable non-secret configurations."""

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="presets.html",
            context={"presets": app.state.run_store.list_presets()},
        )

    @app.post("/presets/{preset_id}/delete")
    async def delete_preset(preset_id: str) -> RedirectResponse:
        """Delete preset metadata and its managed input bundle."""

        try:
            app.state.run_store.delete_preset(preset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preset not found") from exc
        app.state.inputs.discard_preset(preset_id)
        return RedirectResponse("/presets", status_code=303)

    @app.get("/presets/{preset_id}/files/{asset_key:path}")
    async def preset_file(preset_id: str, asset_key: str) -> FileResponse:
        """Serve one allowlisted file owned by a local preset."""

        try:
            preset = app.state.run_store.get_preset(preset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preset not found") from exc
        assets = _preset_asset_paths(
            preset,
            presets_root=app.state.inputs.presets_root,
        )
        path = assets.get(asset_key)
        if path is None:
            raise HTTPException(status_code=404, detail="preset file not found")
        return FileResponse(path, headers={"Cache-Control": "private, no-store"})

    @app.post("/runs/{run_id}/presets")
    async def save_run_preset(request: Request, run_id: str) -> RedirectResponse:
        """Save the prepared run's typed configuration as a reusable preset."""

        try:
            run = app.state.run_store.get_run(run_id)
            config = app.state.job_controller.load_inference_config(run_id)
        except (KeyError, OSError, ValidationError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        submitted = await request.form()
        name = str(submitted.get("name", ""))
        existing = app.state.run_store.get_preset_by_name(name)
        preset_id = f"preset-{uuid4().hex[:12]}"
        try:
            preset_bundle = app.state.inputs.copy_run_to_preset(run_id, preset_id)
            preset_config = rebase_managed_config(
                config,
                source=app.state.inputs.bundle(run_id),
                destination=preset_bundle,
            )
            app.state.run_store.create_preset(
                preset_id,
                name=name,
                provider=str(run.provider),
                config=preset_config,
            )
            if existing is not None and existing.preset_id != preset_id:
                app.state.inputs.discard_preset(existing.preset_id)
        except (ValueError, sqlite3.IntegrityError) as exc:
            app.state.inputs.discard_preset(preset_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(
            f"/runs/{run_id}/review?preset_saved=1",
            status_code=303,
        )

    @app.post("/presets/{preset_id}/prepare", response_class=HTMLResponse)
    async def prepare_preset(request: Request, preset_id: str) -> HTMLResponse:
        """Revalidate a preset and clone it into a fresh prepared run."""

        try:
            preset = app.state.run_store.get_preset(preset_id)
            provider = Provider(preset.provider)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preset not found") from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        run_id = f"run-{uuid4().hex[:12]}"
        try:
            run_bundle = app.state.inputs.copy_preset_to_run(preset_id, run_id)
            preset_bundle = app.state.inputs.presets_root / preset_id / "inputs"
            config = rebase_managed_config(
                preset.config,
                source=preset_bundle,
                destination=run_bundle,
            )
            validate_config_paths(config)
            app.state.job_controller.prepare_inference(
                run_id,
                config=config,
                provider=provider,
            )
        except (ValueError, OSError, ValidationError) as exc:
            app.state.inputs.discard(run_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="review.html",
            context={"summary": _config_summary(config), "run_id": run_id},
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> HTMLResponse:
        """Render current state and persisted event progress for one run."""

        try:
            run = app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        run_view = _run_view(app.state.run_store, run)
        try:
            config = app.state.job_controller.load_inference_config(run_id)
        except (KeyError, OSError, ValidationError, ValueError):
            config = None
        if config is not None:
            billing_mode = config.auth.mode.value
            providers = (
                ", ".join(provider.value for provider in config.auth.providers) or None
            )
            run_view.update(
                {
                    "auth_mode": billing_mode,
                    "auth_providers": providers or "None",
                    "billing_mode": billing_mode,
                    "billing": (
                        "subscription" if billing_mode == "subscription" else "api_key"
                    ),
                    "billing_label": (
                        "Subscription billing"
                        if billing_mode == "subscription"
                        else "API-key billing"
                    ),
                }
            )
        run_view["workspace_available"] = _managed_config_available(
            app.state.job_controller,
            run_id,
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={"run": run_view},
        )

    @app.get("/runs/{run_id}/parse-rules", response_class=HTMLResponse)
    async def parse_rule_editor(request: Request, run_id: str) -> HTMLResponse:
        """Render the complete structured parse-rule review form."""

        return _render_parse_rule_editor(request, app, run_id)

    @app.get("/runs/{run_id}/outputs", response_class=HTMLResponse)
    async def run_outputs(request: Request, run_id: str) -> HTMLResponse:
        """List regular files beneath the run's validated output root."""

        try:
            artifacts = app.state.artifacts.list_artifacts(run_id)
        except (KeyError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail="run outputs not found"
            ) from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="outputs.html",
            context={"run_id": run_id, "artifacts": artifacts},
        )

    @app.get("/runs/{run_id}/pages", response_class=HTMLResponse)
    async def run_pages(request: Request, run_id: str) -> Response:
        """Open the page editor at the first processed page or show an empty state."""

        try:
            run = app.state.run_store.get_run(run_id)
            events = app.state.run_store.list_events(run_id)
            pages = app.state.artifacts.list_pages(run_id)
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run pages not found") from exc
        if pages:
            return RedirectResponse(
                f"/runs/{run_id}/pages/{pages[0].page_id}",
                status_code=303,
            )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="pages.html",
            context={
                "run_id": run_id,
                "is_active": _run_is_active(run, events),
                "last_event_sequence": max(
                    (int(event.get("sequence", 0)) for event in events),
                    default=0,
                ),
            },
        )

    @app.get("/runs/{run_id}/pages/{page_id}", response_class=HTMLResponse)
    async def page_detail(request: Request, run_id: str, page_id: str) -> HTMLResponse:
        """Render source and editable generated text for one processed page."""

        try:
            run = app.state.run_store.get_run(run_id)
            events = app.state.run_store.list_events(run_id)
            pages = app.state.artifacts.list_pages(run_id)
            page = next(item for item in pages if item.page_id == page_id)
            page_index = pages.index(page)
            source = app.state.artifacts.source_page(run_id, page_id)
            stage1 = (
                app.state.artifacts.editable_text(run_id, page.stage1)
                if page.stage1
                else None
            )
            stage2 = (
                app.state.artifacts.editable_text(run_id, page.stage2)
                if page.stage2
                else None
            )
            related = [
                artifact
                for artifact in app.state.artifacts.list_artifacts(run_id)
                if page_id in artifact.relative_path.parts
            ]
        except (
            ArtifactAccessError,
            KeyError,
            OSError,
            StopIteration,
            ValueError,
        ) as exc:
            raise HTTPException(status_code=404, detail="page not found") from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="page_detail.html",
            context={
                "run_id": run_id,
                "page_id": page_id,
                "source_is_image": source.suffix.lower() != ".pdf",
                "stage1": stage1,
                "stage2": stage2,
                "artifacts": related,
                "page_index": page_index,
                "page_count": len(pages),
                "page_urls": [f"/runs/{run_id}/pages/{item.page_id}" for item in pages],
                "page_labels": [item.page_id.removeprefix("page_") for item in pages],
                "previous_page": pages[page_index - 1] if page_index > 0 else None,
                "next_page": (
                    pages[page_index + 1] if page_index + 1 < len(pages) else None
                ),
                "saved": request.query_params.get("saved") == "1",
                "is_active": _run_is_active(run, events),
                "last_event_sequence": max(
                    (int(event.get("sequence", 0)) for event in events),
                    default=0,
                ),
            },
        )

    @app.post("/runs/{run_id}/pages/{page_id}/edit")
    async def edit_page_artifacts(
        request: Request,
        run_id: str,
        page_id: str,
    ) -> RedirectResponse:
        """Persist explicit corrections to existing Stage 1 and Stage 2 files."""

        submitted = await request.form()
        updates = 0
        try:
            for field, stage in (
                ("stage1_text", "stage1"),
                ("stage2_text", "stage2"),
            ):
                value = submitted.get(field)
                if value is None:
                    continue
                app.state.artifacts.update_page_text(
                    run_id,
                    page_id,
                    stage,
                    str(value),
                )
                updates += 1
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail="page artifact not found",
            ) from exc
        if updates == 0:
            raise HTTPException(
                status_code=422,
                detail="no editable page text submitted",
            )
        return RedirectResponse(
            f"/runs/{run_id}/pages/{page_id}?saved=1",
            status_code=303,
        )

    @app.get("/runs/{run_id}/pages/{page_id}/source")
    async def source_page(run_id: str, page_id: str) -> FileResponse:
        """Serve only a validated source page from the configured input root."""

        try:
            path = app.state.artifacts.source_page(run_id, page_id)
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail="source page not found"
            ) from exc
        return FileResponse(
            path,
            headers={_ALLOW_SAME_ORIGIN_FRAME_HEADER: "1"},
        )

    @app.get("/runs/{run_id}/usage", response_class=HTMLResponse)
    async def run_usage(request: Request, run_id: str) -> HTMLResponse:
        """Render token and cost totals from generated usage artifacts."""

        try:
            usage = app.state.artifacts.usage_summary(run_id)
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run usage not found") from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="usage.html",
            context={"run_id": run_id, "usage": usage},
        )

    @app.get("/runs/{run_id}/logs", response_class=HTMLResponse)
    async def run_logs(request: Request, run_id: str) -> HTMLResponse:
        """Render a bounded, redacted view of the app-managed worker log."""

        try:
            run = app.state.run_store.get_run(run_id)
            log_path = app.state.job_controller.log_path(run_id)
            events = app.state.run_store.list_events(run_id)
        except (KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        content, truncated = _read_log_tail(
            log_path,
            redactions=app.state.credential_vault.redaction_values(),
        )
        failure_message = _failure_message(events)
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="logs.html",
            context={
                "run_id": run_id,
                "content": content,
                "truncated": truncated,
                "failure_message": failure_message,
                "is_active": _run_is_active(run, events),
                "last_event_sequence": max(
                    (int(event.get("sequence", 0)) for event in events),
                    default=0,
                ),
            },
        )

    @app.get("/runs/{run_id}/artifacts/{artifact_path:path}")
    async def download_artifact(run_id: str, artifact_path: str) -> FileResponse:
        """Download one safe regular file beneath the configured output root."""

        try:
            path = app.state.artifacts.resolve(run_id, artifact_path)
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return FileResponse(path, filename=path.name)

    @app.post("/runs/{run_id}/parse-rules/draft", response_class=HTMLResponse)
    async def save_parse_rule_draft(
        request: Request,
        run_id: str,
    ) -> HTMLResponse:
        """Validate and save a structured draft without implying approval."""

        submitted = await request.form()
        try:
            payload = _parse_rule_form(submitted)
            app.state.parse_rule_reviews.save_draft(run_id, payload)
        except (KeyError, ValueError) as exc:
            return _render_parse_rule_editor(
                request,
                app,
                run_id,
                message=str(exc),
                status_code=422,
            )
        return _render_parse_rule_editor(
            request,
            app,
            run_id,
            message="Draft saved",
        )

    @app.post("/runs/{run_id}/parse-rules/approve")
    async def approve_parse_rules(request: Request, run_id: str) -> HTMLResponse:
        """Explicitly approve the current valid rules and authorize Pass 2."""

        try:
            credentials: tuple[ResolvedCredential, ...] = ()
            managed_config = app.state.job_controller.config_path(run_id)
            if managed_config.is_file():
                config = app.state.job_controller.load_inference_config(run_id)
                if config.auth.mode is not AuthMode.SUBSCRIPTION:
                    credentials, missing = _resolve_api_credentials(
                        config,
                        app.state.credential_vault,
                    )
                    if missing:
                        return _render_parse_rule_editor(
                            request,
                            app,
                            run_id,
                            message=(
                                "API credential required before Pass 2 can start: "
                                + ", ".join(provider.value for provider in missing)
                            ),
                            status_code=409,
                        )
            submitted = await request.form()
            if submitted:
                app.state.parse_rule_reviews.save_draft(
                    run_id,
                    _parse_rule_form(submitted),
                )
            approval = app.state.parse_rule_reviews.approve(run_id)
            if managed_config.is_file():
                app.state.job_controller.start_pass2(
                    run_id,
                    approval=approval,
                    credentials=credentials,
                    offline_executor=app.state.offline_inference,
                )
        except (KeyError, ValueError) as exc:
            return _render_parse_rule_editor(
                request,
                app,
                run_id,
                message=str(exc),
                status_code=422,
            )
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> RedirectResponse:
        """Cancel an owned active worker and return to its detail screen."""

        try:
            app.state.job_controller.cancel(run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/delete")
    async def delete_inactive_run(run_id: str) -> RedirectResponse:
        """Delete inactive local metadata/inputs while preserving user outputs."""

        try:
            app.state.run_store.delete_inactive(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except InvalidRunTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        app.state.inputs.discard_run(run_id)
        return RedirectResponse("/history", status_code=303)

    @app.post("/history/delete-all")
    async def delete_all_history() -> RedirectResponse:
        """Delete all inactive local run records while preserving user outputs."""

        run_ids = app.state.run_store.delete_all_inactive()
        for run_id in run_ids:
            app.state.inputs.discard_run(run_id)
        return RedirectResponse("/history", status_code=303)

    @app.post("/runs/{run_id}/resume")
    async def resume_run(request: Request, run_id: str) -> HTMLResponse:
        """Resume only the phase justified by the run's durable metadata."""

        try:
            run = app.state.run_store.get_run(run_id)
            config = app.state.job_controller.load_inference_config(run_id)
        except (KeyError, OSError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        subscription = config.auth.mode is AuthMode.SUBSCRIPTION
        credentials, missing = (
            ((), ())
            if subscription
            else _resolve_api_credentials(config, app.state.credential_vault)
        )
        if missing:
            provider = missing[0]
            if run.status in {RunStatus.INTERRUPTED, RunStatus.FAILED}:
                if run.status is RunStatus.FAILED and run.resume_phase is None:
                    app.state.job_controller.prepare_failed_retry(run_id)
                app.state.run_store.resume(run_id, credentials_available=False)
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="credential_required.html",
                context={
                    "run_id": run_id,
                    "provider": provider.value,
                    "continue_action": f"/runs/{run_id}/resume",
                },
                status_code=409,
            )
        try:
            app.state.job_controller.resume_inference(
                run_id,
                credentials=credentials,
                offline_executor=app.state.offline_inference,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str) -> StreamingResponse:
        """Replay and tail persisted events using resumable server-sent events."""

        try:
            app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        last_header = request.query_params.get(
            "after",
            request.headers.get("last-event-id", "0"),
        )
        try:
            last_sequence = max(0, int(last_header))
        except ValueError:
            last_sequence = 0

        async def stream() -> object:
            nonlocal last_sequence
            while True:
                events = app.state.run_store.list_events(run_id)
                new_events = [
                    event
                    for event in events
                    if int(event.get("sequence", 0)) > last_sequence
                ]
                for event in new_events:
                    last_sequence = int(event["sequence"])
                    yield _sse_event(event)
                current_run = app.state.run_store.get_run(run_id)
                if not _run_is_active(current_run, events):
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _container_output_directory(value: str) -> Path:
    """Map dashboard output paths onto directories mounted by Compose."""

    submitted = Path(value).expanduser()
    if not submitted.is_absolute():
        parts = submitted.parts
        if parts and parts[0] == "outputs":
            parts = parts[1:]
        candidate = _CONTAINER_OUTPUT_ROOT.joinpath(*parts)
    elif submitted.is_relative_to(_CONTAINER_OUTPUT_ROOT) or submitted.is_relative_to(
        _CONTAINER_DATA_ROOT
    ):
        candidate = submitted
    elif "outputs" in submitted.parts:
        outputs_index = (
            len(submitted.parts) - 1 - submitted.parts[::-1].index("outputs")
        )
        candidate = _CONTAINER_OUTPUT_ROOT.joinpath(
            *submitted.parts[outputs_index + 1 :]
        )
    else:
        raise FormFieldError(
            "output_directory",
            "Use outputs/ or /data/ for Docker output files.",
        )

    resolved = candidate.resolve()
    if not (
        resolved.is_relative_to(_CONTAINER_OUTPUT_ROOT)
        or resolved.is_relative_to(_CONTAINER_DATA_ROOT)
    ):
        raise FormFieldError(
            "output_directory",
            "Use outputs/ or /data/ for Docker output files.",
        )
    return resolved


def _validation_errors(exc: ValidationError | ValueError) -> list[dict[str, str]]:
    """Return safe, user-facing validation details without submitted values."""

    if isinstance(exc, FormFieldError):
        return [
            {
                "key": exc.field,
                "field": exc.field.replace("_", " ").title(),
                "message": str(exc),
            }
        ]
    if not isinstance(exc, ValidationError):
        return [{"key": "configuration", "field": "Configuration", "message": str(exc)}]

    issues: list[dict[str, str]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "__root__"]
        raw_field = location[-1] if location else "configuration"
        field = raw_field.replace("_", " ").strip().title()
        message = str(error.get("msg", "Invalid value"))
        if message.lower().startswith("value error, "):
            message = message[len("value error, ") :]
        issues.append({"key": raw_field, "field": field, "message": message})
    return issues or [
        {
            "key": "configuration",
            "field": "Configuration",
            "message": "The submitted configuration could not be validated",
        }
    ]


def _preset_form_state(
    preset: PresetRecord,
    *,
    known_models: set[str],
    presets_root: Path | None = None,
) -> dict[str, list[str]]:
    """Translate a typed preset into browser-form values without secrets."""

    config = preset.config
    state: dict[str, list[str]] = {
        "output_directory": [str(config.output.directory)],
        "output_policy": ["overwrite" if config.runtime.overwrite else "resume"],
        "pipeline": [
            {"all": "complete", "1": "transcription", "2": "structure"}[
                config.pipeline.stage
            ]
        ],
        "auth_mode": [config.auth.mode.value],
        "stage1_provider": [
            (config.models.stage1 or config.models.default).split("/", maxsplit=1)[0]
        ],
        "stage2_provider": [
            (config.models.stage2_pass1 or config.models.default).split(
                "/", maxsplit=1
            )[0]
        ],
        "batch_size": [str(config.runtime.batch_size)],
        "stage1_reasoning": [config.models.stage1_reasoning],
        "stage2_pass1_reasoning": [
            config.models.stage2_pass1_reasoning or config.models.stage2_reasoning
        ],
        "stage2_pass2_reasoning": [
            config.models.stage2_pass2_reasoning or config.models.stage2_reasoning
        ],
        "agentic": [
            "true" if config.agentic.stage1 or config.agentic.stage2 else "false"
        ],
        "verify_stage1": ["true"] if config.agentic.stage1 else [],
        "verify_stage2": ["true"] if config.agentic.stage2 else [],
        "max_iterations": [str(config.agentic.max_iterations)],
        "min_retry_confidence": [str(config.agentic.min_retry_confidence)],
        "verifier_patches": [str(config.agentic.verifier_patches).lower()],
        "require_concrete_retry": [str(config.agentic.require_concrete_retry).lower()],
        "mdf_manual_source": [
            "upload" if config.input.toolbox_pdf is not None else "none"
        ],
    }

    assets = (
        _preset_asset_paths(preset, presets_root=presets_root)
        if presets_root is not None
        else {}
    )

    def put(name: str, value: object | None) -> None:
        if value is not None and str(value) != "":
            state[name] = [str(value)]

    def put_model(field: str, custom_field: str, model: str | None) -> None:
        if model is None:
            return
        if config.auth.mode is AuthMode.SUBSCRIPTION or model in known_models:
            put(field, model)
        else:
            put(field, "__other__")
            put(custom_field, model)

    def put_agentic_model(role: str, model: str | None) -> None:
        if model is None:
            return
        prefix = model.split("/", 1)[0]
        provider = (
            prefix if prefix in {item.value for item in Provider} else preset.provider
        )
        put(f"{role}_provider", provider)
        put_model(f"{role}_model", f"{role}_custom_model", model)

    def managed_guide(path: Path | None, key: str) -> Path | None:
        if presets_root is None:
            return path if path is not None else None
        return assets.get(key)

    def put_instruction_state(
        stage: str,
        path: Path | None,
        *,
        page_spec: str | None,
        scope: str | None,
    ) -> None:
        asset_key = f"{stage}-instruction"
        managed = managed_guide(path, asset_key)
        metadata = read_managed_instruction_metadata(managed)
        text = _read_preset_text(managed)
        source_field = f"{stage}_instruction_source"
        pages_field = f"{stage}_instruction_pdf_pages"
        keep_field = f"{stage}_instruction_keep_existing"
        text_field = f"{stage}_additional_instructions"
        if metadata and metadata.get("source_mode") == "file":
            put(source_field, "file")
            put(pages_field, page_spec)
            put(keep_field, "true")
            return
        if managed is not None and text is not None:
            # Sidecar-free legacy TXT/MD/DOCX guides retain typed semantics.
            put(source_field, "typed")
            put(text_field, text)
            return
        if metadata and metadata.get("source_mode") == "typed":
            put(source_field, "typed")
            put(pages_field, page_spec)
            return
        if path is not None and managed is None:
            put(source_field, "typed")
        put(pages_field, page_spec)

    put("dictionary_pages", config.input.dictionary_pages)
    put("introduction_pages", config.input.introduction_pages)
    put("openrouter_provider", config.models.openrouter_provider)
    put_model(
        "stage1_model",
        "stage1_custom_model",
        config.models.stage1 or config.models.default,
    )
    put_model(
        "stage2_pass1_model",
        "stage2_pass1_custom_model",
        config.models.stage2_pass1 or config.models.default,
    )
    put_model(
        "stage2_pass2_model",
        "stage2_pass2_custom_model",
        config.models.stage2_pass2 or config.models.default,
    )
    put_agentic_model("evaluator", config.agentic.evaluator_model)
    put_agentic_model("rewriter", config.agentic.rewriter_model)
    put("evaluator_reasoning", config.agentic.evaluator_reasoning)
    put("rewriter_reasoning", config.agentic.rewriter_reasoning)
    if config.pipeline.parse_rules_pages:
        put("parse_rules_pages", ",".join(config.pipeline.parse_rules_pages))
    put(
        "character_inventory",
        _read_preset_text(managed_guide(config.input.alphabet, "character-inventory")),
    )
    put_instruction_state(
        "stage1",
        config.pipeline.stage1_guides,
        page_spec=config.pipeline.stage1_guides_pages,
        scope=None,
    )
    put_instruction_state(
        "stage2",
        config.pipeline.stage2_guides,
        page_spec=config.pipeline.stage2_guides_pages,
        scope=config.pipeline.stage2_guides_scope,
    )
    put("stage2_instruction_scope", config.pipeline.stage2_guides_scope)

    profile = config.input.dictionary_profile
    if profile is not None:
        put("profile_headword_language", profile.headword.language)
        put("profile_headword_script", profile.headword.script)
        state["profile_target_languages"] = [item.language for item in profile.targets]
        state["profile_target_scripts"] = [item.script for item in profile.targets]
        put("profile_page_layout", profile.page_layout)
        state["profile_information_types"] = [
            item.value if hasattr(item, "value") else str(item)
            for item in profile.information_types
        ]
        put("profile_other_information_types", profile.other_information_types)
    return state


_RESUMABLE_REVIEW_PHASES = frozenset(
    {"stage1", "stage2_pass1", "parse_rule_review", "stage2_pass2"}
)


def _review_continuation(run: RunRecord) -> tuple[str, str]:
    """Choose the safe continuation for a prepared run review."""

    if (
        run.status in {RunStatus.INTERRUPTED, RunStatus.CREDENTIALS_REQUIRED}
        and run.resume_phase in _RESUMABLE_REVIEW_PHASES
    ):
        return f"/runs/{run.run_id}/resume", "Resume run"
    return f"/runs/{run.run_id}/start", "Start run"


def _read_preset_text(path: Path | None) -> str | None:
    """Read and validate one sidecar-free legacy text guide."""

    if (
        path is None
        or path.is_symlink()
        or not path.is_file()
        or path.suffix.lower() not in {".txt", ".md", ".docx"}
    ):
        return None
    try:
        return read_instruction_text(path)
    except Exception:
        return None


def _active_config_models(config: InferenceConfig) -> tuple[str, ...]:
    """Return every model route used by the selected pipeline and verifier."""

    models: list[str] = []
    if config.pipeline.stage in {"1", "all"}:
        models.append(config.models.stage1 or config.models.default)
    if config.pipeline.stage in {"2", "all", "2-pass-1", "2-pass-2"}:
        if config.pipeline.stage != "2-pass-2":
            models.append(config.models.stage2_pass1 or config.models.default)
        if config.pipeline.stage not in {"2-pass-1"}:
            models.append(config.models.stage2_pass2 or config.models.default)
    if config.agentic.stage1 or config.agentic.stage2:
        models.extend(
            model
            for model in (
                config.agentic.evaluator_model,
                config.agentic.rewriter_model,
            )
            if model is not None
        )
    return tuple(dict.fromkeys(models))


def _api_providers_for_config(config: InferenceConfig) -> tuple[Provider, ...]:
    """Return API-key providers required by active model routes."""

    providers: list[Provider] = []
    for model in _active_config_models(config):
        prefix = model.split("/", maxsplit=1)[0]
        try:
            provider = Provider(prefix)
        except ValueError:
            provider = Provider.CUSTOM
        if provider is not Provider.CUSTOM and provider not in providers:
            providers.append(provider)
    return tuple(providers)


def _primary_provider_for_config(config: InferenceConfig) -> Provider:
    providers = _api_providers_for_config(config)
    if providers:
        return providers[0]
    try:
        return Provider(config.models.default.split("/", maxsplit=1)[0])
    except ValueError:
        return Provider.CUSTOM


def _resolve_api_credentials(
    config: InferenceConfig,
    vault: CredentialVault,
) -> tuple[tuple[ResolvedCredential, ...], tuple[Provider, ...]]:
    credentials: list[ResolvedCredential] = []
    missing: list[Provider] = []
    for provider in _api_providers_for_config(config):
        credential = vault.resolve(provider)
        if credential is None:
            missing.append(provider)
        else:
            credentials.append(credential)
    return tuple(credentials), tuple(missing)


def _config_summary(config: InferenceConfig) -> dict[str, object]:
    """Build the same metadata-only Review summary for every route."""

    verified_stages = [
        label
        for enabled, label in (
            (config.agentic.stage1, "Stage 1"),
            (config.agentic.stage2, "Stage 2"),
        )
        if enabled
    ]
    manual = config.input.toolbox_pdf
    runs_stage1 = config.pipeline.stage in {"1", "all"}
    runs_stage2 = config.pipeline.stage in {
        "2",
        "all",
        "2-pass-1",
        "2-pass-2",
    }
    parse_rule_pages = "Not used"
    if runs_stage2:
        parse_rule_pages = (
            ", ".join(config.pipeline.parse_rules_pages)
            if config.pipeline.parse_rules_pages
            else "Automatic selection"
        )
    stage1_summary = (
        (config.models.stage1 or config.models.default) if runs_stage1 else "Not used"
    )
    pass1_summary = (
        (config.models.stage2_pass1 or config.models.default)
        if runs_stage2
        else "Not used"
    )
    pass2_summary = (
        (config.models.stage2_pass2 or config.models.default)
        if runs_stage2
        else "Not used"
    )
    billing_mode = config.auth.mode.value
    auth_providers = (
        ", ".join(provider.value for provider in config.auth.providers) or None
    )
    return {
        "input": str(config.input.pages),
        "output": str(config.output.directory),
        "auth_mode": billing_mode,
        "auth_providers": auth_providers or "None",
        "billing_mode": billing_mode,
        "billing": "subscription" if billing_mode == "subscription" else "api_key",
        "billing_label": (
            "Subscription billing"
            if billing_mode == "subscription"
            else "API-key billing"
        ),
        "pipeline": str(config.pipeline.stage),
        "dictionary_pages": config.input.dictionary_pages or "All provided pages",
        "parse_rule_pages": parse_rule_pages,
        "stage_1_model": stage1_summary,
        "stage_2_pass_1_model": pass1_summary,
        "stage_2_pass_2_model": pass2_summary,
        "agentic": " + ".join(verified_stages) if verified_stages else "Off",
        "stage_1_instructions": instruction_review_summary(
            config.pipeline.stage1_guides,
            page_spec=(config.pipeline.stage1_guides_pages if runs_stage1 else None),
            stage2_scope=None,
        ),
        "stage_2_instructions": instruction_review_summary(
            config.pipeline.stage2_guides,
            page_spec=(config.pipeline.stage2_guides_pages if runs_stage2 else None),
            stage2_scope=(config.pipeline.stage2_guides_scope if runs_stage2 else None),
        ),
        "mdf_parsing_guide": (
            "Human approval required"
            if config.pipeline.stage != "1" and config.pipeline.parse_rules_file is None
            else (
                "Uploaded guide used directly"
                if config.pipeline.parse_rules_file is not None
                else "Not used"
            )
        ),
        "mdf_manual": ("Not used" if manual is None else "Custom upload"),
    }


def _read_log_tail(path: Path, *, redactions: tuple[str, ...]) -> tuple[str, bool]:
    if not path.is_file() or path.is_symlink():
        return "", False
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > _MAX_LOG_BYTES:
            stream.seek(-_MAX_LOG_BYTES, 2)
        raw = stream.read(_MAX_LOG_BYTES)
    content = raw.decode("utf-8", errors="replace")
    for secret in redactions:
        content = content.replace(secret, "[REDACTED]")
    return content, size > _MAX_LOG_BYTES


def _preset_asset_paths(
    preset: PresetRecord,
    *,
    presets_root: Path,
) -> dict[str, Path]:
    """Return preset-configured files that remain inside its managed bundle."""

    root = (presets_root / preset.preset_id / "inputs").resolve()

    def owned_path(value: Path) -> Path | None:
        if value.is_symlink():
            return None
        resolved = value.expanduser().resolve()
        if not resolved.is_relative_to(root):
            return None
        relative = resolved.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        return resolved

    def owned_file(value: Path | None) -> Path | None:
        if value is None:
            return None
        resolved = owned_path(value)
        if resolved is None or not resolved.is_file():
            return None
        return resolved

    assets: dict[str, Path] = {}
    pages = preset.config.input.pages
    page_directory = owned_path(pages) if pages is not None else None
    if page_directory is not None and page_directory.is_dir():
        page_files = sorted(
            (
                path
                for path in page_directory.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.relative_to(page_directory).as_posix(),
        )
    else:
        page_files = [pages] if pages is not None else []
    for index, page in enumerate(page_files):
        owned = owned_file(page)
        if owned is not None:
            assets[f"pages/{index}"] = owned

    guide = owned_file(preset.config.pipeline.parse_rules_file)
    if guide is not None:
        assets["mdf-guide"] = guide
    manual = owned_file(preset.config.input.toolbox_pdf)
    if manual is not None:
        assets["mdf-manual"] = manual
    alphabet = owned_file(preset.config.input.alphabet)
    if alphabet is not None:
        assets["character-inventory"] = alphabet
    for stage, guide_path in (
        ("stage1", preset.config.pipeline.stage1_guides),
        ("stage2", preset.config.pipeline.stage2_guides),
    ):
        guide = owned_file(guide_path)
        if guide is not None:
            assets[f"{stage}-instruction"] = guide
    return assets


def _preset_asset_links(
    preset: PresetRecord,
    *,
    presets_root: Path,
) -> dict[str, object]:
    """Build template-safe labels and local URLs for saved preset inputs."""

    paths = _preset_asset_paths(preset, presets_root=presets_root)

    def link_for(key: str) -> dict[str, object] | None:
        path = paths.get(key)
        if path is None:
            return None
        metadata = read_managed_instruction_metadata(path)
        suffix = path.suffix.lower()
        return {
            "name": (
                str(metadata["original_filename"])
                if metadata and metadata.get("original_filename")
                else path.name
            ),
            "url": f"/presets/{preset.preset_id}/files/{key}",
            "source_mode": metadata.get("source_mode") if metadata else "typed",
            "kind": (
                metadata.get("kind")
                if metadata
                else ("pdf" if suffix == ".pdf" else "text")
            ),
            "pdf_page_count": metadata.get("pdf_page_count") if metadata else None,
            "selected_pages": metadata.get("selected_pages", []) if metadata else [],
            "stage2_scope": metadata.get("stage2_scope") if metadata else None,
        }

    stage1_instruction = link_for("stage1-instruction")
    stage2_instruction = link_for("stage2-instruction")
    return {
        "pages": [
            {
                "name": path.name,
                "url": f"/presets/{preset.preset_id}/files/{key}",
            }
            for key, path in paths.items()
            if key.startswith("pages/")
        ],
        "mdf_guide": (
            {
                "name": paths["mdf-guide"].name,
                "url": f"/presets/{preset.preset_id}/files/mdf-guide",
            }
            if "mdf-guide" in paths
            else None
        ),
        "mdf_manual": (
            {
                "name": paths["mdf-manual"].name,
                "url": f"/presets/{preset.preset_id}/files/mdf-manual",
            }
            if "mdf-manual" in paths
            else None
        ),
        "stage1_instruction": stage1_instruction,
        "stage2_instruction": stage2_instruction,
        "instructions": {
            "stage1": stage1_instruction,
            "stage2": stage2_instruction,
        },
    }


def _all_models(app: FastAPI) -> tuple[object, ...]:
    models = app.state.model_catalog.options
    seen: set[str] = set()
    unique: list[object] = []
    for model in models:
        if model.model_id in seen:
            continue
        seen.add(model.model_id)
        unique.append(model)
    return tuple(unique)


def _history_output_directory(controller: JobController, run_id: str) -> str:
    """Return a prepared run's output directory without breaking stale history."""

    try:
        config = controller.load_inference_config(run_id)
    except (KeyError, OSError, ValidationError):
        return "Unavailable"
    return str(config.output.directory)


def _managed_config_available(controller: JobController, run_id: str) -> bool:
    """Return whether config-dependent run workspace views can load safely."""

    try:
        controller.load_inference_config(run_id)
    except (KeyError, OSError, ValidationError, ValueError):
        return False
    return True


def _run_is_active(
    run: RunRecord,
    events: list[dict[str, object]],
) -> bool:
    """Ignore terminal events from attempts older than the current worker."""

    if run.status not in _LIVE_RUN_STATUSES:
        return False
    if run.status is RunStatus.QUEUED:
        return True
    latest_terminal = max(
        (
            int(event.get("sequence", 0))
            for event in events
            if str(event.get("type", "")) in _TERMINAL_EVENT_TYPES
        ),
        default=0,
    )
    latest_start = max(
        (
            int(event.get("sequence", 0))
            for event in events
            if event.get("type") == "stage.started"
        ),
        default=0,
    )
    return latest_start > latest_terminal or latest_terminal == 0


def _run_view(store: RunStore, run: RunRecord) -> dict[str, object]:
    events = store.list_events(run.run_id)
    is_active = _run_is_active(run, events)
    for event in events:
        event["display_type"] = _event_label(
            str(event.get("type", "")), str(event.get("stage", ""))
        )
    active_stage = {
        RunStatus.RUNNING_STAGE1: "stage1",
        RunStatus.DISCOVERING_PARSE_RULES: "stage2_pass1",
        RunStatus.AWAITING_PARSE_RULES_REVIEW: "stage2_pass1",
        RunStatus.RUNNING_STAGE2: "stage2_pass2",
    }.get(run.status)
    progress_stage = active_stage
    if progress_stage is None and run.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
        RunStatus.CREDENTIALS_REQUIRED,
    }:
        progress_stage = next(
            (
                str(event["stage"])
                for event in reversed(events)
                if event.get("type") == "stage.started" and event.get("stage")
            ),
            None,
        )
    stage_events = [event for event in events if event.get("stage") == progress_stage]
    completed_pages = sum(
        event.get("type") == "page.completed" for event in stage_events
    )
    started = next(
        (
            event
            for event in reversed(stage_events)
            if event.get("type") == "stage.started"
        ),
        {},
    )
    total_pages = int(started.get("total_pages") or completed_pages or 0)
    current_page = None
    if is_active:
        current_page = next(
            (
                int(event["page"])
                for event in reversed(stage_events)
                if event.get("type") == "page.started"
                and not any(
                    later.get("type") == "page.completed"
                    and later.get("stage") == progress_stage
                    and later.get("page") == event.get("page")
                    for later in events[events.index(event) + 1 :]
                )
            ),
            None,
        )
    try:
        review_row = store.get_parse_rule_review(run.run_id)
    except KeyError:
        review_row = None
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "status_label": _status_label(run.status),
        "provider": run.provider or "Not selected",
        "completed_pages": completed_pages,
        "total_pages": total_pages,
        "current_stage_label": (
            "Review parsing guide"
            if run.status is RunStatus.AWAITING_PARSE_RULES_REVIEW
            else _stage_label(active_stage)
        ),
        "current_page": current_page,
        "pipeline_steps": _pipeline_steps(events, run.status),
        "events": events,
        "last_event_sequence": max(
            (int(event.get("sequence", 0)) for event in events),
            default=0,
        ),
        "failure_message": _failure_message(events),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "is_active": is_active,
        "is_terminal": run.status
        in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
        "delete_available": can_delete_run(run.status),
        "review_available": review_row is not None,
        "review_status": review_row.get("status") if review_row else None,
        "resume_available": run.status
        in {
            RunStatus.INTERRUPTED,
            RunStatus.CREDENTIALS_REQUIRED,
            RunStatus.FAILED,
        },
        "resume_label": "Retry run" if run.status is RunStatus.FAILED else "Resume run",
    }


def _failure_message(events: list[dict[str, object]]) -> str | None:
    """Return the current attempt's persisted worker failure, if any."""

    latest_failure: tuple[int, str] | None = None
    latest_recovery = 0
    for event in events:
        sequence = int(event.get("sequence", 0))
        event_type = str(event.get("type", ""))
        if event_type == "run.failed" and event.get("message"):
            latest_failure = (sequence, str(event["message"]))
        elif event_type in {"stage.started", "run.completed", "run.cancelled"}:
            latest_recovery = max(latest_recovery, sequence)
    if latest_failure is None or latest_recovery > latest_failure[0]:
        return None
    return latest_failure[1]


def _sse_event(event: dict[str, object]) -> str:
    return (
        f"id: {event['sequence']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _status_label(status: RunStatus) -> str:
    """Translate internal compatibility states into dashboard terminology."""

    if status is RunStatus.DISCOVERING_PARSE_RULES:
        return "Inferring MDF Parsing Guide"
    if status is RunStatus.AWAITING_PARSE_RULES_REVIEW:
        return "Awaiting MDF Parsing Guide Review"
    return status.value.replace("_", " ").title()


def _stage_label(stage: str | None) -> str:
    return {
        "stage1": "Stage 1 — Transcription",
        "stage2_pass1": "MDF parsing guide discovery",
        "stage2_pass2": "Stage 2 — MDF conversion",
    }.get(stage, "Pipeline")


def _page_progress_detail(completed: int, total: int) -> str:
    page_label = "page" if total == 1 else "pages"
    return f"{completed} of {total} {page_label} complete"


def _pipeline_steps(
    events: list[dict[str, object]], status: RunStatus
) -> list[dict[str, str]]:
    """Build the fixed pipeline timeline from durable worker events."""

    started_stages = {
        str(event.get("stage"))
        for event in events
        if event.get("type") == "stage.started"
    }
    completed_by_stage = {
        str(event.get("stage")): sum(
            item.get("type") == "page.completed"
            and item.get("stage") == event.get("stage")
            for item in events
        )
        for event in events
        if event.get("type") == "stage.started"
    }
    totals = {
        str(event.get("stage")): max(
            int(event.get("total_pages") or 0),
            completed_by_stage.get(str(event.get("stage")), 0),
        )
        for event in events
        if event.get("type") == "stage.started"
    }
    guide_ready = any(event.get("type") == "parse_rules.generated" for event in events)
    stage1_done = (
        "stage2_pass1" in started_stages
        or "stage2_pass2" in started_stages
        or (
            totals.get("stage1", 0) > 0
            and completed_by_stage.get("stage1", 0) >= totals["stage1"]
        )
    )
    stage1_completed = (
        totals.get("stage1", 0) if stage1_done else completed_by_stage.get("stage1", 0)
    )
    stage2_done = status is RunStatus.COMPLETED and "stage2_pass2" in started_stages
    stage2_completed = (
        totals.get("stage2_pass2", 0)
        if stage2_done
        else completed_by_stage.get("stage2_pass2", 0)
    )
    return [
        {
            "label": "Stage 1 — Transcription",
            "state": "completed"
            if stage1_done
            else "running"
            if status is RunStatus.RUNNING_STAGE1
            else "pending",
            "detail": (
                _page_progress_detail(stage1_completed, totals.get("stage1", 0))
                if "stage1" in started_stages
                else ""
            ),
        },
        {
            "label": "MDF parsing guide discovery",
            "state": "completed"
            if guide_ready
            else "running"
            if status is RunStatus.DISCOVERING_PARSE_RULES
            else "pending",
            "detail": (
                "Guide ready for review"
                if guide_ready
                else (
                    "Inferring guide from representative pages"
                    if status is RunStatus.DISCOVERING_PARSE_RULES
                    else "Starts after Stage 1 is complete"
                )
            ),
        },
        {
            "label": "Review parsing guide",
            "state": "completed"
            if "stage2_pass2" in started_stages
            else "running"
            if status is RunStatus.AWAITING_PARSE_RULES_REVIEW
            else "pending",
            "detail": "Approval is required before MDF conversion"
            if status is RunStatus.AWAITING_PARSE_RULES_REVIEW
            else "",
        },
        {
            "label": "Stage 2 — MDF conversion",
            "state": "completed"
            if stage2_done
            else "running"
            if status is RunStatus.RUNNING_STAGE2
            else "pending",
            "detail": (
                _page_progress_detail(
                    stage2_completed,
                    totals.get("stage2_pass2", 0),
                )
                if "stage2_pass2" in started_stages
                else "Starts after guide approval"
            ),
        },
    ]


def _event_label(event_type: str, stage: str) -> str:
    labels = {
        "parse_rules.generated": "MDF parsing guide inferred",
        "stage.started": f"{_stage_label(stage)} started",
        "page.started": f"{_stage_label(stage)} page started",
        "page.completed": f"{_stage_label(stage)} page completed",
        "run.completed": "Run completed",
        "run.failed": "Run failed",
    }
    return labels.get(
        event_type,
        event_type.replace("_", " ").replace(".", " · ").title(),
    )


def _render_parse_rule_editor(
    request: Request,
    app: FastAPI,
    run_id: str,
    *,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    try:
        review = app.state.parse_rule_reviews.get(run_id)
        payload = app.state.parse_rule_reviews.load_editable_payload(run_id)
        run = app.state.run_store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="MDF parsing guide review not found"
        ) from exc
    markers = payload.get("markers") if isinstance(payload.get("markers"), list) else []
    rules = payload.get("rules") if isinstance(payload.get("rules"), list) else []
    abbreviations_raw = payload.get("abbreviations")
    abbreviations = (
        list(abbreviations_raw.items()) if isinstance(abbreviations_raw, dict) else []
    )
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="parse_rules.html",
        context={
            "run_id": run_id,
            "run": run,
            "review": review,
            "markers": markers or [{"marker": "", "description": ""}],
            "rules": rules or [""],
            "abbreviations": abbreviations or [("", "")],
            "message": message,
        },
        status_code=status_code,
    )


def _parse_rule_form(form: object) -> dict[str, object]:
    getlist = getattr(form, "getlist")
    codes = [str(value) for value in getlist("marker_code")]
    descriptions = [str(value) for value in getlist("marker_description")]
    if len(codes) != len(descriptions):
        raise ValueError("marker codes and descriptions must be paired")
    abbreviation_keys = [str(value) for value in getlist("abbreviation_key")]
    abbreviation_values = [str(value) for value in getlist("abbreviation_value")]
    if len(abbreviation_keys) != len(abbreviation_values):
        raise ValueError("abbreviation names and meanings must be paired")
    abbreviations: dict[str, str] = {}
    for key, value in zip(abbreviation_keys, abbreviation_values, strict=True):
        if bool(key.strip()) != bool(value.strip()):
            raise ValueError("each abbreviation requires both a name and meaning")
        if key.strip():
            abbreviations[key.strip()] = value.strip()
    return {
        "markers": [
            {"marker": code, "description": description}
            for code, description in zip(codes, descriptions, strict=True)
        ],
        "rules": [str(value) for value in getlist("rule") if str(value).strip()],
        "abbreviations": abbreviations,
    }


def _origin_matches_host(origin: str, host: str) -> bool:
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == host


def _add_security_headers(response: object) -> object:
    headers = getattr(response, "headers")
    allow_same_origin_frame = headers.get(_ALLOW_SAME_ORIGIN_FRAME_HEADER)
    if allow_same_origin_frame:
        del headers[_ALLOW_SAME_ORIGIN_FRAME_HEADER]
    headers["Content-Security-Policy"] = (
        _CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        if allow_same_origin_frame
        else _CSP
    )
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    headers["X-Frame-Options"] = "SAMEORIGIN" if allow_same_origin_frame else "DENY"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
