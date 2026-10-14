"""Explicit live synthetic validation through the shared private endpoint policy."""

import json

import pytest

from dataset_privacy.codex import CodexConfig, CodexDetector
from dataset_privacy.pipeline import prepare


@pytest.mark.e2e
def test_codex_privacy_returns_valid_email_findings(private_endpoint, tmp_path):
    endpoint = private_endpoint("AGENTBOARD_E2E_CODEX")
    source = tmp_path / "synthetic-turns.json"
    source.write_text(
        json.dumps(
            [
                {
                    "index": 0,
                    "original_codex_session_id": "synthetic-session",
                    "previous_turn_index": None,
                    "messages": [
                        {"role": "user", "content": "Please email Alex Morgan at alex.morgan@example.test."}
                    ],
                }
            ]
        )
    )
    detector = CodexDetector(
        CodexConfig(
            endpoint.model,
            endpoint.base_url,
            api_key_env="AGENTBOARD_E2E_CODEX_API_KEY" if endpoint.api_key else None,
        )
    )
    output = tmp_path / "private-run"
    result = prepare(source, output, detector)
    assert result["status"] == "complete_draft"
    assert result["privacy_status"] == "needs_review"
    assert "alex.morgan@example.test" not in (output / "draft.jsonl").read_text()
    findings = json.loads((output / "findings.json").read_text())
    assert any(f["category"] == "EMAIL" for values in findings.values() for f in values)
