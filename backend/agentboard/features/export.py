"""First-party export request mapping."""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..services import ExportServices


def router(services: ExportServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/export")
    def export(sid: str, kind: str = ""):
        services.get_session(sid)
        return StreamingResponse(
            (json.dumps(e) + "\n" for e in services.export(sid, kind)),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="events.jsonl"'},
        )

    return app
