import copy
import json

from agentboard.otlp import session_identity

TRACE = 'ab' * 16
SID = '01a076c1-3143-7a82-b136-b349b0bc481c'


def attr(key, value):
    return {'key': key, 'value': {'intValue' if isinstance(value, int) else 'stringValue': str(value)}}


def span(index, sid=None, nested=False):
    attrs = [attr('thread.id', 14)]
    result = {'traceId': TRACE, 'spanId': f'{index:016x}', 'name': 'startup_prewarm',
              'startTimeUnixNano': '1700000000000000000', 'endTimeUnixNano': '1700000000984000000',
              'attributes': attrs}
    if sid:
        if nested:
            result['events'] = [{'name': 'startup', 'timeUnixNano': '1700000000000000001',
                                 'attributes': [attr('conversation.id', sid)]}]
        else:
            attrs.append(attr('thread.id', sid))
    return result


def post(client, *spans):
    payload = {'resourceSpans': [{'resource': {'attributes': [attr('service.name', 'codex-app-server')]},
                                 'scopeSpans': [{'spans': list(spans)}]}]}
    result = client.post('/v1/traces', json=payload)
    assert result.status_code == 200, result.text


def events(client, sid):
    return client.get(f'/api/v1/sessions/{sid}/events').json()['items']


def test_worker_id_is_not_a_conversation_and_nested_identity_is_used():
    for worker in (14, '14'):
        raw = span(1)
        raw['attributes'] = [attr('thread.id', worker)]
        assert session_identity({}, {}, raw)[0] is None
    assert session_identity({}, {}, span(1, SID))[0] == SID
    assert session_identity({}, {}, span(1, SID, nested=True))[0] == SID
    assert session_identity({}, {'attributes': [attr('conversation.id', SID)]}, span(1))[0] == SID


def test_late_identity_reassociates_earlier_spans_and_retries_are_idempotent(client):
    original = span(1)
    post(client, original)
    first = events(client, TRACE)[0]
    assert client.get('/api/v1/sessions/14').status_code == 404
    post(client, span(2, SID, nested=True))
    recovered = events(client, SID)
    assert len(recovered) == 2
    assert recovered[0]['id'] == first['id'] and recovered[0]['row_id'] == first['row_id']
    assert recovered[0]['attributes']['otel']['record'] == original
    assert recovered[0]['attributes']['agentboard_session_association']['basis'] == (
        'unique conversation identity in trace'
    )
    post(client, original)
    assert len(events(client, SID)) == 2
    assert client.get(f'/api/v1/sessions/{TRACE}').status_code == 404
    # A retry with changed contents must not alter correlation from the preserved first record.
    changed = copy.deepcopy(original)
    changed['attributes'].append(attr('conversation.id', 'conflicting-retry'))
    post(client, changed)
    assert len(events(client, SID)) == 2


def test_conflicting_trace_identities_revoke_inference_without_moving_direct_spans(client):
    post(client, span(1), span(2, SID, nested=True))
    post(client, span(3, 'another-session', nested=True))
    assert len(events(client, SID)) == len(events(client, 'another-session')) == 1
    assert len(events(client, TRACE)) == 1
    assert events(client, TRACE)[0]['attributes']['agentboard_session_association']['basis'] == (
        'unattributed trace'
    )


def test_repair_preserves_raw_records_ids_and_cursors(client):
    post(client, span(1), span(2, SID, nested=True))
    store = client.app.state.store
    before = events(client, SID)
    with store.connect() as db:
        db.execute("INSERT INTO sessions(id,agent,title,started_at,metadata,classification) VALUES('14','codex','Untitled session',?,? ,NULL)",
                   (before[0]['start_time'], json.dumps({'service.name': 'codex-app-server'})))
        db.execute("UPDATE events SET session_id='14'")
        db.execute('DELETE FROM otlp_session_evidence')
    assert store.repair_otlp_sessions()['reassigned_events'] == 2
    after = events(client, SID)
    for a, b in zip(before, after):
        assert (a['id'], a['row_id'], a['attributes']['otel']) == (b['id'], b['row_id'], b['attributes']['otel'])
    assert client.get('/api/v1/sessions/14').status_code == 404
    assert store.repair_otlp_sessions()['reassigned_events'] == 0


