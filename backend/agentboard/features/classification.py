"""First-party classification request mapping."""


from fastapi import APIRouter

from ..services import ClassificationServices


def router(services: ClassificationServices):
    app = APIRouter()
    import time

    from ..classification import (
        TAXONOMY_VERSION,
        ClassificationRequest,
        ClassificationResult,
        classification_schema,
    )
    from ..timestamps import format_timestamp

    @app.get("/api/v1/classification-schema")
    def classifier_schema():
        return classification_schema()

    @app.post("/api/v1/sessions/{sid}/classify", response_model=ClassificationResult, response_model_exclude_none=True)
    def classify(sid: str):
        return services.run(sid)

    @app.get("/api/v1/sessions/{sid}/classification-input")
    def classifier_input(sid: str):
        return services.input(sid)

    @app.put("/api/v1/sessions/{sid}/classification", response_model=ClassificationResult, response_model_exclude_none=True)
    def external_classification(sid: str, body: ClassificationRequest):
        if services.get_session(sid)["identity_kind"] != "session":
            raise ValueError("Purpose classification requires a session, not unattributed telemetry")
        result = {
            **body.model_dump(mode="json", exclude_none=True), "provider": "external", "dummy": False,
            "taxonomy_version": TAXONOMY_VERSION, "classified_at": format_timestamp(time.time_ns()),
            "content_origin": "model_generated", "provenance": "externally_asserted",
        }
        result = ClassificationResult.model_validate(result).model_dump(mode="json", exclude_none=True)
        services.classify(sid, result)
        return result

    return app
