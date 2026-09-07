import json

import httpx
import pytest

from agentboard.classification import classification_schema
from agentboard.config import Settings
from agentboard.models import ModelGateway, ModelServiceError


def mock_responses(monkeypatch, body, status=200):
    import openai

    requests = []
    real_client = openai.OpenAI

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "served-model"}]})
        return httpx.Response(status, json=body)

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ))
    return requests


def response_body(**overrides):
    return {
        "id": "resp-test", "object": "response", "created_at": 0, "model": "served-model",
        "status": "completed", "error": None, "incomplete_details": None,
        "output": [{"id": "msg-test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "A model answer.", "annotations": []}]}],
        **overrides,
    }


def test_responses_wire_format_and_discovery(monkeypatch):
    requests = mock_responses(monkeypatch, response_body())
    messages = [{"role": "system", "content": "Classify the transcript."},
                {"role": "user", "content": "Write a letter."}]
    result = ModelGateway(Settings(model_mode="local", model_api="responses")).complete(messages)
    assert result["text"] == "A model answer."
    assert result["api"] == "responses" and result["model"] == "served-model"
    assert result["dummy"] is False
    assert [r.url.path for r in requests] == ["/v1/models", "/v1/responses"]
    body = json.loads(requests[-1].content)
    assert body == {"model": "served-model", "input": messages, "max_output_tokens": 2048, "store": False}


def test_responses_classification_sends_shared_strict_schema(client, imported, monkeypatch):
    output = response_body()
    output["output"][0]["content"][0]["text"] = '{"category":"coding","reason":"Implements software."}'
    requests = mock_responses(monkeypatch, output)
    client.app.state.settings.model_mode = "local"
    client.app.state.settings.model_api = "responses"
    result = client.post(f"/api/v1/sessions/{imported}/classify")
    assert result.status_code == 200, result.text
    body = json.loads(requests[-1].content)
    assert body["text"]["format"] == {"type": "json_schema", "name": "session_purpose",
                                       "strict": True, "schema": classification_schema()}
    assert result.json()["category"] == "coding"


@pytest.mark.parametrize("overrides", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    {"status": "failed", "error": {"code": "server_error", "message": "failure"}},
    {"output": []},
    {"output": [{"type": "message", "role": "assistant", "content": [
        {"type": "refusal", "refusal": "Cannot classify this input."},
        {"type": "output_text", "text": '{"category":"coding","reason":"Partial output."}'},
    ]}]},
])
def test_responses_failures_do_not_overwrite_or_fall_back(client, imported, monkeypatch, overrides):
    mock_responses(monkeypatch, response_body(**overrides))
    client.app.state.settings.model_mode = "auto"
    client.app.state.settings.model_api = "responses"
    original = {"category": "coding", "reason": "Previously reviewed", "model": "test"}
    client.app.state.store.classify(imported, original)
    result = client.post(f"/api/v1/sessions/{imported}/classify")
    assert result.status_code == 502, result.text
    assert client.app.state.store.get_session(imported)["classification"] == original


@pytest.mark.parametrize("status", [401, 404, 500])
def test_responses_http_errors_are_visible(monkeypatch, status):
    requests = mock_responses(monkeypatch, {"error": {"message": "rejected"}}, status)
    with pytest.raises(ModelServiceError, match=str(status)):
        ModelGateway(Settings(model_mode="auto", model_api="responses")).complete(
            [{"role": "user", "content": "Test"}],
        )
    assert requests[-1].url.path == "/v1/responses"


def test_responses_settings_from_environment_and_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBOARD_MODEL_API", "responses")
    monkeypatch.setenv("AGENTBOARD_MODEL_TIMEOUT_SECONDS", "90")
    assert Settings().model_api == "responses"
    assert Settings().model_timeout_seconds == 90
    path = tmp_path / "settings.toml"
    path.write_text('model_api = "chat_completions"\nmodel_timeout_seconds = 75\n')
    assert Settings.from_file(path).model_api == "chat_completions"
    assert Settings.from_file(path).model_timeout_seconds == 75
    with pytest.raises(ValueError, match="Model API"):
        ModelGateway(Settings(model_api="invalid")).complete([])
    with pytest.raises(ValueError, match="timeout"):
        ModelGateway(Settings(model_timeout_seconds=0)).complete([])


def test_configured_timeout_reaches_model_request(monkeypatch):
    requests = mock_responses(monkeypatch, response_body())
    ModelGateway(Settings(model_mode="local", model_api="responses", model_timeout_seconds=75)).complete(
        [{"role": "user", "content": "Hello"}],
    )
    assert requests[-1].extensions["timeout"]["read"] == 75
