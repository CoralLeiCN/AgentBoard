"""First-party unified_timeline request mapping."""


from fastapi import APIRouter, Query

from ..services import UnifiedServices


def router(services: UnifiedServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/unified")
    def unified(
        sid: str, limit: int = Query(200, ge=1, le=1000),
        after: int = Query(0, ge=0), kind: str = "", q: str = "",
    ):
        return services.unified(sid, limit, after, kind, q)

    return app
