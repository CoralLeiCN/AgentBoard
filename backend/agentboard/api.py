import importlib
import io
import json
import secrets
import sqlite3
import threading
import zlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.protobuf.json_format import ParseError
from google.protobuf.message import DecodeError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .adapters import adapters
from .config import Settings
from .models import ModelGateway, ModelServiceError, classify_session, replay_session
from .otlp import decode, normalize
from .store import Store


def frontend_directory():
    """Use editable source assets, falling back to the copy bundled in a wheel."""
    source = Path(__file__).resolve().parents[2] / "frontend"
    return source if (source / "index.html").is_file() else Path(__file__).parent / "static"


class ReplayRequest(BaseModel):
    input_id: str
    replacement: str = Field(min_length=1, max_length=30000)


class ClassificationRequest(BaseModel):
    category: str
    reason: str = Field(max_length=2000)
    model: str = Field(min_length=1, max_length=200)


async def bounded_body(request, limit):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Request exceeds body limit")
        chunks.append(chunk)
    body = b"".join(chunks)
    encoding = request.headers.get("content-encoding", "identity")
    if encoding == "gzip":
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decoder.decompress(body, limit + 1)
            if len(body) > limit or decoder.unconsumed_tail:
                raise HTTPException(413, "Decompressed request exceeds body limit")
            if not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid gzip stream")
        except (zlib.error, ValueError) as exc:
            raise HTTPException(400, "Invalid gzip body") from exc
    elif encoding != "identity":
        raise HTTPException(415, "Only identity and gzip content encoding are supported")
    return body


