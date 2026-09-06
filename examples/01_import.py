"""Import a synthetic historical Codex rollout. Run twice to see idempotent import."""

from common import FIXTURE, request

print(request("POST", "/api/v1/import/codex", content=FIXTURE.read_bytes()).json())
