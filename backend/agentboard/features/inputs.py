"""First-party inputs request mapping."""


from fastapi import APIRouter, Query

from ..services import InputServices


def router(services: InputServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/inputs")
    def inputs(sid: str, limit: int = Query(200, ge=1, le=1000), after: int = Query(0, ge=0), q: str = ""):
        services.get_session(sid)
        return services.events(sid, limit, after, "user", q)

    return app
