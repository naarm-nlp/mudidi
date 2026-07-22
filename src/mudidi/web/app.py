"""FastAPI application factory for the local MUDIDI website."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mudidi.config.yaml_config import InferenceConfig, validate_config_paths
from mudidi.web.credentials import CredentialVault, PersistentCredentialStore
from mudidi.web.artifacts import ArtifactAccessError, ArtifactService
from mudidi.web.forms import FormFieldError, NewRunForm
from mudidi.web.jobs import JobController
from mudidi.web.inputs import InputMaterializer, rebase_managed_config
from mudidi.web.models import (
    ModelCatalog,
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

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_PACKAGE_DIR / "templates")
_MAX_REQUEST_BYTES = 25 * 1024 * 1024
_MAX_LOG_BYTES = 512_000
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
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


class _RequestBodyTooLarge(Exception):
    """Internal control flow for streamed request-size enforcement."""


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


def create_app(
    *,
    data_dir: Path | None = None,
    credential_vault: CredentialVault | None = None,
    offline_inference: bool = False,
    model_discovery: ModelDiscovery | None = None,
    container_mode: bool = False,
) -> FastAPI:
    """Create a loopback-oriented application without starting a server.

    Args:
        data_dir: Directory reserved for web metadata and managed uploads. The
            directory is created eagerly so startup fails before serving when
            it is not writable.
        container_mode: Permit the local host aliases used to reach the app
            through Docker Desktop. Arbitrary host headers remain rejected.

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
    app.state.credential_vault = credential_vault or CredentialVault(
        environ={},
        persistent_store=PersistentCredentialStore(
            database_path=resolved_data_dir / "mudidi-web.sqlite3",
            key_path=resolved_data_dir / ".credential-key",
        ),
    )
    app.state.model_catalog = ModelCatalog.bundled()
    app.state.model_discovery = model_discovery or ModelDiscovery()
    app.state.live_models = {}
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
    app.state.inputs = InputMaterializer(data_dir=resolved_data_dir)
    app.state.job_controller.reconcile_startup()
    app.state.inputs.reconcile(
        {run.run_id for run in app.state.run_store.list_runs()}
    )
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
            if declared_length > _MAX_REQUEST_BYTES:
                response = PlainTextResponse("Request body too large", status_code=413)
                return _add_security_headers(response)
        received_bytes = 0
        original_receive = request._receive

        async def limited_receive() -> object:
            nonlocal received_bytes
            message = await original_receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > _MAX_REQUEST_BYTES:
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
            )
            preset_assets = _preset_asset_links(
                selected_preset,
                presets_root=app.state.inputs.presets_root,
            )
        errors = validation_errors or []
        return {
            "request": request,
            "active_page": "new-run",
            "models": _all_models(app),
            "presets": presets,
            "selected_preset": selected_preset,
            "preset_state": preset_state,
            "preset_assets": preset_assets,
            "credential_statuses": credential_statuses,
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
        }
        retired_dashboard_fields = {
            "page_limit",
            "media_reference",
            "prompt_cache",
        }
        payload = {
            key: value
            for key, value in submitted.items()
            if key not in upload_fields
            and key not in retired_dashboard_fields
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
                raise ValueError("dashboard input paths must be selected in the browser")
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
                raise ValueError("an MDF parsing guide requires an MDF parsing pipeline")
            if guide_files:
                payload["parse_rules_file"] = (
                    await app.state.inputs.materialize_mdf_guide(
                        run_id,
                        guide_files[0],
                        replace=preset_config is not None,
                    )
                )
            elif preset_config is not None and runs_stage2:
                payload["parse_rules_file"] = preset_config.pipeline.parse_rules_file

            stage1_instructions = str(
                payload.get("stage1_additional_instructions", "")
            ).strip()
            if stage1_instructions and runs_stage1:
                payload["stage1_guides"] = app.state.inputs.materialize_instruction(
                    run_id,
                    "stage1",
                    stage1_instructions,
                    replace=preset_config is not None,
                )
            elif preset_config is not None and runs_stage1:
                payload["stage1_guides"] = preset_config.pipeline.stage1_guides
            stage2_instructions = str(
                payload.get("stage2_additional_instructions", "")
            ).strip()
            if stage2_instructions and runs_stage2:
                payload["stage2_guides"] = app.state.inputs.materialize_instruction(
                    run_id,
                    "stage2",
                    stage2_instructions,
                    replace=preset_config is not None,
                )
            elif preset_config is not None and runs_stage2:
                payload["stage2_guides"] = preset_config.pipeline.stage2_guides

            manual_source = str(payload.get("mdf_manual_source", "none"))
            manual_files = uploaded("custom_mdf_manual")
            if not runs_stage2:
                if manual_files:
                    raise ValueError("an MDF manual requires an MDF parsing pipeline")
                manual_source = "none"
                payload["mdf_manual_source"] = "none"
            if manual_source == "upload":
                if len(manual_files) == 1:
                    payload["toolbox_pdf"] = (
                        await app.state.inputs.materialize_mdf_manual(
                            run_id,
                            manual_files[0],
                            replace=preset_config is not None,
                        )
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
                provider=Provider(run_form.provider),
            )
        except Exception:
            app.state.inputs.discard(run_id)
            raise
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
            raise HTTPException(status_code=422, detail="API key cannot be empty") from exc
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
        return JSONResponse(
            {"status": "deleted", "provider": provider.value},
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

        active = app.state.run_store.list_active_runs()
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="active.html",
            context={"runs": [_run_view(app.state.run_store, run) for run in active]},
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
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "runs": [_run_view(app.state.run_store, run) for run in runs],
                "filters": {"q": q, "status": status, "provider": provider},
                "statuses": tuple(RunStatus),
                "status_label": _status_label,
                "providers": tuple(
                    provider for provider in Provider if provider is not Provider.CUSTOM
                ),
                "has_deletable_runs": any(can_delete_run(run.status) for run in all_runs),
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
        return RedirectResponse("/presets", status_code=303)

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
                "is_active": run.status in _LIVE_RUN_STATUSES,
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
            page = next(
                item
                for item in pages
                if item.page_id == page_id
            )
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
                "page_urls": [
                    f"/runs/{run_id}/pages/{item.page_id}" for item in pages
                ],
                "page_labels": [item.page_id.removeprefix("page_") for item in pages],
                "previous_page": pages[page_index - 1] if page_index > 0 else None,
                "next_page": (
                    pages[page_index + 1] if page_index + 1 < len(pages) else None
                ),
                "saved": request.query_params.get("saved") == "1",
                "is_active": run.status in _LIVE_RUN_STATUSES,
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
            log_path = app.state.job_controller.log_path(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        content, truncated = _read_log_tail(
            log_path,
            redactions=app.state.credential_vault.redaction_values(),
        )
        failure_message = _failure_message(
            app.state.run_store.list_events(run_id)
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="logs.html",
            context={
                "run_id": run_id,
                "content": content,
                "truncated": truncated,
                "failure_message": failure_message,
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
            provider = Provider(str(run.provider))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        credential = app.state.credential_vault.resolve(provider)
        if credential is None and provider is not Provider.CUSTOM:
            if run.status is RunStatus.INTERRUPTED:
                app.state.run_store.resume(run_id, credentials_available=False)
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="credential_required.html",
                context={"run_id": run_id, "provider": provider.value},
                status_code=409,
            )
        try:
            app.state.job_controller.resume_inference(
                run_id,
                credential=credential,
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
        outputs_index = len(submitted.parts) - 1 - submitted.parts[::-1].index(
            "outputs"
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
        return [
            {"key": "configuration", "field": "Configuration", "message": str(exc)}
        ]

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
        "provider": [preset.provider],
        "temperature": [str(config.models.temperature)],
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
        "require_concrete_retry": [
            str(config.agentic.require_concrete_retry).lower()
        ],
        "mdf_manual_source": [
            "upload" if config.input.toolbox_pdf is not None else "none"
        ],
    }

    def put(name: str, value: object | None) -> None:
        if value is not None and str(value) != "":
            state[name] = [str(value)]

    def put_model(field: str, custom_field: str, model: str | None) -> None:
        if model is None:
            return
        if model in known_models:
            put(field, model)
        else:
            put(field, "__other__")
            put(custom_field, model)

    def put_agentic_model(role: str, model: str | None) -> None:
        if model is None:
            return
        prefix = model.split("/", 1)[0]
        provider = prefix if prefix in {item.value for item in Provider} else preset.provider
        put(f"{role}_provider", provider)
        put_model(f"{role}_model", f"{role}_custom_model", model)

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
    put("character_inventory", _read_preset_text(config.input.alphabet))
    put(
        "stage1_additional_instructions",
        _read_preset_text(config.pipeline.stage1_guides),
    )
    put(
        "stage2_additional_instructions",
        _read_preset_text(config.pipeline.stage2_guides),
    )

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


def _read_preset_text(path: Path | None) -> str | None:
    """Read a bounded UTF-8 text field from a validated preset bundle."""

    if path is None or not path.is_file() or path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8")[:20_000]
    except (OSError, UnicodeDecodeError):
        return None


def _config_summary(config: InferenceConfig) -> dict[str, str]:
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
        config.models.stage1 or config.models.default
    ) if runs_stage1 else "Not used"
    pass1_summary = (
        config.models.stage2_pass1 or config.models.default
    ) if runs_stage2 else "Not used"
    pass2_summary = (
        config.models.stage2_pass2 or config.models.default
    ) if runs_stage2 else "Not used"
    return {
        "input": str(config.input.pages),
        "output": str(config.output.directory),
        "pipeline": str(config.pipeline.stage),
        "dictionary_pages": config.input.dictionary_pages or "All provided pages",
        "parse_rule_pages": parse_rule_pages,
        "stage_1_model": stage1_summary,
        "stage_2_pass_1_model": pass1_summary,
        "stage_2_pass_2_model": pass2_summary,
        "agentic": " + ".join(verified_stages) if verified_stages else "Off",
        "additional_instructions": ", ".join(
            label
            for path, label in (
                (config.pipeline.stage1_guides, "Stage 1"),
                (config.pipeline.stage2_guides, "Stage 2"),
            )
            if path is not None
        )
        or "None",
        "mdf_manual": (
            "Not used"
            if manual is None
            else "Custom upload"
        ),
        "mdf_parsing_guide": (
            "Not used" if config.pipeline.stage == "1" else "Human approval required"
        ),
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
    return assets


def _preset_asset_links(
    preset: PresetRecord,
    *,
    presets_root: Path,
) -> dict[str, object]:
    """Build template-safe labels and local URLs for saved preset inputs."""

    paths = _preset_asset_paths(preset, presets_root=presets_root)
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
    }


def _all_models(app: FastAPI) -> tuple[object, ...]:
    live = tuple(model for models in app.state.live_models.values() for model in models)
    bundled_ids = {model.model_id for model in app.state.model_catalog.options}
    return app.state.model_catalog.options + tuple(
        model for model in live if model.model_id not in bundled_ids
    )


def _run_view(store: RunStore, run: RunRecord) -> dict[str, object]:
    events = store.list_events(run.run_id)
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
    stage_events = [event for event in events if event.get("stage") == active_stage]
    completed_pages = sum(
        event.get("type") == "page.completed" for event in stage_events
    )
    started = next(
        (event for event in reversed(stage_events) if event.get("type") == "stage.started"),
        {},
    )
    total_pages = int(started.get("total_pages") or completed_pages or 0)
    current_page = next(
        (
            int(event["page"])
            for event in reversed(stage_events)
            if event.get("type") == "page.started"
            and not any(
                later.get("type") == "page.completed"
                and later.get("stage") == active_stage
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
        "is_active": run.status in _LIVE_RUN_STATUSES,
        "is_terminal": run.status
        in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
        "delete_available": can_delete_run(run.status),
        "review_available": review_row is not None,
        "review_status": review_row.get("status") if review_row else None,
        "resume_available": run.status
        in {RunStatus.INTERRUPTED, RunStatus.CREDENTIALS_REQUIRED},
    }


def _failure_message(events: list[dict[str, object]]) -> str | None:
    """Return the latest persisted worker failure without exposing credentials."""

    for event in reversed(events):
        if event.get("type") == "run.failed" and event.get("message"):
            return str(event["message"])
    return None


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

    started_stages = {str(event.get("stage")) for event in events if event.get("type") == "stage.started"}
    completed_by_stage = {
        str(event.get("stage")): sum(
            item.get("type") == "page.completed" and item.get("stage") == event.get("stage")
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
    stage1_done = "stage2_pass1" in started_stages or "stage2_pass2" in started_stages or (
        totals.get("stage1", 0) > 0
        and completed_by_stage.get("stage1", 0) >= totals["stage1"]
    )
    stage1_completed = (
        totals.get("stage1", 0)
        if stage1_done
        else completed_by_stage.get("stage1", 0)
    )
    stage2_done = (
        status is RunStatus.COMPLETED and "stage2_pass2" in started_stages
    )
    stage2_completed = (
        totals.get("stage2_pass2", 0)
        if stage2_done
        else completed_by_stage.get("stage2_pass2", 0)
    )
    return [
        {
            "label": "Stage 1 — Transcription",
            "state": "completed" if stage1_done else "running" if status is RunStatus.RUNNING_STAGE1 else "pending",
            "detail": (
                _page_progress_detail(
                    stage1_completed, totals.get("stage1", 0)
                )
                if "stage1" in started_stages
                else ""
            ),
        },
        {
            "label": "MDF parsing guide discovery",
            "state": "completed" if guide_ready else "running" if status is RunStatus.DISCOVERING_PARSE_RULES else "pending",
            "detail": "Guide ready for review" if guide_ready else "Starts after Stage 1 is complete",
        },
        {
            "label": "Review parsing guide",
            "state": "completed" if "stage2_pass2" in started_stages else "running" if status is RunStatus.AWAITING_PARSE_RULES_REVIEW else "pending",
            "detail": "Approval is required before MDF conversion" if status is RunStatus.AWAITING_PARSE_RULES_REVIEW else "",
        },
        {
            "label": "Stage 2 — MDF conversion",
            "state": "completed" if stage2_done else "running" if status is RunStatus.RUNNING_STAGE2 else "pending",
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