def create_app(settings=None):
    settings = settings or Settings()
    app = FastAPI(title="AgentBoard", version="0.1.0", description="Agent-neutral trace and session API")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    ingest_slots = threading.BoundedSemaphore(settings.ingest_concurrency)
    app.state.ingest_slots = ingest_slots
    store = Store(settings.database)
    app.state.store, app.state.settings = store, settings
    registry = adapters()
    gateway = ModelGateway(settings)
    app.state.gateway = gateway

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

    @app.exception_handler(ModelServiceError)
    async def model_error(request, exc):
        return JSONResponse({"detail": str(exc)}, 502)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request, exc):
        return JSONResponse(
            {"detail": "Storage unavailable; retry the request"}, 503, headers={"Retry-After": "1"}
        )

    def feature(name):
        if name not in settings.features:
            raise HTTPException(404, f"{name} feature is disabled")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/config")
    def config():
        return {
            "environment": settings.environment,
            "database_name": Path(settings.database).name,
            "otlp_enabled": settings.otlp_enabled,
            "features": sorted(settings.features),
            "adapters": sorted(registry),
            "model_mode": settings.model_mode,
            "max_import_bytes": settings.max_body_bytes,
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

    @app.get("/api/v1/sessions/{sid}/inputs")
    def inputs(sid: str, limit: int = Query(200, ge=1, le=1000), after: int = Query(0, ge=0), q: str = ""):
        store.get_session(sid)
        return store.events(sid, limit, after, "user", q)

    @app.get("/api/v1/sessions/{sid}/unified")
    def unified(
        sid: str, limit: int = Query(200, ge=1, le=1000),
        after: int = Query(0, ge=0), kind: str = "", q: str = "",
    ):
        return store.unified(sid, limit, after, kind, q)

    @app.get("/api/v1/sessions/{sid}/stats")
    def stats(sid: str, source: str = ""):
        return store.stats(sid, source)

    @app.get("/api/v1/sessions/{sid}/parallel-groups")
    def parallel_groups(sid: str, source: str = ""):
        return store.parallel_groups(sid, source)

    @app.get("/api/v1/sessions/{sid}/export")
    def export(sid: str, kind: str = ""):
        store.get_session(sid)
        return StreamingResponse(
            (json.dumps(e) + "\n" for e in store.export(sid, kind)),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="events.jsonl"'},
        )

    @app.post("/api/v1/import/{agent}")
    async def ingest(agent: str, request: Request):
        if agent not in registry:
            raise HTTPException(404, "Unknown agent adapter")
        if not ingest_slots.acquire(blocking=False):
            raise HTTPException(503, "Ingestion capacity reached", headers={"Retry-After": "1"})
        try:
            body = await bounded_body(request, settings.max_body_bytes)

            def ingest_body():
                return store.ingest(registry[agent].parse(io.StringIO(body.decode("utf-8"))))

            return await run_in_threadpool(ingest_body)
        finally:
            ingest_slots.release()

    @app.get("/api/v1/sessions/{sid}/raw-imports")
    def raw_imports(sid: str):
        store.get_session(sid)
        return {"items": store.raw_imports(sid)}

    @app.get("/api/v1/sessions/{sid}/events/{event_id}/raw")
    def event_raw(sid: str, event_id: str):
        return store.event_raw(sid, event_id)

    @app.get("/api/v1/sessions/{sid}/lineage")
    def session_lineage(sid: str):
        return store.field_lineage(sid)

    @app.get("/api/v1/sessions/{sid}/events/{event_id}/lineage")
    def event_lineage(sid: str, event_id: str):
        return store.field_lineage(sid, event_id)

    @app.get("/api/v1/sessions/{sid}/raw")
    def raw_export(sid: str, import_id: int | None = Query(None, ge=1)):
        archive = store.raw_import(sid, import_id)
        return StreamingResponse(
            store.export_raw(sid, archive["id"]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="rollout.jsonl"'},
        )

    async def otlp(request, signal):
        if not settings.otlp_enabled:
            raise HTTPException(403, "Live OTLP ingestion is disabled in this environment; use an explicit import")
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type not in ("application/json", "application/x-protobuf"):
            raise HTTPException(415, "Use application/json or application/x-protobuf")
        binary = content_type == "application/x-protobuf"
        if not ingest_slots.acquire(blocking=False):
            raise HTTPException(503, "Ingestion capacity reached", headers={"Retry-After": "1"})
        try:
            body = await bounded_body(request, settings.max_body_bytes)

            def ingest_body():
                try:
                    return store.ingest(normalize(decode(body, signal, binary), signal))
                except (ValueError, DecodeError, ParseError, TypeError, OverflowError) as exc:
                    raise HTTPException(400, f"Invalid OTLP payload: {type(exc).__name__}") from exc

            await run_in_threadpool(ingest_body)
        finally:
            ingest_slots.release()
        return Response(b"", media_type=content_type) if binary else JSONResponse({})

    @app.post("/v1/traces")
    async def traces(request: Request):
        return await otlp(request, "traces")

    @app.post("/v1/logs")
    async def logs(request: Request):
        return await otlp(request, "logs")

    @app.post("/api/v1/sessions/{sid}/classify")
    def classify(sid: str):
        feature("classification")
        return classify_session(store, sid, gateway, settings.max_model_chars)

    @app.put("/api/v1/sessions/{sid}/classification")
    def external_classification(sid: str, body: ClassificationRequest):
        feature("classification")
        from .models import CATEGORIES

        if body.category not in CATEGORIES:
            raise ValueError("Unsupported category")
        result = {**body.model_dump(), "provider": "external", "dummy": False}
        store.classify(sid, result)
        return result

    @app.post("/api/v1/sessions/{sid}/replay")
    def replay(sid: str, body: ReplayRequest):
        feature("replay")
        return replay_session(store, sid, body.input_id, body.replacement, gateway, settings.max_model_chars)

    @app.post("/api/v1/sessions/{sid}/codex-plan")
    def codex_plan(sid: str, body: ReplayRequest):
        feature("replay")
        from .resume import make_plan

        return make_plan(store, sid, body.input_id, body.replacement)

    for module in settings.plugins:
        importlib.import_module(module).register(app=app, store=store, adapters=registry, settings=settings)

    static = frontend_directory()
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app
