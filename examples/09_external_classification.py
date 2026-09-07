"""An external AI worker fetches bounded input, classifies via the OpenAI-compatible gateway,
and writes the result back to the unified API. Dummy results keep their provenance by
using the built-in classification route; this example only submits real model results.
"""

from common import demo_session, request

from agentboard.classification import ClassificationLabel
from agentboard.config import Settings
from agentboard.models import ModelGateway

sid = demo_session()
context = request("GET", f"/api/v1/sessions/{sid}/classification-input").json()
result = ModelGateway(Settings()).complete(
    context["messages"],
    classification=True,
)
if result["dummy"]:
    print("Local model unavailable; exercising the built-in dummy classification path.")
    print(request("POST", f"/api/v1/sessions/{sid}/classify").json())
else:
    classification = ClassificationLabel.model_validate_json(result["text"]).model_dump(mode="json")
    print(
        request(
            "PUT",
            f"/api/v1/sessions/{sid}/classification",
            json={
                "category": classification["category"],
                "reason": classification["reason"],
                "model": result["model"],
                "input_sha256": context["input_sha256"],
            },
        ).json()
    )
