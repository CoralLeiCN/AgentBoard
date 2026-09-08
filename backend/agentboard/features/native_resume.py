"""First-party native_resume request mapping."""


from fastapi import APIRouter

from ..services import ResumeServices
from .requests import ReplayRequest


def router(services: ResumeServices):
    app = APIRouter()

    @app.post("/api/v1/sessions/{sid}/codex-plan")
    def codex_plan(sid: str, body: ReplayRequest):
        return services.plan(sid, body.input_id, body.replacement)

    return app
