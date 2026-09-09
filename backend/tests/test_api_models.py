import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings
from agentboard.domain import Event, Session
from agentboard.models import ModelGateway
from agentboard.resume import execute_plan


def test_pagination_filter_and_export(client, imported):
    path = f"/api/v1/sessions/{imported}"
    first = client.get(path + "/events?limit=3").json()
    second = client.get(path + f"/events?limit=3&after={first['next_cursor']}").json()
    assert not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]}
    filtered = client.get(path + "/inputs?q=regression").json()["items"]
    assert len(filtered) == 1 and filtered[0]["kind"] == "user"
    export = client.get(path + "/export")
    assert "application/x-ndjson" in export.headers["content-type"]
    assert len([json.loads(line) for line in export.text.splitlines()]) == 24
    assert client.get(path + "/events?limit=1001").status_code == 422
    assert client.get("/api/v1/sessions/missing/events").status_code == 404
    prompts = client.get(path + "/export?kind=user")
    assert len(prompts.text.splitlines()) == 2
    assert all(json.loads(line)["kind"] == "user" for line in prompts.text.splitlines())


def test_timeline_filters_before_pagination(client):
    store = client.app.state.store
    sid = "sparse-timeline"
    start = "2026-09-06T10:00:00Z"
    records = [Session(id=sid, started_at=start)]
    for index in range(205):
        records.append(Event(id=f"log-{index}", session_id=sid, sequence=index,
                             kind="event", name="transport", source="otlp_trace", start_time=start))
    for index, kind in enumerate(("tool", "llm", "user_wait"), start=205):
        records.append(Event(id=kind, session_id=sid, sequence=index, kind=kind,
                             name="operation", source="otlp_trace", start_time=start))
    store.ingest(records)
    path = f"/api/v1/sessions/{sid}/events"
    assert all(e["kind"] == "event" for e in client.get(path).json()["items"])
    first = client.get(path, params={"timeline": True, "limit": 2, "source": "otlp_trace"}).json()
    assert [e["kind"] for e in first["items"]] == ["tool", "llm"]
    second = client.get(path, params={"timeline": True, "limit": 2, "after": first["next_cursor"]}).json()
    assert [e["kind"] for e in second["items"]] == ["user_wait"]
    assert second["next_cursor"] is None
    for filters in ({"q": "transport"}, {"source": "codex_jsonl"}, {"kind": "user"}):
        assert client.get(path, params={"timeline": True, **filters}).json()["items"] == []


def test_dummy_classification_is_labeled_and_filterable(client, imported):
    result = client.post(f"/api/v1/sessions/{imported}/classify").json()
    assert result["dummy"] is True and result["category"] == "bug-fixing"
    assert result["content_origin"] == "inferred"
    assert client.get("/api/v1/sessions?category=bug-fixing").json()["total"] == 1
    assert client.get("/api/v1/sessions?category=writing").json()["total"] == 0


def test_external_classification(client, imported):
    path = f"/api/v1/sessions/{imported}/classification"
    assert (
        client.put(path, json={"category": "invalid", "reason": "test", "model": "external"}).status_code
        == 422
    )
    result = client.put(
        path, json={"category": "coding", "reason": "Reviewed tool calls", "model": "external-reviewer"}
    )
    assert result.json()["provider"] == "external"
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"]["model"] == "external-reviewer"


def test_replay_truncates_and_preserves_source(client, imported):
    store = client.app.state.store
    original = list(store.export(imported))
    inputs = [e for e in original if e["kind"] == "user"]
    captured = []

    def complete(messages, **kwargs):
        captured.extend(messages)
        return {"text": "Changed direction", "model": "test-model", "provider": "test", "dummy": False}

    client.app.state.gateway.complete = complete
    result = client.post(
        f"/api/v1/sessions/{imported}/replay",
        json={"input_id": inputs[1]["id"], "replacement": "Write a summary instead."},
    )
    assert result.status_code == 200, result.text
    assert captured[-1] == {"role": "user", "content": "Write a summary instead."}
    assert inputs[1]["text"] not in str(captured)
    assert "All 24 tests pass" not in str(captured)
    assert "Recorded tool call" in str(captured)
    assert list(store.export(imported)) == original
    branch = store.get_session(result.json()["session_id"])
    assert branch["metadata"]["parent_session_id"] == imported
    answer = list(store.export(branch["id"]))[-1]
    assert answer["attributes"]["content_origin"] == "model_generated"


def test_replay_first_prompt_has_no_previous_context(client, imported):
    selected = client.get(f"/api/v1/sessions/{imported}/inputs").json()["items"][0]
    result = client.post(
        f"/api/v1/sessions/{imported}/replay",
        json={"input_id": selected["id"], "replacement": "New first input"},
    )
    sid = result.json()["session_id"]
    events = client.get(f"/api/v1/sessions/{sid}/events").json()["items"]
    assert [e["text"] for e in events if e["kind"] == "user"] == ["New first input"]
    assert result.json()["dummy"] is True
    assert events[-1]["attributes"]["content_origin"] == "inferred"


def test_replay_validation_and_context_limit(client, imported):
    all_events = client.get(f"/api/v1/sessions/{imported}/events").json()["items"]
    tool = next(e for e in all_events if e["kind"] == "tool")
    user = [e for e in all_events if e["kind"] == "user"][1]
    path = f"/api/v1/sessions/{imported}/replay"
    assert client.post(path, json={"input_id": tool["id"], "replacement": "x"}).status_code == 422
    assert client.post(path, json={"input_id": user["id"], "replacement": ""}).status_code == 422
    client.app.state.settings.max_model_chars = 20
    assert client.post(path, json={"input_id": user["id"], "replacement": "x"}).status_code == 422


