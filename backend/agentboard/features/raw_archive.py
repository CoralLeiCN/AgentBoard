"""First-party raw_archive request mapping."""


from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..services import ArchiveServices


def router(services: ArchiveServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/raw-imports")
    def raw_imports(sid: str):
        services.get_session(sid)
        return {"items": services.raw_imports(sid)}

    @app.get("/api/v1/sessions/{sid}/events/{event_id}/raw")
    def event_raw(sid: str, event_id: str):
        return services.event_raw(sid, event_id)

    if services.export_enabled:
        @app.get("/api/v1/sessions/{sid}/raw")
        def raw_export(sid: str, import_id: int | None = Query(None, ge=1)):
            archive = services.raw_import(sid, import_id)
            return StreamingResponse(
                services.export_raw(sid, archive["id"]),
                media_type="application/x-ndjson",
                headers={"Content-Disposition": 'attachment; filename="rollout.jsonl"'},
            )

    @app.get("/api/v1/captures")
    def captures(limit: int = Query(50, ge=1, le=200), after: int = Query(0, ge=0)):
        return services.captures(limit, after)

    @app.get("/api/v1/captures/{capture_id}")
    def capture(capture_id: int):
        return services.capture(capture_id)

    if services.export_enabled:
        @app.get("/api/v1/captures/{capture_id}/raw")
        def capture_raw(capture_id: int):
            services.capture(capture_id)
            return StreamingResponse(services.export_capture(capture_id), media_type="application/octet-stream",
                                     headers={"Content-Disposition": f'attachment; filename="capture-{capture_id}.bin"'})

    return app
