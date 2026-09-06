"""Regression coverage for the reviewed, redacted excerpt of a real Codex rollout."""

import hashlib
import json
import re
from pathlib import Path

from agentboard.timestamps import timestamp_ns

FIXTURE = Path(__file__).parents[2] / 'examples/fixtures/codex-real-excerpt.jsonl'


def test_real_excerpt_excludes_private_metadata_and_identifiers():
    records = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    manifest = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
    assert 'source_prefix_sha256' not in manifest
    assert 'source_path' not in manifest
    forbidden = {'rate_limits', 'credits', 'timezone', 'base_instructions', 'developer_instructions',
                 'sandbox_policy', 'permission_profile', 'file_system_sandbox_policy',
                 'active_permission_profile', 'collaboration_mode', 'api_key', 'password',
                 'access_token', 'refresh_token', 'authorization'}

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for key, child in value.items():
                if (key == 'id' or key.endswith('_id')) and isinstance(child, str):
                    assert child == 'real-codex-excerpt' or re.fullmatch(r'example-id-\d+', child)
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, str):
            assert not re.search(r'/Users/|/home/(?!example(?:/|$))', value)
            assert not re.search(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', value)
            assert not re.search(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value, re.I)
            assert not re.search(r'-----BEGIN .*PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_|AKIA)', value)

    check(records)


def test_real_excerpt_preserves_archived_records_and_direct_event_mapping(client):
    body = FIXTURE.read_bytes()
    records = [json.loads(line) for line in body.splitlines()]
    provenance = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
    assert hashlib.sha256(body).hexdigest() == provenance['sha256']
    assert len(records) == provenance['record_count'] == 13
    assert [record['ordinal'] + 1 for record in records] == [
        line['original_line'] for line in provenance['lines']]
    assert records[0]['payload']['cli_version'] == provenance['source_cli_version'] == '0.153.4'
    assert records[0]['payload']['agentboard_fixture']['kind'] == 'redacted_real_excerpt'
    response = client.post('/api/v1/import/codex', content=body)
    assert response.status_code == 200, response.text
    base = '/api/v1/sessions/real-codex-excerpt'
    assert client.get(base + '/raw').content == body
    events = client.get(base + '/events').json()['items']
    assert len(events) == 9
    call = next(event for event in events if event['name'] == 'exec')
    evidence = client.get(base + f'/events/{call["id"]}/raw').json()
    assert [line['line_number'] for line in evidence['lines']] == [8, 12]
    assert records[7]['payload']['call_id'] == records[11]['payload']['call_id']
    assert timestamp_ns(call['end_time']) - timestamp_ns(call['start_time']) == 136000000
    items = [event for event in events if event['source'] == 'codex_item']
    assert len(items) == 3
    for event in items:
        evidence = client.get(base + f'/events/{event["id"]}/raw').json()
        assert [line['line_number'] for line in evidence['lines']] == [event['sequence']]
        assert json.loads(evidence['lines'][0]['text'])['payload']['type'] == 'item_completed'
    command = next(event for event in items if event['sequence'] == 10)
    assert timestamp_ns(command['end_time']) - timestamp_ns(command['start_time']) == 3125
    # Unmapped source variants remain in the archive without fabricated normalized events.
    assert records[8]['type'] == 'token_usage_record'
    assert not any(event['sequence'] == 9 for event in events)
    for event in events:
        if event['source'] == 'codex_jsonl' and event['kind'] == 'llm':
            assert client.get(base + f'/events/{event["id"]}/raw').json()['lines'] == []
