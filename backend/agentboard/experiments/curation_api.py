"""Local curation UI/API, intentionally independent of trace Runtime and model services."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, cast

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..classification import taxonomy
from .curation import CurationStore, ReviewAction, ReviewDecision, RevisionConflict, summary


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    action: ReviewAction
    target: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    category: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)


def preview(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "label", "comparison", "disposition", "coverage_complete")} | {
        "session_id": row["target"]["original_codex_session_id"],
        "turn_id": row["target"]["original_codex_turn_id"],
        "input": "\n".join(m["content"] for m in row["target"]["messages"] if m.get("role") == "user")[:240],
    }


def create_review_app(store: CurationStore, wid: str) -> FastAPI:
    from ..api import frontend_directory

    store.read(wid)
    assets = frontend_directory()
    app = FastAPI(title="AgentBoard dataset curation")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_access(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, 409 if isinstance(exc, RevisionConflict) else 422)

    @app.exception_handler(OSError)
    async def storage_error(request: Request, exc: OSError) -> JSONResponse:
        return JSONResponse({"detail": "Curation storage unavailable; no successful write confirmed"}, 503)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(assets / "curation.html")

    @app.get("/curation.js")
    def script() -> FileResponse:
        return FileResponse(assets / "curation.js")

    @app.get("/curation.css")
    def style() -> FileResponse:
        return FileResponse(assets / "curation.css")

    @app.get("/api/workspace")
    def workspace() -> dict[str, Any]:
        value = store.read(wid)
        return {
            **summary(value),
            "similarity": value["similarity"],
            "recipe": value["recipe"],
            "taxonomy": taxonomy(),
            "created_at": value["created_at"],
        }

    @app.get("/api/turns")
    def turns(
        queue: Literal["disagreements", "all", "unlabeled", "verified", "removed"] = "disagreements",
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict[str, Any]:
        value = store.read(wid)
        rows = value["rows"]
        if queue == "disagreements":
            rows = [
                r
                for r in rows
                if r["comparison"] == "disagreement"
                and r["label"]["status"] != "verified"
                and r["disposition"] == "active"
            ]
        elif queue == "removed":
            rows = [r for r in rows if r["disposition"] != "active"]
        elif queue != "all":
            rows = [r for r in rows if r["label"]["status"] == queue]
        return {
            "items": [preview(r) for r in rows[offset : offset + limit]],
            "total": len(rows),
            "revision": value["revision"],
        }

    @app.get("/api/turns/{target}", response_model=None)
    def turn(target: str) -> dict[str, Any] | JSONResponse:
        value = store.read(wid)
        row = next((r for r in value["rows"] if r["id"] == target), None)
        if row is None:
            return JSONResponse({"detail": "Unknown target"}, 404)
        previous = row["target"].get("previous_turn_index")
        context = next(
            (
                r["target"]
                for r in value["rows"]
                if previous is not None
                and r["target"].get("index") == previous
                and r["target"]["original_codex_session_id"] == row["target"]["original_codex_session_id"]
            ),
            None,
        )
        return {
            **row,
            "context": context,
            "context_status": "not_required"
            if previous is None
            else "available"
            if context is not None
            else "unavailable_in_selected_dataset",
            "revision": value["revision"],
            "history": [
                h
                for h in value["history"]
                if h["target"] == target
                or any(p["id"] == h["target"] and target in (p["left"], p["right"]) for p in value["pairs"])
            ],
        }

    @app.get("/api/pairs")
    def pairs(
        include_reviewed: bool = False, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
    ) -> dict[str, Any]:
        value = store.read(wid)
        rows = {r["id"]: r for r in value["rows"]}
        pairs = [
            p
            for p in value["pairs"]
            if include_reviewed
            or (
                p["decision"] == "pending"
                and rows[p["left"]]["disposition"] == rows[p["right"]]["disposition"] == "active"
            )
        ]
        return {
            "items": [
                {**p, "left_turn": preview(rows[p["left"]]), "right_turn": preview(rows[p["right"]])}
                for p in pairs[offset : offset + limit]
            ],
            "total": len(pairs),
            "revision": value["revision"],
        }

    @app.post("/api/decisions")
    def decide(decision: Decision) -> dict[str, Any]:
        return store.decide(wid, cast(ReviewDecision, decision.model_dump()))

    @app.post("/api/export")
    def export(request: ExportRequest) -> dict[str, Any]:
        return store.export(wid, request.revision)

    @app.get("/api/exports/{bid}/{view}", response_model=None)
    def download(bid: str, view: Literal["all", "active", "workspace"]) -> FileResponse | JSONResponse:
        manifest = store.archive.inspect(bid)
        if manifest.get("configuration", {}).get("workspace_id") != wid:
            return JSONResponse({"detail": "Export belongs to another workspace"}, 404)
        name = "workspace.json" if view == "workspace" else f"{view}-turns.json"
        ref = store.archive.reference(bid, "outputs/" + name)
        return FileResponse(store.archive.resolve(ref), filename=name)

    return app
