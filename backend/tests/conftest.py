from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings

FIXTURE = Path(__file__).parents[2] / "examples/fixtures/codex-session.jsonl"


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
