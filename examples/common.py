"""All network examples use the same API as the browser."""

import os
from pathlib import Path

import httpx

BASE = os.getenv("AGENTBOARD_URL", "http://127.0.0.1:4318")
TOKEN = os.getenv("AGENTBOARD_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
FIXTURE = Path(__file__).parent / "fixtures" / "codex-session.jsonl"


def request(method, path, **kwargs):
    response = httpx.request(method, BASE + path, headers=HEADERS, timeout=60, **kwargs)
    response.raise_for_status()
    return response


def demo_session():
    return request("POST", "/api/v1/import/codex", content=FIXTURE.read_bytes()).json()["session_ids"][0]


def events(sid, suffix="events", **params):
    after = 0
    while True:
        page = request("GET", f"/api/v1/sessions/{sid}/{suffix}", params={**params, "after": after}).json()
        yield from page["items"]
        if page["next_cursor"] is None:
            return
        after = page["next_cursor"]
