"""FastAPI application factory for the local MUDIDI website."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mudidi.config.yaml_config import validate_config_paths
from mudidi.web.forms import NewRunForm

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_PACKAGE_DIR / "templates")


def create_app(*, data_dir: Path | None = None) -> FastAPI:
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
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
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
            context={"active_page": "new-run"},
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
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="review.html",
            context={"summary": run_form.to_summary()},
        )

    return app


def _error_count(exc: ValidationError | ValueError) -> int:
    if isinstance(exc, ValidationError):
        return len(exc.errors())
    return 1
