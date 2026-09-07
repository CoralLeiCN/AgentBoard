import httpx
import pytest

from scripts.private_endpoint import PRIVATE_BASE_URL, endpoint_config, validate_base_url

PREFIX = "AGENTBOARD_E2E_CODEX"


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "http://localhost:30000/v1", "https://example.test/v1",
    PRIVATE_BASE_URL + "?redirect=1", PRIVATE_BASE_URL + "/responses",
    "http://192.168.1.220:30000@api.openai.com/v1",
])
def test_live_tests_reject_other_providers(url):
    with pytest.raises(ValueError, match="private endpoint"):
        validate_base_url(url)


@pytest.fixture
def clean_endpoint(monkeypatch):
    for suffix in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"{PREFIX}_{suffix}", raising=False)


def test_explicit_model_needs_no_discovery(clean_endpoint, monkeypatch):
    monkeypatch.setenv(f"{PREFIX}_MODEL", "lan-model")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Unexpected discovery"))
    endpoint = endpoint_config(PREFIX)
    assert (endpoint.base_url, endpoint.model, endpoint.api_key) == (PRIVATE_BASE_URL, "lan-model", "")


@pytest.mark.parametrize("model_ids", [["local-one"], [], ["one", "two"]])
def test_model_discovery_requires_an_unambiguous_private_model(clean_endpoint, monkeypatch, model_ids):
    original = httpx.Client

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def respond(request):
            assert str(request.url) == PRIVATE_BASE_URL + "/models"
            return httpx.Response(200, json={"data": [{"id": model} for model in model_ids]})
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    if len(model_ids) == 1:
        assert endpoint_config(PREFIX).model == "local-one"
    else:
        with pytest.raises(ValueError, match="explicitly"):
            endpoint_config(PREFIX)


def test_discovery_never_follows_redirects(clean_endpoint, monkeypatch):
    original = httpx.Client
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://api.openai.com/v1/models"})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    with pytest.raises(httpx.HTTPStatusError):
        endpoint_config(PREFIX)
    assert requests == [PRIVATE_BASE_URL + "/models"]
