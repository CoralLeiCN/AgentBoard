import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings
from scripts.private_endpoint import endpoint_config

FIXTURE = Path(__file__).parents[2] / "examples/fixtures/codex-session.jsonl"


def pytest_addoption(parser):
    parser.addoption("--run-private-e2e", action="store_true", help="Run live tests against the private LAN model")


@pytest.fixture
def private_endpoint(request, monkeypatch):
    def load(prefix):
        configured = any(os.getenv(f"{prefix}_{suffix}") for suffix in ("BASE_URL", "MODEL", "API_KEY"))
        if not request.config.getoption("--run-private-e2e") and not configured:
            pytest.skip("Pass --run-private-e2e to use the private model endpoint")
        # Neither the SDK nor the child Codex process may inherit a hosted proxy/key.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
                     "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            monkeypatch.delenv(name, raising=False)
        try:
            return endpoint_config(prefix)
        except (ValueError, httpx.HTTPError) as exc:
            pytest.fail(f"Private endpoint setup failed ({type(exc).__name__}); "
                        "check the LAN service, endpoint URL, and model ID. No fallback was attempted.", pytrace=False)
    return load


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(database=str(tmp_path / "test.db"), model_mode="dummy"))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def imported(client):
    r = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
    assert r.status_code == 200, r.text
    return r.json()["session_ids"][0]
