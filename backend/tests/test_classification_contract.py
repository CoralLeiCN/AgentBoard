import hashlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentboard.api import create_app
from agentboard.classification import (
    PURPOSES,
    ClassificationLabel,
    ClassificationRequest,
    PurposeCategory,
    classification_schema,
)
from agentboard.config import Settings

EXPECTED_CATEGORIES = [
    "writing", "coding", "bug-fixing", "research", "analysis", "creative-media", "guidance", "other",
]


def test_contract_has_exact_enum_required_fields_and_no_extra_properties(client):
    schema = client.get("/api/v1/classification-schema").json()
    assert schema == classification_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["category", "reason"]
    assert set(schema["properties"]) == {"category", "reason"}
    assert schema["$defs"]["PurposeCategory"]["enum"] == EXPECTED_CATEGORIES
    assert schema["properties"]["reason"]["minLength"] == 1
    assert schema["properties"]["reason"]["maxLength"] == 2000
    pattern = schema["properties"]["reason"]["pattern"]
    # Some compatible decoders use full-match semantics instead of JSON Schema's search semantics.
    assert re.fullmatch(pattern, "The user asks to implement software.\nA second sentence.")
    assert not re.search(pattern, " \n\t")
    assert [item[0].value for item in PURPOSES] == EXPECTED_CATEGORIES
    label = ClassificationLabel.model_validate_json('{"category":"coding","reason":"Implements a feature."}')
    assert label.category is PurposeCategory.CODING
    assert label.model_dump(mode="json")["category"] == "coding"


def test_openapi_declares_typed_request_and_result(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert schemas["PurposeCategory"]["enum"] == EXPECTED_CATEGORIES
    assert schemas["ClassificationRequest"]["additionalProperties"] is False
    category = schemas["ClassificationRequest"]["properties"]["category"]
    assert category["anyOf"] == [{"$ref": "#/components/schemas/PurposeCategory"},
                                 {"type": "string", "const": "debugging"}]
    assert schemas["ClassificationResult"]["properties"]["category"] == {
        "$ref": "#/components/schemas/PurposeCategory",
    }
    for path, method in (("/api/v1/sessions/{sid}/classify", "post"),
                         ("/api/v1/sessions/{sid}/classification", "put")):
        response = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response == {"$ref": "#/components/schemas/ClassificationResult"}


def test_legacy_alias_only_normalizes_at_external_submission_boundary():
    with pytest.raises(ValidationError):
        ClassificationLabel(category="debugging", reason="Diagnoses a bug.")
    request = ClassificationRequest(category="debugging", reason="Diagnoses a bug.", model="external-agent")
    assert request.category is PurposeCategory.DEBUGGING
    assert request.model_dump(mode="json")["category"] == "bug-fixing"


@pytest.mark.parametrize("extra", [{"confidence": 0.9}, {"provider": "dummy"}, {"dummy": True}])
def test_external_agent_cannot_add_uncontracted_fields_or_override_provenance(client, imported, extra):
    original = client.post(f"/api/v1/sessions/{imported}/classify").json()
    response = client.put(f"/api/v1/sessions/{imported}/classification", json={
        "category": "coding", "reason": "Implements software.", "model": "agent", **extra,
    })
    assert response.status_code == 422
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"] == original


def test_saved_schema_hash_matches_public_contract(client, imported):
    result = client.post(f"/api/v1/sessions/{imported}/classify").json()
    schema = client.get("/api/v1/classification-schema").json()
    expected = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert result["output_schema_sha256"] == expected
    assert client.get(f"/api/v1/sessions/{imported}/classification-input").json()["output_schema_sha256"] == expected
    settings = Settings(database=client.app.state.settings.database, features=set())
    with TestClient(create_app(settings)) as disabled:
        assert disabled.get("/api/v1/classification-schema").status_code == 404


@pytest.mark.parametrize("finish_reason,refusal", [("length", None), ("content_filter", None),
                                                 ("stop", "Cannot classify this input.")])
def test_chat_refusals_and_incomplete_json_do_not_overwrite(client, imported, monkeypatch, finish_reason, refusal):
    import openai

    original = client.post(f"/api/v1/sessions/{imported}/classify").json()
    real_client = openai.OpenAI
    body = {"choices": [{"finish_reason": finish_reason, "message": {
        "role": "assistant", "refusal": refusal,
        "content": '{"category":"coding","reason":"This JSON looks valid but is not a completed answer."}',
    }}]}
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
    ))
    client.app.state.settings.model_mode = "auto"
    client.app.state.settings.model_api = "chat_completions"
    client.app.state.settings.model = "test"
    response = client.post(f"/api/v1/sessions/{imported}/classify")
    assert response.status_code == 502, response.text
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"] == original
