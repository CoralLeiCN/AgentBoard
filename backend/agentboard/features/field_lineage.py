"""First-party field_lineage request mapping."""


from fastapi import APIRouter

from ..services import LineageServices


def router(services: LineageServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/lineage")
    def session_lineage(sid: str):
        return services.field_lineage(sid)

    @app.get("/api/v1/sessions/{sid}/events/{event_id}/lineage")
    def event_lineage(sid: str, event_id: str):
        return services.field_lineage(sid, event_id)

    return app
