"""Opt-in live Responses test. Sends only synthetic sessions to the configured endpoint."""

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings

CASES = (
    ("debugging", None, "bug-fixing"),
    ("writing", "Draft a friendly email inviting my colleagues to a team lunch next Friday.", "writing"),
    ("coding", "Implement a Python function that merges two sorted lists in linear time.", "coding"),
    ("analysis", "Calculate the mean, median, and standard deviation of these sales totals: 12, 18, 25, 31.",
     "analysis"),
)


@pytest.mark.e2e
@pytest.mark.parametrize("case,prompt,expected", CASES, ids=[case[0] for case in CASES])
def test_live_responses_classification(tmp_path, private_endpoint, case, prompt, expected):
    endpoint = private_endpoint("AGENTBOARD_E2E_CLASSIFICATION")
    app = create_app(Settings(
        database=str(tmp_path / "classification.db"), features={"classification"}, plugins=(),
        otlp_enabled=False, model_mode="local", model_api="responses", model_base_url=endpoint.base_url,
        model=endpoint.model, model_key=endpoint.api_key or "local", api_token="",
        model_timeout_seconds=int(os.getenv("AGENTBOARD_E2E_CLASSIFICATION_TIMEOUT_SECONDS", "120")),
    ))
    if prompt is None:
        content = (Path(__file__).parents[2] / "examples/fixtures/codex-session.jsonl").read_bytes()
    else:
        records = [
            {"timestamp": "2026-09-07T10:00:00Z", "type": "session_meta", "payload": {
                "id": f"synthetic-purpose-{case}", "originator": "agentboard-synthetic-test",
            }},
            {"timestamp": "2026-09-07T10:00:01Z", "type": "event_msg", "payload": {
                "type": "user_message", "message": prompt,
            }},
        ]
        content = "".join(json.dumps(row) + "\n" for row in records).encode()
    with TestClient(app) as client:
        imported = client.post("/api/v1/import/codex", content=content)
        assert imported.status_code == 200, imported.text
        sid = imported.json()["session_ids"][0]
        start = time.monotonic()
        response = client.post(f"/api/v1/sessions/{sid}/classify")
        elapsed = round(time.monotonic() - start, 3)
        assert response.status_code == 200, response.text
        result = response.json()
        print(json.dumps({"case": case, "expected": expected, "elapsed_seconds": elapsed, **result}), flush=True)
        assert result["api"] == "responses" and result["dummy"] is False
        assert result["content_origin"] == "model_generated"
        assert result["category"] == expected
        assert result["model"] and len(result["reason"].split()) >= 3, "Expected an explanatory reason"
        assert result["output_schema_sha256"]
        assert client.get(f"/api/v1/sessions/{sid}").json()["classification"] == result
