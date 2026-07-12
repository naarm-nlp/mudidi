"""FastAPI application factory for the local MUDIDI website."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mudidi.config.yaml_config import validate_config_paths
from mudidi.web.credentials import CredentialVault
from mudidi.web.artifacts import ArtifactAccessError, ArtifactService
from mudidi.web.forms import NewRunForm
from mudidi.web.jobs import JobController
from mudidi.web.models import ModelCatalog, Provider
from mudidi.web.parse_rules import ParseRuleReviewService
from mudidi.web.runs import RunRecord, RunStatus, RunStore

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_PACKAGE_DIR / "templates")
_MAX_REQUEST_BYTES = 25 * 1024 * 1024
_MAX_LOG_BYTES = 512_000
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


def create_app(
    *,
    data_dir: Path | None = None,
    credential_vault: CredentialVault | None = None,
    offline_inference: bool = False,
) -> FastAPI:
    """Create a loopback-oriented application without starting a server.

    Args:
        data_dir: Directory reserved for web metadata and managed uploads. The
            directory is created eagerly so startup fails before serving when
            it is not writable.

    Returns:
        Configured FastAPI application.
    """

    resolved_data_dir = (data_dir or Path.home() / ".local/share/mudidi").resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="MUDIDI Local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.data_dir = resolved_data_dir
    app.state.credential_vault = credential_vault or CredentialVault()
    app.state.model_catalog = ModelCatalog.bundled()
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
    app.state.job_controller.reconcile_startup()
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

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
            if declared_length > _MAX_REQUEST_BYTES:
                response = PlainTextResponse("Request body too large", status_code=413)
                return _add_security_headers(response)
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
        response = await call_next(request)  # type: ignore[operator]
        return _add_security_headers(response)

    app.mount(
        "/static",
        StaticFiles(directory=_PACKAGE_DIR / "static"),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """Render the local production-inference workspace."""

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "active_page": "new-run",
                "models": app.state.model_catalog.options,
            },
        )

    @app.get("/healthz")
    async def health() -> dict[str, str | int]:
        """Return a stable, non-secret liveness response."""

        return {"status": "ok", "protocol_version": 1}

    @app.post("/runs/preview", response_class=HTMLResponse)
    async def preview_run(request: Request) -> HTMLResponse:
        """Validate browser form state and render a non-secret run review."""

        submitted = await request.form()
        payload = {key: value for key, value in submitted.items()}
        parse_rule_pages = [
            str(value) for value in submitted.getlist("parse_rules_pages")
        ]
        if parse_rule_pages:
            payload["parse_rules_pages"] = parse_rule_pages
        try:
            run_form = NewRunForm.model_validate(payload)
            config = run_form.to_inference_config()
            validate_config_paths(config)
        except (ValidationError, ValueError) as exc:
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="form_error.html",
                context={"error_count": _error_count(exc)},
                status_code=422,
            )
        run_id = f"run-{uuid4().hex[:12]}"
        app.state.job_controller.prepare_inference(
            run_id,
            config=config,
            provider=Provider(run_form.provider),
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="review.html",
            context={"summary": run_form.to_summary(), "run_id": run_id},
        )

    @app.post("/runs/{run_id}/start")
    async def start_prepared_run(request: Request, run_id: str) -> HTMLResponse:
        """Start a validated production run after resolving its provider key."""

        try:
            run = app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        try:
            provider = Provider(str(run.provider))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid run provider") from exc
        credential = app.state.credential_vault.resolve(provider)
        if credential is None and provider is not Provider.CUSTOM:
            if run.status is RunStatus.VALIDATED:
                app.state.run_store.transition(run_id, RunStatus.CREDENTIALS_REQUIRED)
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="credential_required.html",
                context={"run_id": run_id, "provider": provider.value},
                status_code=409,
            )
        try:
            app.state.job_controller.start_inference(
                run_id,
                credential=credential,
                offline_executor=app.state.offline_inference,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/providers", response_class=HTMLResponse)
    async def providers(request: Request) -> HTMLResponse:
        """Render provider key availability and the bundled model fallback."""

        return _render_providers(request, app)

    @app.post("/providers/{provider_name}/credential", response_class=HTMLResponse)
    async def set_provider_credential(
        request: Request,
        provider_name: str,
    ) -> HTMLResponse:
        """Store one temporary provider credential in process memory."""

        try:
            provider = Provider(provider_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="unknown provider") from exc
        submitted = await request.form()
        api_key = str(submitted.get("api_key", ""))
        try:
            app.state.credential_vault.set_temporary(provider, api_key)
        except ValueError:
            return _render_providers(
                request,
                app,
                message="API key cannot be empty",
                status_code=422,
            )
        return _render_providers(
            request,
            app,
            message="Temporary key ready",
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

        active = app.state.run_store.list_active_runs()
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="active.html",
            context={"runs": [_run_view(app.state.run_store, run) for run in active]},
        )

    @app.get("/history", response_class=HTMLResponse)
    async def run_history(request: Request) -> HTMLResponse:
        """Render durable newest-first local run history."""

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "runs": [
                    _run_view(app.state.run_store, run)
                    for run in app.state.run_store.list_runs()
                ]
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> HTMLResponse:
        """Render current state and persisted event progress for one run."""

        try:
            run = app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={"run": _run_view(app.state.run_store, run)},
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
    async def run_pages(request: Request, run_id: str) -> HTMLResponse:
        """Render bounded Stage 1 and Stage 2 text grouped by source page."""

        try:
            pages = app.state.artifacts.list_pages(run_id)
            page_views = [
                {
                    "page_id": page.page_id,
                    "stage1": (
                        app.state.artifacts.preview_text(
                            run_id, page.stage1.relative_path
                        )
                        if page.stage1
                        else None
                    ),
                    "stage2": (
                        app.state.artifacts.preview_text(
                            run_id, page.stage2.relative_path
                        )
                        if page.stage2
                        else None
                    ),
                }
                for page in pages
            ]
        except (ArtifactAccessError, KeyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run pages not found") from exc
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="pages.html",
            context={"run_id": run_id, "pages": page_views},
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
            log_path = app.state.job_controller.log_path(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        content, truncated = _read_log_tail(
            log_path,
            redactions=app.state.credential_vault.redaction_values(),
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="logs.html",
            context={"run_id": run_id, "content": content, "truncated": truncated},
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
            credential = None
            managed_config = app.state.job_controller.config_path(run_id)
            if managed_config.is_file():
                run = app.state.run_store.get_run(run_id)
                provider = Provider(str(run.provider))
                credential = app.state.credential_vault.resolve(provider)
                if credential is None and provider is not Provider.CUSTOM:
                    return _render_parse_rule_editor(
                        request,
                        app,
                        run_id,
                        message="API credential required before Pass 2 can start",
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
                    credential=credential,
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

    @app.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str) -> StreamingResponse:
        """Replay and tail persisted events using resumable server-sent events."""

        try:
            app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        last_header = request.headers.get("last-event-id", "0")
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
                status = app.state.run_store.get_run(run_id).status
                if status in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
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


def _error_count(exc: ValidationError | ValueError) -> int:
    if isinstance(exc, ValidationError):
        return len(exc.errors())
    return 1


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


def _render_providers(
    request: Request,
    app: FastAPI,
    *,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    direct_providers = (
        Provider.ANTHROPIC,
        Provider.OPENAI,
        Provider.GEMINI,
        Provider.OPENROUTER,
    )
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="providers.html",
        context={
            "providers": direct_providers,
            "statuses": {
                provider.value: app.state.credential_vault.status(provider)
                for provider in direct_providers
            },
            "models": app.state.model_catalog.options,
            "catalog_as_of": app.state.model_catalog.as_of,
            "message": message,
        },
        status_code=status_code,
    )


def _run_view(store: RunStore, run: RunRecord) -> dict[str, object]:
    events = store.list_events(run.run_id)
    completed_pages = sum(event.get("type") == "page.completed" for event in events)
    started = next(
        (event for event in events if event.get("type") == "stage.started"), {}
    )
    total_pages = int(started.get("total_pages") or completed_pages or 0)
    try:
        review_row = store.get_parse_rule_review(run.run_id)
    except KeyError:
        review_row = None
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "status_label": run.status.value.replace("_", " ").title(),
        "provider": run.provider or "Not selected",
        "completed_pages": completed_pages,
        "total_pages": total_pages,
        "events": events,
        "created_at": run.created_at,
        "is_active": run.status
        in {
            RunStatus.RUNNING_STAGE1,
            RunStatus.DISCOVERING_PARSE_RULES,
            RunStatus.RUNNING_STAGE2,
        },
        "review_available": review_row is not None,
        "review_status": review_row.get("status") if review_row else None,
    }


def _sse_event(event: dict[str, object]) -> str:
    return (
        f"id: {event['sequence']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
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
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="parse-rule review not found"
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
            "review": review,
            "dictionary_name": str(payload.get("dictionary_name", "")),
            "markers": markers or [{"marker": "", "description": ""}],
            "rules": rules or [""],
            "abbreviations": abbreviations or [("", "")],
            "message": message,
        },
        status_code=status_code,
    )


def _parse_rule_form(form: object) -> dict[str, object]:
    get = getattr(form, "get")
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
        "dictionary_name": str(get("dictionary_name", "")),
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
    headers["Content-Security-Policy"] = _CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    headers["X-Frame-Options"] = "DENY"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
