"""Event source links must identify exact archives, including after conflicting reimports."""

import json

from conftest import FIXTURE

from agentboard.domain import Event, Session
from agentboard.store import Store


def upload(client, rows):
    body = ''.join(json.dumps(row, ensure_ascii=False) + '\r\n' for row in rows)
    response = client.post('/api/v1/import/codex', content=body.encode())
    assert response.status_code == 200, response.text
    return response.json()['raw_import_ids'][0], body.splitlines(keepends=True)


def raw(client, sid, event):
    response = client.get(f'/api/v1/sessions/{sid}/events/{event["id"]}/raw')
    assert response.status_code == 200, response.text
    return response.json()


def test_source_lines_calls_and_delayed_prompts_exclude_inferred_boundaries(client):
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    # Remove prompt mirrors so event-only prompts are emitted later than their source line.
    rows = [row for row in rows if row['payload'].get('role') != 'user']
    archive, lines = upload(client, rows)
    sid = rows[0]['payload']['id']
    events = client.get(f'/api/v1/sessions/{sid}/events').json()['items']
    for event in events:
        evidence = raw(client, sid, event)
        if event['kind'] in ('llm', 'user_wait'):
            assert not evidence['available']
            assert evidence['lines'] == []
            assert 'inferred this interval' in evidence['reason']
            with client.app.state.store.connect() as db:
                assert not db.execute('SELECT 1 FROM event_raw_sources WHERE event_row_id=?',
                                      (event['row_id'],)).fetchone()
            continue
        assert evidence['available']
        assert evidence['archive']['id'] == archive
        for line in evidence['lines']:
            assert line['text'] == lines[line['line_number'] - 1]
        sources = [json.loads(line['text']) for line in evidence['lines']]
        if event['kind'] == 'user':
            assert len(sources) == 1
            assert sources[0]['payload']['message'] == event['text']
        elif event['kind'] == 'tool':
            assert len(sources) == 2
            assert sources[0]['payload']['call_id'] == sources[1]['payload']['call_id']
            assert sources[0]['payload']['type'] in ('function_call', 'custom_tool_call')
            assert sources[1]['payload']['type'] in ('function_call_output', 'custom_tool_call_output')
    assert client.get(f'/api/v1/sessions/other/events/{events[0]["id"]}/raw').status_code == 404
    assert client.get(f'/api/v1/sessions/{sid}/events/missing/raw').status_code == 404


def test_archive_binding_survives_conflict_repeat_and_shortening(client):
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    archive, lines = upload(client, rows)
    sid = rows[0]['payload']['id']
    user = client.get(f'/api/v1/sessions/{sid}/inputs').json()['items'][0]
    rows[user['sequence'] - 1]['payload']['content'][0]['text'] = 'Changed source'
    upload(client, rows)
    upload(client, rows[:1])
    source = raw(client, sid, user)
    assert source['archive']['id'] == archive
    assert source['lines'][0]['text'] == lines[user['sequence'] - 1]
    # Deduplicating the original archive must retain mappings to it.
    same, _ = upload(client, [json.loads(line) for line in lines])
    assert same == archive
    assert raw(client, sid, user) == source
    with client.app.state.store.connect() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_growing_tool_updates_links_but_hybrid_does_not_claim_evidence(client):
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    sid = rows[0]['payload']['id']
    first, _ = upload(client, rows[:7])
    tool = client.get(f'/api/v1/sessions/{sid}/events?kind=tool').json()['items'][0]
    assert len(raw(client, sid, tool)['lines']) == 1
    second, _ = upload(client, rows)
    evidence = raw(client, sid, tool)
    assert evidence['archive']['id'] == second != first
    assert len(evidence['lines']) == 2
    # Simulate a legacy incomplete call with different retained text.
    with client.app.state.store.connect() as db:
        db.execute("UPDATE events SET end_time=NULL,text='old content' WHERE id=?", (tool['id'],))
    upload(client, rows)
    assert not raw(client, sid, tool)['available']


def test_legacy_backfill_requires_matching_normalized_event(client):
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    upload(client, rows)
    sid = rows[0]['payload']['id']
    user = client.get(f'/api/v1/sessions/{sid}/inputs').json()['items'][0]
    store = client.app.state.store
    with store.connect() as db:
        db.execute('DROP TABLE event_raw_sources')
        db.execute('PRAGMA user_version=5')
    Store(store.path)
    assert not raw(client, sid, user)['available']
    upload(client, rows)
    assert raw(client, sid, user)['available']
    store.ingest([Session(id='replay', started_at=user['start_time']), Event(
        id='replayed', session_id='replay', sequence=0, kind='assistant', name='Replay',
        start_time=user['start_time'], source='replay')])
    assert 'does not originate' in raw(client, 'replay', {'id': 'replayed'})['reason']


def test_unified_sources_include_full_item_envelope(client):
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    call = rows[6]['payload']
    rows.append({'timestamp': '2026-01-15T10:02:00Z', 'type': 'event_msg', 'payload': {
        'type': 'item_completed', 'turn_id': 'turn-001', 'started_at_ms': 1768471200000,
        'completed_at_ms': 1768471201000,
        'item': {'type': 'CommandExecution', 'id': call['call_id'], 'command': 'test'}}})
    _, lines = upload(client, rows)
    sid = rows[0]['payload']['id']
    events = client.get(f'/api/v1/sessions/{sid}/unified').json()['items']
    merged = next(event for event in events if event.get('unified', {}).get('match_status') == 'matched')
    evidence = [raw(client, sid, {'id': eid}) for eid in merged['unified']['source_event_ids']]
    assert sorted(len(result['lines']) for result in evidence) == [1, 2]
    item = next(result for result in evidence if len(result['lines']) == 1)
    assert item['lines'][0]['text'] == lines[-1]


def test_existing_inferred_boundary_links_are_hidden_without_reimport(client, imported):
    events = client.get(f'/api/v1/sessions/{imported}/events').json()['items']
    store = client.app.state.store
    archive = store.raw_imports(imported)[0]['id']
    inferred = [event for event in events if event['kind'] in ('llm', 'user_wait')]
    assert {event['kind'] for event in inferred} == {'llm', 'user_wait'}
    for event in inferred:
        with store.connect() as db:
            db.execute('INSERT INTO event_raw_sources VALUES(?,?,?)',
                       (event['row_id'], archive, json.dumps([event['sequence']])))
        evidence = raw(client, imported, event)
        assert not evidence['available']
        assert evidence['lines'] == []
        assert 'inferred this interval' in evidence['reason']


def test_actual_llm_item_keeps_its_raw_record(client):
    rows = [json.loads(FIXTURE.read_text().splitlines()[0]), {
        'timestamp': '2026-09-06T00:00:01Z', 'type': 'event_msg', 'payload': {
            'type': 'item_completed', 'started_at_ms': 1788652800000,
            'completed_at_ms': 1788652801000, 'item': {'type': 'AgentMessage', 'id': 'message-1'},
        },
    }]
    _, lines = upload(client, rows)
    sid = rows[0]['payload']['id']
    event = client.get(f'/api/v1/sessions/{sid}/events').json()['items'][0]
    assert event['kind'] == 'llm' and event['source'] == 'codex_item'
    evidence = raw(client, sid, event)
    assert evidence['available']
    assert [line['text'] for line in evidence['lines']] == [lines[1]]
