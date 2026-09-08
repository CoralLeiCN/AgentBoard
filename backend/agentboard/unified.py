"""Read-only Codex timeline composition; identity matches never use timestamp proximity."""

from collections import Counter, defaultdict
from copy import deepcopy

from .timestamps import timestamp_ns

NOTE = (
    "Historical records and Codex item timings share one timeline. Only unique, same-turn tool identities "
    "are merged; unmatched records remain separate and may describe related work. Timing totals stay "
    "separate by source and quality. Item LLM timing measures output streaming, not full response latency."
)


def compose(events):
    events = deepcopy(events)
    calls = defaultdict(list)
    candidates = defaultdict(list)
    for e in events:
        if e['source'] == 'codex_jsonl' and e['kind'] in ('tool', 'user_wait'):
            call_id = e['attributes'].get('call_id')
            if call_id and e.get('turn_id'):
                calls[(e['session_id'], e['turn_id'], call_id)].append(e)
    for e in events:
        if e['source'] != 'codex_item' or e['kind'] not in ('tool', 'user_wait'):
            continue
        item = e['attributes'].get('item', {})
        # Item IDs can directly identify the call. Prefer an explicit call_id
        # when present; never strip prefixes or match names/nearby timestamps.
        call_id = item.get('call_id') or item.get('id')
        key = (e['session_id'], e.get('turn_id'), call_id)
        if call_id and e.get('turn_id'):
            candidates[key].append(e)
    removed = set()
    for e in events:
        if e['source'] != 'codex_item' or e['kind'] not in ('tool', 'user_wait'):
            continue
        item = e['attributes'].get('item', {})
        key = (e['session_id'], e.get('turn_id'), item.get('call_id') or item.get('id'))
        matches = calls.get(key, [])
        parent_key = (e['session_id'], e.get('turn_id'), item.get('parent_call_id'))
        parents = calls.get(parent_key, []) if item.get('parent_call_id') else []
        e['unified'] = {'match_status': 'unmatched', 'source_event_ids': [e['id']]}
        if len(parents) == 1:
            e['unified'].update(match_status='child', parent_event_id=parents[0]['id'],
                                basis='Explicit item.parent_call_id matches a unique same-turn rollout call_id')
        elif (len(matches) == 1 and len(candidates[key]) == 1
              and matches[0]['kind'] == e['kind'] and e['end_time'] is not None):
            original = matches[0]
            e['unified'] = {
                'match_status': 'matched',
                'basis': 'Unique same-turn rollout call_id equals item.call_id or item.id',
                'source_event_ids': [original['id'], e['id']],
                'records': [deepcopy(original), {k: deepcopy(v) for k, v in e.items() if k != 'unified'}],
            }
            e['text'] = original['text'] or e['text']
            removed.add(original['id'])
        elif matches:
            e['unified']['match_status'] = 'ambiguous'
    result = [e for e in events if e['id'] not in removed]
    replacements = {old_id: e['id'] for e in result
                    if e.get('unified', {}).get('match_status') == 'matched'
                    for old_id in e['unified']['source_event_ids']}
    for e in result:
        if e['kind'] in ('tool', 'user_wait'):
            e.setdefault('unified', {'match_status': 'unmatched', 'source_event_ids': [e['id']]})
            parent = e['unified'].get('parent_event_id')
            if parent in replacements:
                e['unified']['parent_event_id'] = replacements[parent]
    return sorted(result, key=lambda e: (e['start_time'], e['sequence'], e['id']))


def statistics(events):
    groups = defaultdict(list)
    for e in events:
        if e['kind'] in ('llm', 'tool', 'user_wait'):
            groups[(e['source'], e['kind'], e['timing'])].append(e)
    timing = []
    for (source, kind, quality), members in sorted(groups.items()):
        intervals = sorted((timestamp_ns(e['start_time']), timestamp_ns(e['end_time']))
                           for e in members if e['end_time'] is not None)
        union = summed = 0
        end = None
        for a, b in intervals:
            summed += b - a
            union += max(0, b - max(a, end if end is not None else a))
            end = max(end if end is not None else b, b)
        timing.append(dict(source=source, kind=kind, timing=quality, count=len(members),
                           active_ms=union / 1e6 if intervals else None,
                           sum_ms=summed / 1e6 if intervals else None))
    return {
        'counts': dict(Counter(e['kind'] for e in events)),
        'sources': sorted({e['source'] for e in events}),
        'timeline_count': len(events), 'timing': timing,
        'elapsed_ms': (max(timestamp_ns(e['end_time'] or e['start_time']) for e in events)
                       - min(timestamp_ns(e['start_time']) for e in events)) / 1e6 if events else 0,
        'note': NOTE,
    }


def timeline(events, limit=200, after=0, kind='', q='', *, include_parallel=True):
    composed = compose(events)
    parallel = None
    if include_parallel:
        from .parallel import NOTE as PARALLEL_NOTE
        from .parallel import parallel_groups

        groups = parallel_groups(composed)
        # The sources partition rollout parents from item children.
        for group in groups:
            group['display_label'] = ('Items ' if group['source'] == 'codex_item' else 'Rollout ') + group['label']
        parallel = {'items': groups, 'note': PARALLEL_NOTE, 'scope': 'full_unified_session'}
    filtered = [e for e in composed if (not kind or e['kind'] == kind)
                and (not q or q.casefold() in (e['name'] + '\n' + e['text']).casefold())]
    return {
        'items': filtered[after:after + limit],
        'next_cursor': after + limit if after + limit < len(filtered) else None,
        'stats': statistics(composed),
        'parallel': parallel,
        'note': NOTE,
    }
