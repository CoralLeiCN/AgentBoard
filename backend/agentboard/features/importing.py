from fastapi import APIRouter, HTTPException, Request

from ..services import ImportServices


def router(services: ImportServices):
    routes = APIRouter()

    @routes.post("/api/v1/import/{agent}")
    async def ingest(agent: str, request: Request):
        if agent not in services.adapters:
            raise HTTPException(404, "Unknown agent adapter")
        return await services.receive(agent, request)

    return routes
