import hashlib
import json
import sys

import httpx
import pytest

from agentboard.classification import (
    CATEGORIES,
    CLASSIFICATION_PROMPT,
    TAXONOMY_VERSION,
    classification_schema,
)
from agentboard.cli import main
from agentboard.domain import Event, Session
from agentboard.models import ModelGateway, ModelServiceError


def conversation(store, sid, text="Calculate the mean", **session_kwargs):
    start = "2026-09-07T10:00:00Z"
    store.ingest([
        Session(id=sid, started_at=start, **session_kwargs),
        Event(id=sid + "-user", session_id=sid, sequence=1, kind="user",
              name="User", start_time=start, text=text),
    ])


@pytest.mark.parametrize("category", CATEGORIES)
def test_model_categories_persist_and_filter(client, imported, category):
    client.app.state.gateway.complete = lambda *a, **k: {
        "text": json.dumps({"category": category, "reason": "The user requests this outcome."}),
        "model": "test-llm", "provider": "test", "dummy": False,
    }
    result = client.post(f"/api/v1/sessions/{imported}/classify")
    assert result.status_code == 200, result.text
    label = result.json()
    assert label["category"] == category
    assert label["content_origin"] == "model_generated"
    assert label["taxonomy_version"] == TAXONOMY_VERSION
    assert label["classified_at"].endswith("Z")
    assert client.get("/api/v1/sessions", params={"category": category}).json()["total"] == 1
    assert client.get("/api/v1/sessions?category=unclassified").json()["total"] == 0
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"] == label


def test_shared_input_order_source_hash_and_truncation(client):
    store = client.app.state.store
    conversation(store, "ordered", "First request")
    start = "2026-09-07T10:00:00Z"
    store.ingest([
        Event(id="last", session_id="ordered", sequence=3, kind="user", name="User",
              start_time=start, text="Final request"),
        Event(id="middle", session_id="ordered", sequence=2, kind="assistant", name="Assistant",
              start_time=start, text="Middle response"),
        Event(id="duplicate", session_id="ordered", sequence=0, kind="user", name="User",
              start_time=start, text="DUPLICATE TELEMETRY", source="otlp_log"),
        Event(id="tool", session_id="ordered", sequence=2, kind="tool", name="Shell",
              start_time=start, text="TOOL BODY", attributes={"output": "RAW OUTPUT"}),
    ])
    path = "/api/v1/sessions/ordered/classification-input"
    context = client.get(path).json()
    transcript = "user: First request\nassistant: Middle response\nuser: Final request\n"
    assert context["messages"] == [
        {"role": "system", "content": CLASSIFICATION_PROMPT},
        {"role": "user", "content": transcript},
    ]
    assert context["input_event_ids"] == ["ordered-user", "middle", "last"]
    assert context["input_source"] == "codex_jsonl"
    assert context["input_sha256"] == hashlib.sha256(transcript.encode()).hexdigest()
    assert context["truncated"] is False
    client.app.state.settings.max_model_chars = len(transcript)
    assert client.get(path).json()["truncated"] is False
    client.app.state.settings.max_model_chars = 20
    partial = client.get(path).json()
    assert partial["input_chars"] == 20 and partial["truncated"] is True
    assert partial["messages"][1]["content"] == transcript[:20]
    result = client.post("/api/v1/sessions/ordered/classify").json()
    assert result["input_sha256"] == partial["input_sha256"]
    assert result["truncated"] is True


