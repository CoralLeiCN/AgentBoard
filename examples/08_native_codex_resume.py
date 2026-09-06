"""Produce a native Codex branch plan. Execution requires an actual locally stored Codex session.

agentboard resume SESSION_ID INPUT_ID --prompt 'New prompt' --cwd /path/to/repo --execute
"""

import json

from common import demo_session, events, request

sid = demo_session()
selected = list(events(sid, "inputs"))[1]
print(
    json.dumps(
        request(
            "POST",
            f"/api/v1/sessions/{sid}/codex-plan",
            json={"input_id": selected["id"], "replacement": "Only explain the test failure."},
        ).json(),
        indent=2,
    )
)