def test_native_plan_uses_previous_completed_turn(client, imported):
    inputs = client.get(f"/api/v1/sessions/{imported}/inputs").json()["items"]
    result = client.post(
        f"/api/v1/sessions/{imported}/codex-plan",
        json={"input_id": inputs[1]["id"], "replacement": "Inspect only"},
    ).json()
    assert result["branch"]["params"]["lastTurnId"] == "turn-001"
    assert result["branch"]["params"]["sandbox"] == "read-only"
    calls = []

    class FakeRPC:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def call(self, method, params):
            calls.append((method, params))
            return (
                {"thread": {"id": "new-branch"}} if method == "thread/fork" else {"turn": {"id": "new-turn"}}
            )

        def wait_turn(self, sid, tid):
            assert (sid, tid) == ("new-branch", "new-turn")
            return {"id": tid, "status": "completed"}

    executed = execute_plan(result, "/tmp", FakeRPC)
    assert executed["thread_id"] == "new-branch"
    assert calls[1][1]["threadId"] == "new-branch"  # never append to original
    first = client.post(
        f"/api/v1/sessions/{imported}/codex-plan",
        json={"input_id": inputs[0]["id"], "replacement": "Start over"},
    ).json()
    assert first["branch"]["method"] == "thread/start"


def test_features_auth_and_same_origin(tmp_path):
    app = create_app(Settings(database=str(tmp_path / "restricted.db"), features=set(), api_token="secret"))
    with TestClient(app) as c:
        assert c.get("/api/v1/sessions").status_code == 401
        c.headers["Authorization"] = "Bearer secret"
        assert c.get("/api/v1/sessions").status_code == 200
        assert c.post("/api/v1/sessions/any/classify").status_code == 404
        assert (
            c.post("/api/v1/sessions/any/replay", json={"input_id": "x", "replacement": "x"}).status_code
            == 404
        )
        assert c.get("/api/v1/sessions", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert c.get("/").status_code == 200
        assert "AgentBoard" in c.get("/static/app.js").text
        assert c.get("/", headers={"Host": "attacker.example"}).status_code == 400



def test_openai_client_local_service(monkeypatch):
    import openai

    real_client = openai.OpenAI
    captured = []

    def handler(request):
        captured.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "local-test", "object": "model", "created": 0, "owned_by": "local"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "completion",
                "object": "chat.completion",
                "created": 0,
                "model": "local-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: real_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )
    result = ModelGateway(Settings(model_mode="local")).complete([{"role": "user", "content": "hello"}])
    assert not result["dummy"] and result["model"] == "local-test"
    assert [str(r.url) for r in captured] == [
        "http://localhost:30000/v1/models",
        "http://localhost:30000/v1/chat/completions",
    ]
    assert json.loads(captured[1].content)["messages"][0]["content"] == "hello"


def test_model_connection_fallback_is_labeled(monkeypatch):
    import openai

    real_client = openai.OpenAI

    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: real_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )
    result = ModelGateway(Settings()).complete([{"role": "user", "content": "hi"}])
    assert result["dummy"] and result["fallback_reason"] == "Local model service is unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        ModelGateway(Settings(model_mode="local")).complete([{"role": "user", "content": "hi"}])


def test_model_auth_errors_are_not_dummy_results(monkeypatch):
    import openai

    from agentboard.models import ModelServiceError

    real_client = openai.OpenAI
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: real_client(
            **kwargs,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(401, json={"error": {"message": "unauthorized"}})
                )
            ),
        ),
    )
    with pytest.raises(ModelServiceError, match="401"):
        ModelGateway(Settings()).complete([{"role": "user", "content": "hi"}])


def test_invalid_model_classification_does_not_persist(client, imported):
    client.app.state.gateway.complete = lambda *a, **k: {"text": "not-json", "model": "test", "dummy": False}
    assert client.post(f"/api/v1/sessions/{imported}/classify").status_code == 422
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"] is None


def test_replay_preserves_input_request_answers(client):
    from test_codex import parse, records, wait_record

    rows = [
        records()[0],
        wait_record(1, "user_message", message="Make a plan"),
        wait_record(
            2,
            "function_call",
            name="functions.request_user_input",
            call_id="question",
            arguments='{"question":"Which color?"}',
        ),
        wait_record(5, "function_call_output", call_id="question", output="Choose blue"),
        wait_record(6, "task_complete"),
        wait_record(9, "user_message", message="Build it"),
    ]
    store = client.app.state.store
    store.ingest(parse(rows))
    sid = rows[0]["payload"]["id"]
    selected = client.get(f"/api/v1/sessions/{sid}/inputs").json()["items"][-1]
    captured = []

    def complete(messages, **kwargs):
        captured.extend(messages)
        return {"text": "Done", "model": "test", "provider": "test", "dummy": False}

    client.app.state.gateway.complete = complete
    response = client.post(
        f"/api/v1/sessions/{sid}/replay", json={"input_id": selected["id"], "replacement": "Build a mockup"}
    )
    assert response.status_code == 200, response.text
    assert "Choose blue" in str(captured)
    assert "Between turns" not in str(captured)
