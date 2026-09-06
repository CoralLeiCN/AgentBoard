"""Synthetic identity cases; matching must not fabricate relationships from timing."""

from copy import deepcopy

from agentboard.domain import Event, Session
from agentboard.unified import compose, timeline


def event(eid, source='codex_jsonl', kind='tool', start=0, end=5, turn='turn', **attrs):
    return Event(id=eid, session_id='unified', sequence=start, kind=kind, name=eid,
                 start_time=f'2026-09-06T00:00:{start:02d}.000000000Z',
                 end_time=f'2026-09-06T00:00:{end:02d}.000000000Z' if end is not None else None,
                 turn_id=turn, source=source, timing='measured' if source == 'codex_item' else 'estimated',
                 text='call content' if source == 'codex_jsonl' else '', attributes=attrs).model_dump()


def test_exact_match_uses_item_timing_and_preserves_both_records():
    rows = [event('call', call_id='same'), event('item', 'codex_item', start=1, end=9, item={'id': 'same'})]
    before = deepcopy(rows)
    merged = compose(rows)
    assert rows == before and len(merged) == 1
    assert merged[0]['id'] == 'item' and merged[0]['end_time'] == rows[1]['end_time']
    assert merged[0]['text'] == rows[0]['text']
    assert merged[0]['unified']['records'] == rows
    assert merged[0]['unified']['source_event_ids'] == ['call', 'item']


def test_unmatched_ambiguous_and_cross_turn_records_are_not_collapsed():
    rows = [event('call', call_id='same'), event('one', 'codex_item', item={'id': 'same'}),
            event('two', 'codex_item', item={'id': 'same'}),
            event('other-turn', 'codex_item', turn='other', item={'id': 'same'}),
            event('unmatched', 'codex_item', item={'id': 'different'})]
    result = compose(rows)
    assert len(result) == 5
    assert {e['unified']['match_status'] for e in result} == {'ambiguous', 'unmatched'}
    assert len(compose([event('call', turn=None, call_id='same'),
                        event('item', 'codex_item', turn=None, item={'id': 'same'})])) == 2


def test_incomplete_item_does_not_replace_closed_rollout_estimate():
    rows = [event('call', call_id='same'), event('item', 'codex_item', end=None, item={'id': 'same'})]
    result = compose(rows)
    assert len(result) == 2 and result[0]['end_time'] == rows[0]['end_time']


def test_explicit_children_remain_separate_from_the_parent():
    rows = [event('parent', call_id='batch'),
            event('child-a', 'codex_item', item={'id': 'a', 'parent_call_id': 'batch'}),
            event('child-b', 'codex_item', item={'id': 'b', 'parent_call_id': 'batch'})]
    result = timeline(rows)
    children = [e for e in result['items'] if e['source'] == 'codex_item']
    assert len(result['items']) == 3
    assert all(e['unified']['parent_event_id'] == 'parent' for e in children)
    assert result['parallel']['items'][0]['event_ids'] == ['child-a', 'child-b']


def test_prompts_estimates_and_streaming_survive_with_separate_totals():
    rows = [event('prompt', kind='user', end=None), event('reply', kind='assistant', end=None),
            event('estimate', kind='llm'), event('streaming', 'codex_item', kind='llm', start=1, end=2)]
    result = timeline(rows)
    assert len(result['items']) == 4
    assert result['stats']['counts']['user'] == 1
    assert len(result['stats']['timing']) == 2
    assert {g['active_ms'] for g in result['stats']['timing']} == {5000, 1000}


def test_filter_and_pagination_follow_full_composition_and_grouping():
    rows = [event('call', call_id='a'), event('item-a', 'codex_item', item={'id': 'a'}),
            event('item-b', 'codex_item', start=1, end=3, item={'id': 'b'})]
    full = timeline(rows)
    first = timeline(rows, limit=1)
    second = timeline(rows, limit=1, after=first['next_cursor'])
    assert first['items'] + second['items'] == full['items']
    assert first['parallel'] == second['parallel'] == full['parallel']
    filtered = timeline(rows, q='item-b')
    assert len(filtered['items']) == 1 and filtered['parallel'] == full['parallel']
    assert full['stats']['counts']['tool'] == 2


def test_api_unified_is_read_only_and_source_views_remain_available(client):
    rows = [event('prompt', kind='user', end=None), event('call', call_id='same'),
            event('item', 'codex_item', item={'id': 'same'})]
    client.app.state.store.ingest([Session(id='unified', started_at=rows[0]['start_time'])]
                                  + [Event(**e) for e in rows])
    base = '/api/v1/sessions/unified'
    before = client.get(base + '/export').content
    response = client.get(base + '/unified?limit=1').json()
    assert len(response['items']) == 1 and response['next_cursor'] == 1
    inputs = client.get(base + '/unified?kind=user').json()
    assert [e['id'] for e in inputs['items']] == ['prompt']
    assert len(client.get(base + '/events?source=codex_jsonl').json()['items']) == 2
    assert client.get(base + '/export').content == before
    assert client.get('/api/v1/sessions/missing/unified').status_code == 404
