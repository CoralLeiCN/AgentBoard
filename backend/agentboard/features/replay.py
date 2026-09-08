"""First-party replay request mapping."""


from fastapi import APIRouter

from ..services import ReplayServices
from .requests import ReplayRequest


def router(services: ReplayServices):
    app = APIRouter()

    @app.post("/api/v1/sessions/{sid}/replay")
    def replay(sid: str, body: ReplayRequest):
        return services.run(sid, body.input_id, body.replacement)

    return app
