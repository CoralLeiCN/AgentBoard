import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .runtime import Runtime


def frontend_directory():
    """Use editable source assets, falling back to the copy bundled in a wheel."""
    source = Path(__file__).resolve().parents[2] / "frontend"
    return source if (source / "index.html").is_file() else Path(__file__).parent / "static"


def _create_app(settings, runtime):

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            runtime.close()
    app = FastAPI(title="AgentBoard", version="0.1.0", description="Agent-neutral trace and session API", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.state.ingest_slots = runtime.ingest_slots
    store = runtime.store
    app.state.store, app.state.settings, app.state.runtime = store, settings, runtime
    if runtime.features & {"classification", "replay"}:
        from .models import ModelServiceError

        app.state.gateway = runtime.gateway()

        @app.exception_handler(ModelServiceError)
        async def model_error(request, exc):
            return JSONResponse({"detail": str(exc)}, 502)

    @app.middleware("http")
    async def access(request, call_next):
        protected = request.url.path.startswith(("/api/", "/v1/"))
        if protected:
            if settings.api_token and not secrets.compare_digest(
                request.headers.get("authorization", ""), "Bearer " + settings.api_token
            ):
                return JSONResponse({"detail": "Bearer token required"}, 401)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin requests are disabled"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if protected:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, 404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request, exc):
        return JSONResponse(
            {"detail": "Storage unavailable; retry the request"}, 503, headers={"Retry-After": "1"}
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/config")
    def config():
        classification_taxonomy = None
        if runtime.enabled("classification"):
            from .classification import taxonomy

            classification_taxonomy = taxonomy()
        return {
            "environment": settings.environment,
            "database_name": Path(settings.database).name,
            "features": sorted(runtime.features),
            "feature_catalog": runtime.catalog.catalog(runtime.features),
            "adapters": sorted(runtime.adapters),
            "model_mode": settings.model_mode,
            "model_api": settings.model_api,
            "max_import_bytes": settings.max_body_bytes,
            "classification_taxonomy": classification_taxonomy,
        }

    @app.get("/api/v1/sessions")
    def sessions(
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), q: str = "", category: str = "",
        identity_kind: str = "session",
    ):
        return store.list_sessions(limit, offset, q, category, identity_kind)

    @app.get("/api/v1/sessions/{sid}")
    def session(sid: str):
        return store.get_session(sid)

    @app.get("/api/v1/sessions/{sid}/events")
    def events(
        sid: str,
        limit: int = Query(200, ge=1, le=1000),
        after: int = Query(0, ge=0),
        kind: str = "",
        q: str = "",
        source: str = "",
        timeline: bool = False,
    ):
        store.get_session(sid)
        return store.events(sid, limit, after, kind, q, source, timeline)

    @app.get("/api/v1/sessions/{sid}/stats")
    def stats(sid: str, source: str = ""):
        return store.stats(sid, source)

    from .ingestion import CaptureError

    @app.exception_handler(CaptureError)
    async def captured_failure(request, exc):
        return JSONResponse({"detail": str(exc), "capture_id": exc.capture_id,
                             "capture_status": "retained", "normalization_status": "failed"},
                            exc.status_code, headers={"X-AgentBoard-Capture-ID": str(exc.capture_id),
                                                      **({"Retry-After": "1"} if exc.status_code == 503 else {})})

    owned = {(method, route.path) for route in app.routes if isinstance(route, APIRoute)
             for method in route.methods}
    try:
        for feature in runtime.catalog.enabled_features(runtime.features):
            router = feature.router_factory(runtime.services_for(feature.name))
            validate_router(feature.name, router, owned)
            app.include_router(router)
    except BaseException:
        runtime.close()
        raise

    static = frontend_directory()
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app


def create_app(settings=None, *, catalog=None):
    settings = settings or Settings()
    runtime = Runtime(settings, catalog)
    try:
        return _create_app(settings, runtime)
    except BaseException:
        runtime.close()
        raise


# Allowed routes are owned by core and validated before mounting.
FEATURE_ROUTES = {'import': [('POST', '/api/v1/import/{agent}')],
 'otlp_logs': [('POST', '/v1/logs')],
 'otlp_traces': [('POST', '/v1/traces')],
 'inputs': [('GET', '/api/v1/sessions/{sid}/inputs')],
 'unified_timeline': [('GET', '/api/v1/sessions/{sid}/unified')],
 'parallel_groups': [('GET', '/api/v1/sessions/{sid}/parallel-groups')],
 'field_lineage': [('GET', '/api/v1/sessions/{sid}/lineage'),
                   ('GET', '/api/v1/sessions/{sid}/events/{event_id}/lineage')],
 'raw_archive': [('GET', '/api/v1/sessions/{sid}/raw-imports'),
                 ('GET', '/api/v1/sessions/{sid}/events/{event_id}/raw'),
                 ('GET', '/api/v1/sessions/{sid}/raw'),
                 ('GET', '/api/v1/captures'),
                 ('GET', '/api/v1/captures/{capture_id}'),
                 ('GET', '/api/v1/captures/{capture_id}/raw')],
 'export': [('GET', '/api/v1/sessions/{sid}/export')],
 'classification': [('GET', '/api/v1/classification-schema'),
                    ('POST', '/api/v1/sessions/{sid}/classify'),
                    ('GET', '/api/v1/sessions/{sid}/classification-input'),
                    ('PUT', '/api/v1/sessions/{sid}/classification')],
 'replay': [('POST', '/api/v1/sessions/{sid}/replay')],
 'native_resume': [('POST', '/api/v1/sessions/{sid}/codex-plan')],
 'token_usage': [('GET', '/api/v1/pricing'),
                 ('GET', '/api/v1/sessions/{sid}/usage'),
                 ('GET', '/api/v1/sessions/{sid}/usage/export')]}


def validate_router(feature, router, owned):
    if not isinstance(router, APIRouter):
        raise ValueError("Feature must return an APIRouter")
    if router.on_startup or router.on_shutdown or type(router.lifespan_context) is not type(APIRouter().lifespan_context):
        raise ValueError("Feature lifecycle hooks are core-owned")
    allowed = set(FEATURE_ROUTES[feature])
    for route in router.routes:
        if not isinstance(route, APIRoute):
            raise ValueError("Feature may only contribute HTTP routes")
        for method in route.methods:
            key = (method, route.path)
            if key not in allowed or key in owned:
                raise ValueError(f"Unowned or duplicate feature route: {method} {route.path}")
            owned.add(key)
