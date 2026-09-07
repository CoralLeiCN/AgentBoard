"""Shared, fail-closed policy for tests that actually call a model."""

import os
from dataclasses import dataclass

import httpx

PRIVATE_BASE_URL = "http://192.168.1.220:30000/v1"


def validate_base_url(value):
    value = value.strip().rstrip("/")
    if value != PRIVATE_BASE_URL:
        raise ValueError(f"Live tests must use the private endpoint {PRIVATE_BASE_URL}; no fallback is allowed")
    return value


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: str
    api_key: str


def endpoint_config(prefix):
    base_url = validate_base_url(os.getenv(f"{prefix}_BASE_URL") or PRIVATE_BASE_URL)
    model = os.getenv(f"{prefix}_MODEL", "").strip()
    api_key = os.getenv(f"{prefix}_API_KEY", "")
    if not model:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # A proxy or redirect must not turn local model discovery into a hosted request.
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            response = client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            models = response.json().get("data", [])
        ids = [item.get("id") for item in models if isinstance(item, dict)]
        if len(ids) != 1 or not isinstance(ids[0], str) or not ids[0].strip():
            raise ValueError(f"Set {prefix}_MODEL explicitly; /models must advertise exactly one model to auto-select")
        model = ids[0]
    return Endpoint(base_url, model, api_key)
