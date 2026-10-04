"""Dedicated Spark encoder HTTP service; no Codex process or provider fallback."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(strict=True)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=256, strict=True)
    target_turn: list[Message]
    preceding_turn_context: list[Message]


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(min_length=1, max_length=16)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            part = await receive()
            if part["type"] == "http.disconnect":
                return
            body.extend(part.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await JSONResponse({"detail": "Request body exceeds 2 MiB"}, status_code=413)(
                    scope, receive, send
                )
            if not part.get("more_body", False):
                break

        async def bounded_receive():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        return await self.app(scope, bounded_receive, send)


def create_app(loader=None):
    @asynccontextmanager
    async def lifespan(app):
        from .encoder_runtime import Encoder

        app.state.encoder = await run_in_threadpool(
            loader or (lambda: Encoder(Path(os.environ["TURN_ENCODER_MODEL"])))
        )
        yield
        app.state.encoder = None

    app = FastAPI(title="Turn encoder", lifespan=lifespan)
    app.state.encoder = None
    app.state.busy = False
    app.add_middleware(BodyLimit)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default validation details echo submitted transcript values.
        return JSONResponse({"detail": "Invalid request schema"}, status_code=422)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    @app.get("/v1/model")
    async def metadata():
        if app.state.encoder is None:
            raise HTTPException(503, "Encoder is not loaded")
        return app.state.encoder.metadata

    @app.post("/v1/turn-classifications")
    async def classify(batch: Batch):
        if app.state.encoder is None:
            raise HTTPException(503, "Encoder is not loaded")
        if app.state.busy:
            raise HTTPException(429, "Encoder is busy; retry later")
        if len({i.request_id for i in batch.items}) != len(batch.items):
            raise HTTPException(422, "request_id values must be unique within the batch")
        app.state.busy = True
        try:
            return {
                "results": await run_in_threadpool(
                    app.state.encoder.predict, [i.model_dump() for i in batch.items]
                )
            }
        except (ValueError, RuntimeError):
            raise HTTPException(503, "Encoder inference failed") from None
        finally:
            app.state.busy = False

    return app
