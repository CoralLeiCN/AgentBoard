"""First-party parallel_groups request mapping."""


from fastapi import APIRouter

from ..services import ParallelServices


def router(services: ParallelServices):
    app = APIRouter()

    @app.get("/api/v1/sessions/{sid}/parallel-groups")
    def parallel_groups(sid: str, source: str = ""):
        return services.parallel_groups(sid, source)

    return app
