"""Application factory."""
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .routers import auth, comms, documents, fleet, forms, fuel, imports, inductions, jobs, leave, org, presets, renewals, roles, safety, service, sessions, sites, suppliers, timesheets, users, workers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Safety Portal API",
        version=settings.app_version,
        description="Independent reconstruction of the Safety Portal. See docs/PROGRESS.md for what is built.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        # A request ID links audit entries and error reports without exposing secrets.
        rid = uuid.uuid4().hex
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - never leak internals to the client
            response = JSONResponse({"detail": "Something went wrong on the server", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    # Static paths are registered before parameterised ones inside each router.
    app.include_router(org.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(roles.router)
    app.include_router(presets.router)
    app.include_router(sessions.router)
    app.include_router(documents.router)
    app.include_router(forms.router)
    app.include_router(fuel.router)
    app.include_router(timesheets.access_router)
    app.include_router(timesheets.me_router)
    app.include_router(timesheets.router)
    app.include_router(leave.me_router)
    app.include_router(leave.router)
    app.include_router(service.router)  # before fleet.router: /service/... must not be read as an asset id
    app.include_router(service.presets_router)
    app.include_router(fleet.router)
    app.include_router(fleet.fleet_router)
    app.include_router(fleet.equipment_router)
    app.include_router(imports.router)
    app.include_router(comms.integrations_router)
    app.include_router(comms.safe_mode_router)
    app.include_router(comms.outbox_router)
    app.include_router(comms.notifications_router)
    app.include_router(inductions.matrix_router)  # before workers.router: /inductions must not be read as a worker id
    app.include_router(inductions.types_router)
    app.include_router(inductions.training_router)
    app.include_router(inductions.hr_router)
    app.include_router(jobs.router)
    app.include_router(workers.router)
    app.include_router(workers.cert_router)
    app.include_router(workers.kinds_router)
    app.include_router(sites.router)
    app.include_router(sites.scan_router)
    app.include_router(sites.public_router)
    app.include_router(sites.visitors_router)
    app.include_router(suppliers.router)
    app.include_router(suppliers.contractors_router)
    app.include_router(suppliers.doc_types_router)  # before renewals.router: /doc-types must not be read as an id
    app.include_router(renewals.router)
    app.include_router(renewals.public_router)
    app.include_router(safety.swms_router)
    for register in safety.routers:
        app.include_router(register)
    return app


app = create_app()
