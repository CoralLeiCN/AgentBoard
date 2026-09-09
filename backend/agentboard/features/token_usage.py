"""First-party token_usage request mapping."""


from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..services import UsageServices


def router(services: UsageServices):
    app = APIRouter()

    @app.get("/api/v1/pricing")
    def pricing():
        return services.pricing()

    @app.get("/api/v1/sessions/{sid}/usage")
    def usage(
        sid: str, model_override: str = "", import_id: int | None = Query(None, ge=1),
        after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000),
    ):
        report = services.usage(sid, model_override, import_id)
        rows = [row for row in report["items"] if row["line_number"] > after]
        report["items"] = rows[:limit]
        report["next_cursor"] = rows[limit - 1]["line_number"] if len(rows) > limit else None
        return report

    if services.export_enabled:
        @app.get("/api/v1/sessions/{sid}/usage/export")
        def usage_export(sid: str, model_override: str = "", import_id: int | None = Query(None, ge=1)):
            return JSONResponse(services.usage(sid, model_override, import_id), headers={
                "Content-Disposition": 'attachment; filename="agentboard-usage.json"',
            })

    return app
