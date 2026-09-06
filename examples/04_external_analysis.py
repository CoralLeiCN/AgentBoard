"""Consume a streaming export with bounded memory, just as an external analysis service would."""

import json
from collections import Counter

import httpx
from common import BASE, HEADERS, demo_session

sid = demo_session()
counts = Counter()
with httpx.stream("GET", f"{BASE}/api/v1/sessions/{sid}/export", headers=HEADERS) as response:
    response.raise_for_status()
    for line in response.iter_lines():
        counts[json.loads(line)["kind"]] += 1
print(dict(counts))