def test_internal_measured_spans_are_visible_in_timeline_without_changing_tool_totals(client):
    post(client, span(1, SID))
    path = f'/api/v1/sessions/{SID}'
    page = client.get(path + '/events?timeline=true').json()
    assert len(page['items']) == 1 and page['items'][0]['kind'] == 'event'
    stats = client.get(path + '/stats').json()
    assert stats['timeline_count'] == 1 and stats['timing'] == []
    assert stats['counts'] == {'event': 1}
    assert client.get(path + '/parallel-groups').json()['items'] == []


def test_session_list_counts_conversations_separately_from_unattributed_traces(client):
    post(client, span(1))
    ordinary = client.get('/api/v1/sessions').json()
    assert ordinary['total'] == 0 and ordinary['items'] == []
    assert ordinary['identity_counts'] == {'unattributed_trace': 1}
    unknown = client.get('/api/v1/sessions?identity_kind=unattributed').json()
    assert unknown['total'] == 1 and unknown['items'][0]['identity_kind'] == 'unattributed_trace'
    assert client.get(f'/api/v1/sessions/{TRACE}/events').json()['items']  # Evidence remains accessible.
    post(client, span(2, SID))
    assert client.get('/api/v1/sessions').json()['total'] == 1
    assert client.get('/api/v1/sessions?identity_kind=unattributed').json()['total'] == 0
    assert client.get('/api/v1/sessions?identity_kind=all').json()['total'] == 1
    assert client.get('/api/v1/sessions?identity_kind=invalid').status_code == 422


def test_codex_thread_id_alias_and_multiple_traces_share_one_session(client):
    first = span(1)
    first['attributes'].append(attr('thread_id', SID))
    second = span(2, SID)
    second['traceId'] = 'cd' * 16
    post(client, first, second)
    assert client.get('/api/v1/sessions').json()['total'] == 1
    assert len(events(client, SID)) == 2
    assert events(client, SID)[0]['attributes']['otel']['record']['attributes'] == first['attributes']


def test_mixed_trace_uses_nearest_identified_ancestor_and_keeps_root_unattributed(client):
    a, b = span(1, SID), span(2, 'another-session', nested=True)
    child_a, child_b, root = span(3), span(4), span(5)
    a['parentSpanId'] = b['parentSpanId'] = root['spanId']
    child_a['parentSpanId'], child_b['parentSpanId'] = a['spanId'], b['spanId']
    post(client, root, a, b, child_a, child_b)
    assert len(events(client, SID)) == len(events(client, 'another-session')) == 2
    assert len(events(client, TRACE)) == 1
    for sid in (SID, 'another-session'):
        child = next(e for e in events(client, sid) if e['span_id'] in (child_a['spanId'], child_b['spanId']))
        assert child['attributes']['agentboard_session_association']['basis'] == 'ancestor span conversation identity'
    assert client.get('/api/v1/sessions').json()['total'] == 2


def test_hex_conversation_id_is_not_mistaken_for_unattributed_trace(client):
    raw = span(1)
    raw['attributes'].append(attr('conversation.id', TRACE))
    post(client, raw)
    assert client.get('/api/v1/sessions').json()['total'] == 1
    assert client.get(f'/api/v1/sessions/{TRACE}').json()['identity_kind'] == 'session'


def test_v4_upgrade_separates_trace_buckets_without_changing_events(client):
    from agentboard.store import Store
    post(client, span(1))
    store = client.app.state.store
    before = events(client, TRACE)
    with store.connect() as db:
        db.execute('DROP INDEX sessions_identity')
        db.execute('ALTER TABLE sessions DROP COLUMN identity_kind')
        db.execute('PRAGMA user_version=4')
    upgraded = Store(store.path)
    assert upgraded.list_sessions()['total'] == 0
    assert upgraded.list_sessions(identity_kind='unattributed')['total'] == 1
    assert upgraded.events(TRACE)['items'] == before
