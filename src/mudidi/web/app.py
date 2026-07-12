"""FastAPI application factory for the local MUDIDI website."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mudidi.config.yaml_config import validate_config_paths
from mudidi.web.forms import NewRunForm
from mudidi.web.credentials import CredentialVault
from mudidi.web.models import ModelCatalog, Provider

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_PACKAGE_DIR / "templates")


def create_app(
    *,
    data_dir: Path | None = None,
    credential_vault: CredentialVault | None = None,
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

    return app


def _error_count(exc: ValidationError | ValueError) -> int:
    if isinstance(exc, ValidationError):
        return len(exc.errors())
    return 1


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
