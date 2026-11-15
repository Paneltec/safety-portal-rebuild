"""Application factory."""
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .routers import auth, documents, forms, org, presets, roles, safety, sessions, users, workers


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
    app.include_router(workers.router)
    app.include_router(workers.cert_router)
    app.include_router(workers.kinds_router)
    app.include_router(safety.swms_router)
    for register in safety.routers:
        app.include_router(register)
    return app


app = create_app()