def test_telemetry_classification_orders_across_batches_before_truncation(client):
    def prompt(second, text):
        return {
            "timeUnixNano": str(1788775200000000000 + second * 1000000000),
            "body": {"stringValue": "codex.user_prompt"},
            "attributes": [
                {"key": "conversation.id", "value": {"stringValue": "telemetry-order"}},
                {"key": "prompt", "value": {"stringValue": text}},
            ],
        }

    # Sequences restart in each batch; the final batch also arrives late.
    for records in ([prompt(1, "First"), prompt(3, "Third")],
                    [prompt(4, "Fourth"), prompt(4, "Same timestamp")],
                    [prompt(2, "Second")]):
        response = client.post("/v1/logs", json={
            "resourceLogs": [{"scopeLogs": [{"logRecords": records}]}],
        })
        assert response.status_code == 200, response.text

    path = "/api/v1/sessions/telemetry-order/classification-input"
    context = client.get(path).json()
    transcript = "user: First\nuser: Second\nuser: Third\nuser: Fourth\nuser: Same timestamp\n"
    assert context["messages"][1]["content"] == transcript
    assert context["input_source"] == "otlp_log"
    events = {e["text"]: e["id"] for e in client.app.state.store.export("telemetry-order")}
    assert context["input_event_ids"] == [
        events[text] for text in ("First", "Second", "Third", "Fourth", "Same timestamp")
    ]

    prefix = "user: First\nuser: Second\n"
    client.app.state.settings.max_model_chars = len(prefix)
    partial = client.get(path).json()
    assert partial["messages"][1]["content"] == prefix
    assert partial["input_event_ids"] == [events["First"], events["Second"]]
    assert partial["input_sha256"] == hashlib.sha256(prefix.encode()).hexdigest()
    assert partial["truncated"] is True


@pytest.mark.parametrize("text", ["", "   ", "\t\n", "[REDACTED]", "\n[redacted]\t", "[Prompt content not recorded]"])
def test_missing_evidence_does_not_call_model_or_replace_label(client, text):
    store = client.app.state.store
    conversation(store, "empty", text)
    store.classify("empty", {"category": "writing", "reason": "Previously reviewed", "model": "external"})
    client.app.state.gateway.complete = lambda *a, **k: pytest.fail("No evidence must not invoke a model")
    response = client.post("/api/v1/sessions/empty/classify")
    assert response.status_code == 422
    assert "No recorded conversation text" in response.json()["detail"]
    assert store.get_session("empty")["classification"]["category"] == "writing"


@pytest.mark.parametrize("output", [
    "[]", "null", '{"category":"analysis","reason":{}}',
    '{"category":"analysis","reason":"  "}', '{"category":"unknown","reason":"No"}',
    json.dumps({"category": "analysis", "reason": "x" * 2001}),
    '{"category":"debugging","reason":"Alias is only valid for external submissions."}',
    '{"category":"analysis","reason":"Valid reason","unexpected":true}',
    '```json\n{"category":"analysis","reason":"Fenced JSON is not the contract."}\n```',
])
def test_invalid_model_results_do_not_replace_labels(client, imported, output):
    store = client.app.state.store
    original = {"category": "coding", "reason": "Original", "model": "reviewer"}
    store.classify(imported, original)
    client.app.state.gateway.complete = lambda *a, **k: {"text": output, "model": "test", "dummy": False}
    assert client.post(f"/api/v1/sessions/{imported}/classify").status_code == 422
    assert store.get_session(imported)["classification"] == original


def test_external_agent_taxonomy_input_and_legacy_alias(client, imported):
    taxonomy = client.get("/api/v1/config").json()["classification_taxonomy"]
    assert {item["id"] for item in taxonomy["categories"]} == set(CATEGORIES)
    assert taxonomy["source_page"] == 14
    context = client.get(f"/api/v1/sessions/{imported}/classification-input").json()
    response = client.put(f"/api/v1/sessions/{imported}/classification", json={
        "category": "debugging", "reason": "Diagnoses a regression.", "model": "external-agent",
        "input_sha256": context["input_sha256"],
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["category"] == "bug-fixing"
    assert result["provenance"] == "externally_asserted"
    assert result["input_sha256"] == context["input_sha256"]
    assert client.get("/api/v1/sessions?category=debugging").json()["total"] == 1
    assert client.get("/api/v1/sessions?category=bug-fixing").json()["total"] == 1
    assert client.put(f"/api/v1/sessions/{imported}/classification", json={
        "category": "analysis", "reason": " ", "model": "external-agent",
    }).status_code == 422


def test_classification_through_openai_compatible_http(client, imported, monkeypatch):
    import openai

    real_client = openai.OpenAI
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "classification-test", "object": "chat.completion", "created": 0,
            "model": "configured-model", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": '{"category":"bug-fixing","reason":"Fixes the checkout rounding regression."}',
                },
            }],
        })

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ))
    client.app.state.settings.model_mode = "local"
    client.app.state.settings.model = "configured-model"
    response = client.post(f"/api/v1/sessions/{imported}/classify")
    assert response.status_code == 200, response.text
    assert response.json()["dummy"] is False
    assert response.json()["category"] == "bug-fixing"
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == "configured-model"
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "session_purpose", "strict": True, "schema": classification_schema(),
    }}
    assert body["messages"][0]["content"] == CLASSIFICATION_PROMPT
    assert "Fix the checkout total rounding bug" in body["messages"][1]["content"]


