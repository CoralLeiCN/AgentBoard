import gzip
import json

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def attr(key, value):
    return {"key": key, "value": {"stringValue": value}}


def trace_payload(sid="otel-session"):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [attr("service.name", "codex"), attr("session.id", sid)]},
                "scopeSpans": [
                    {
                        "scope": {"name": "test"},
                        "spans": [
                            {
                                "traceId": "ab" * 16,
                                "spanId": "cd" * 8,
                                "parentSpanId": "ef" * 8,
                                "name": "chat",
                                "startTimeUnixNano": "1700000000000000001",
                                "endTimeUnixNano": "1700000000123000001",
                                "attributes": [attr("gen_ai.operation.name", "chat")],
                                "events": [{"name": "first_token", "timeUnixNano": "1700000000030000001"}],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_otlp_json_preserves_ids_hierarchy_and_precision(client):
    body = trace_payload()
    response = client.post("/v1/traces", json=body)
    assert response.status_code == 200 and response.json() == {}
    e = client.get("/api/v1/sessions/otel-session/events").json()["items"][0]
    assert e["trace_id"] == "ab" * 16 and e["span_id"] == "cd" * 8 and e["parent_span_id"] == "ef" * 8
    assert e["start_time"] == "2023-11-14T22:13:20.000000001Z"
    assert e["attributes"]["otel"]["record"]["events"][0]["name"] == "first_token"
    assert e["kind"] == "llm" and e["timing"] == "measured"
    client.post("/v1/traces", json=body)
    assert len(client.get("/api/v1/sessions/otel-session/events").json()["items"]) == 1


@pytest.mark.parametrize("compressed", [False, True])
def test_real_protobuf_trace(client, compressed):
    payload = ExportTraceServiceRequest()
    rs = payload.resource_spans.add()
    rs.resource.attributes.add(key="session.id").value.string_value = "binary-session"
    span = rs.scope_spans.add().spans.add(
        trace_id=bytes.fromhex("11" * 16),
        span_id=bytes.fromhex("22" * 8),
        name="tool",
        start_time_unix_nano=1000000000,
        end_time_unix_nano=1100000000,
    )
    span.attributes.add(key="gen_ai.tool.name").value.string_value = "read_file"
    body = payload.SerializeToString()
    headers = {"Content-Type": "application/x-protobuf"}
    if compressed:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    result = client.post("/v1/traces", content=body, headers=headers)
    assert result.status_code == 200 and result.content == b""
    stats = client.get("/api/v1/sessions/binary-session/stats").json()
    assert stats["timing"][0]["active_ms"] == 100


def test_codex_logs_and_resource_correlation(client, imported):
    payload = ExportLogsServiceRequest()
    resource = payload.resource_logs.add()
    resource.resource.attributes.add(key="conversation.id").value.string_value = imported
    logs = resource.scope_logs.add()
    for name in ("codex.api_request", "codex.tool_result", "codex.sse_event"):
        record = logs.log_records.add(time_unix_nano=1788605000000000000)
        record.body.string_value = name
        record.attributes.add(key="duration_ms").value.int_value = 120
        record.attributes.add(key="success").value.bool_value = False
    result = client.post(
        "/v1/logs", content=payload.SerializeToString(), headers={"Content-Type": "application/x-protobuf"}
    )
    assert result.status_code == 200
    events = client.get(f"/api/v1/sessions/{imported}/events?source=otlp_log").json()["items"]
    assert [e["kind"] for e in events] == ["llm", "tool", "event"]  # SSE processing isn't full LLM latency
    assert all(e["status"] == "error" for e in events)
    groups = client.get(f"/api/v1/sessions/{imported}/stats").json()["timing"]
    assert {g["source"] for g in groups} == {"codex_jsonl", "otlp_log"}


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("severity,status", [(9, "ok"), (17, "error")])
def test_named_log_severity_preserves_records_and_accepts_batch(client, binary, severity, status):
    payload = ExportLogsServiceRequest()
    logs = payload.resource_logs.add().scope_logs.add()
    record = logs.log_records.add(time_unix_nano=1788605000000000000, severity_number=severity)
    record.attributes.add(key="conversation.id").value.string_value = "synthetic-severity"
    record.attributes.add(key="event.name").value.string_value = "codex.api_request"
    if binary:
        response = client.post("/v1/logs", content=payload.SerializeToString(),
                               headers={"Content-Type": "application/x-protobuf"})
    else:
        from google.protobuf.json_format import MessageToDict

        response = client.post("/v1/logs", json=MessageToDict(payload))
    assert response.status_code == 200, response.text
    event = client.get("/api/v1/sessions/synthetic-severity/events").json()["items"][0]
    assert event["kind"] == "llm" and event["status"] == status
    expected = "SEVERITY_NUMBER_INFO" if severity == 9 else "SEVERITY_NUMBER_ERROR"
    assert event["attributes"]["otel"]["record"]["severityNumber"] == expected


def test_codex_semantic_log_name_overrides_tracing_source_location(client):
    payload = ExportLogsServiceRequest()
    logs = payload.resource_logs.add().scope_logs.add()
    for name in ("codex.api_request", "codex.tool_result", "codex.sse_event"):
        record = logs.log_records.add(
            observed_time_unix_nano=1788605000000000000, severity_number=9,
            event_name="event otel/src/events/example.rs:10",
        )
        record.attributes.add(key="conversation.id").value.string_value = "synthetic-tracing-name"
        record.attributes.add(key="event.name").value.string_value = name
        record.attributes.add(key="duration_ms").value.string_value = "120"
    response = client.post("/v1/logs", content=payload.SerializeToString(),
                           headers={"Content-Type": "application/x-protobuf"})
    assert response.status_code == 200, response.text
    events = client.get("/api/v1/sessions/synthetic-tracing-name/events").json()["items"]
    assert [event["name"] for event in events] == ["codex.api_request", "codex.tool_result", "codex.sse_event"]
    assert [event["kind"] for event in events] == ["llm", "tool", "event"]
    assert [event["timing"] for event in events] == ["measured", "measured", "unknown"]
    assert all(event["attributes"]["otel"]["record"]["eventName"] ==
               "event otel/src/events/example.rs:10" for event in events)


@pytest.mark.parametrize("bad", ["", "AA==", "0" * 32, "g" * 32])
def test_invalid_trace_ids_reject_batch(client, bad):
    payload = trace_payload()
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] = bad
    result = client.post("/v1/traces", json=payload)
    assert result.status_code == 400
    assert client.get("/api/v1/sessions").json()["total"] == 0


def test_invalid_interval_rolls_back_entire_batch(client):
    payload = trace_payload()
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans.append({**spans[0], "spanId": "12" * 8, "endTimeUnixNano": "1"})
    assert client.post("/v1/traces", json=payload).status_code == 400
    assert client.get("/api/v1/sessions").json()["total"] == 0


def test_payload_limits_and_gzip_bomb(client):
    client.app.state.settings.max_body_bytes = 128
    assert client.post("/v1/traces", content=b"a" * 129).status_code == 415
    headers = {"Content-Type": "application/json"}
    assert client.post("/v1/traces", content=b"a" * 129, headers=headers).status_code == 413
    headers["Content-Encoding"] = "gzip"
    assert client.post("/v1/traces", content=gzip.compress(b"a" * 10000), headers=headers).status_code == 413
    assert client.post("/v1/traces", content=b"not gzip", headers=headers).status_code == 400


def test_gzip_json_and_empty_batch(client):
    result = client.post(
        "/v1/traces",
        content=gzip.compress(json.dumps(trace_payload()).encode()),
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert result.status_code == 200
    assert client.post("/v1/logs", json={}).json() == {}


def test_malformed_protobuf_json_schema_is_client_error(client):
    assert client.post("/v1/traces", json={"resourceSpans": "not-an-array"}).status_code == 400
    assert (
        client.post(
            "/v1/traces", content=b"bad", headers={"Content-Type": "application/x-protobuf"}
        ).status_code
        == 400
    )


def test_ingestion_backpressure_releases_capacity(client):
    slots = client.app.state.ingest_slots
    for _ in range(client.app.state.settings.ingest_concurrency):
        assert slots.acquire(blocking=False)
    try:
        r = client.post("/v1/traces", json={})
        assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    finally:
        for _ in range(client.app.state.settings.ingest_concurrency):
            slots.release()
    assert client.post("/v1/traces", json={}).status_code == 200


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("functions.request_user_input", "user_wait"),
        ("functions.request_user_input_async", "tool"),
    ],
)
def test_input_request_trace_classification(client, tool_name, expected):
    body = trace_payload()
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["name"] = "execute_tool"
    span["attributes"] = [attr("gen_ai.tool.name", tool_name)]
    assert client.post("/v1/traces", json=body).status_code == 200
    event = client.get("/api/v1/sessions/otel-session/events").json()["items"][0]
    assert event["kind"] == expected
    stats = client.get("/api/v1/sessions/otel-session/stats?source=otlp_trace").json()
    assert stats["timing"][0]["kind"] == expected
    assert stats["timing"][0]["active_ms"] == 123
    if expected == "user_wait":
        assert event["attributes"]["wait_type"] == "input_request"
        assert "tool" not in stats["counts"]


def test_input_request_log_duration(client):
    payload = ExportLogsServiceRequest()
    resource = payload.resource_logs.add()
    resource.resource.attributes.add(key="conversation.id").value.string_value = "input-log"
    record = resource.scope_logs.add().log_records.add(time_unix_nano=1788605000000000000)
    record.body.string_value = "codex.tool_result"
    record.attributes.add(key="tool_name").value.string_value = "request_user_input"
    record.attributes.add(key="duration_ms").value.int_value = 45000
    assert (
        client.post(
            "/v1/logs",
            content=payload.SerializeToString(),
            headers={"Content-Type": "application/x-protobuf"},
        ).status_code
        == 200
    )
    stats = client.get("/api/v1/sessions/input-log/stats").json()
    assert stats["counts"] == {"user_wait": 1}
    assert stats["timing"][0]["active_ms"] == 45000
    assert stats["timing"][0]["timing"] == "measured"


def test_input_request_trace_without_tool_attributes(client):
    body = trace_payload()
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["name"] = "functions.request_user_input"
    span["attributes"] = []
    assert client.post("/v1/traces", json=body).status_code == 200
    event = client.get("/api/v1/sessions/otel-session/events").json()["items"][0]
    assert event["kind"] == "user_wait"
