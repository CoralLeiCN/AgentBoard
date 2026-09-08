from fastapi import APIRouter, Request

from ..services import ReceiverServices


def router(services: ReceiverServices):
    routes = APIRouter()

    @routes.post("/v1/traces")
    async def traces(request: Request):
        return await services.receive(request)

    return routes