def test_feature_gate_and_unattributed_exclusion(client):
    conversation(client.app.state.store, "telemetry", identity_kind="unattributed_trace")
    assert client.post("/api/v1/sessions/telemetry/classify").status_code == 422
    assert client.get("/api/v1/sessions/telemetry/classification-input").status_code == 422
    assert client.app.state.store.classification_candidates() == []
    client.app.state.settings.features = set()
    assert client.get("/api/v1/sessions/telemetry/classification-input").status_code == 404


def test_cli_batch_snapshot_skips_existing_and_continues_failures(client, monkeypatch, capsys):
    store = client.app.state.store
    for sid in ("a", "b", "c", "existing"):
        conversation(store, sid, "Failure" if sid == "b" else "Analyze the data")
    conversation(store, "background", identity_kind="unattributed_trace")
    original = {"category": "writing", "reason": "Existing", "model": "external"}
    store.classify("existing", original)
    calls = []

    def complete(self, messages, **kwargs):
        calls.append(messages)
        if "Failure" in messages[-1]["content"]:
            raise ModelServiceError("Model service rejected the request (HTTP 503)")
        return {"text": '{"category":"analysis","reason":"Analyzes data."}',
                "model": "test-llm", "provider": "openai-compatible", "dummy": False}

    monkeypatch.setattr(ModelGateway, "complete", complete)
    monkeypatch.setenv("AGENTBOARD_FEATURES", "classification")
    monkeypatch.setattr(sys, "argv", ["agentboard", "--database", store.path, "classify", "--all"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr()
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert [(r["session_id"], r["status"]) for r in rows] == [
        ("a", "classified"), ("b", "error"), ("c", "classified"),
    ]
    assert len(calls) == 3
    assert store.get_session("existing")["classification"] == original
    assert "2 classified, 0 skipped, 1 failed" in output.err
    assert store.classification_candidates() == ["b"]


def test_cli_force_limit_and_argument_validation(client, monkeypatch, capsys):
    store = client.app.state.store
    for sid in ("a", "b"):
        conversation(store, sid)
        store.classify(sid, {"category": "writing", "reason": "Old", "model": "test"})
    monkeypatch.setenv("AGENTBOARD_MODEL_MODE", "dummy")
    monkeypatch.setenv("AGENTBOARD_FEATURES", "classification")
    base = ["agentboard", "--database", store.path, "classify"]
    monkeypatch.setattr(sys, "argv", base + ["a"])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"
    monkeypatch.setattr(sys, "argv", base + ["--all", "--force", "--limit", "1"])
    main()
    assert json.loads(capsys.readouterr().out)["classification"]["category"] == "analysis"
    assert store.get_session("b")["classification"]["category"] == "writing"
    for args in ([], ["--all", "a"], ["--all", "--limit", "0"], ["a", "--limit", "2"]):
        monkeypatch.setattr(sys, "argv", base + args)
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
    monkeypatch.setenv("AGENTBOARD_FEATURES", "")
    monkeypatch.setattr(sys, "argv", base + ["--all"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
