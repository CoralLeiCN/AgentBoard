import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentboard.domain import Event, Session
from agentboard.store import Store
from agentboard.timestamps import format_timestamp, normalize_timestamp, timestamp_ns


@pytest.mark.parametrize(
    "source,canonical,nanos",
    [
        ("1970-01-01T00:00:00Z", "1970-01-01T00:00:00.000000000Z", 0),
        ("2026-09-06T09:51:24.032+01:00", "2026-09-06T08:51:24.032000000Z", 1788684684032000000),
        ("2023-11-14t16:43:20.000000001-05:30", "2023-11-14T22:13:20.000000001Z", 1700000000000000001),
        ("1970-01-01T01:00:00.123456789+01:00", "1970-01-01T00:00:00.123456789Z", 123456789),
        ("1970-01-01T00:00:00z", "1970-01-01T00:00:00.000000000Z", 0),
    ],
)
def test_exact_rfc3339_normalization(source, canonical, nanos):
    assert normalize_timestamp(source) == canonical
    assert timestamp_ns(source) == nanos
    assert format_timestamp(nanos) == canonical
    assert Session(id="s", started_at=source).model_dump()["started_at"] == canonical


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-06",
        "2026-09-06T01:00:00",
        "2026-09-06 01:00:00Z",
        "2026-09-06T01:00:00-00:00",
        "2026-09-06T01:00:00+00:60",
        "2026-09-06T01:00:00+24:00",
        "2026-02-30T00:00:00Z",
        "2026-09-06T01:00:60Z",
        "2026-09-06T01:00:00.1234567891Z",
        "1969-12-31T23:59:59Z",
        1788684684032000000,
    ],
)
def test_unsupported_timestamp_is_rejected_without_rounding(value):
    with pytest.raises(ValueError):
        Session(id="s", started_at=value)


def test_interval_validation_after_offset_normalization():
    kwargs = dict(id="e", session_id="s", sequence=1, kind="llm", name="response")
    event = Event(**kwargs, start_time="2026-09-06T01:00:00+01:00", end_time="2026-09-06T00:00:00Z")
    assert event.start_time == event.end_time
    with pytest.raises(ValueError, match="precedes"):
        Event(
            **kwargs, start_time="2026-09-06T00:00:00.000000002Z", end_time="2026-09-06T00:00:00.000000001Z"
        )


# Frozen schema from the previous release: migration tests must not derive this
# layout from the current schema, which would hide incompatible schema changes.
LEGACY_SCHEMA = """
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, agent TEXT NOT NULL, title TEXT NOT NULL,
 started_ns INTEGER NOT NULL, metadata TEXT NOT NULL, classification TEXT
);
CREATE INDEX sessions_started ON sessions(started_ns DESC,id);
CREATE TABLE events (
 row_id INTEGER PRIMARY KEY,id TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id),
 sequence INTEGER NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,start_ns INTEGER NOT NULL,end_ns INTEGER,
 timing TEXT NOT NULL,source TEXT NOT NULL,turn_id TEXT,trace_id TEXT,span_id TEXT,parent_span_id TEXT,
 status TEXT NOT NULL,text TEXT NOT NULL,attributes TEXT NOT NULL,UNIQUE(session_id,id)
);
CREATE INDEX events_time ON events(session_id,start_ns);
PRAGMA user_version=1;
"""


def legacy_db(path, bad=False):
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_SCHEMA)
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
            (
                "s",
                "codex",
                "Keep title",
                0,
                '{"cwd":"/project"}',
                '{"category":"coding","model":"reviewer"}',
            ),
        )
        for row_id, eid, start, end in [(7, "first", 0, 5), (11, "overlap", 3, 7), (42, "open", 8, None)]:
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id,
                    eid,
                    "s",
                    row_id,
                    "tool",
                    "exec",
                    start,
                    "invalid" if bad else end,
                    "estimated",
                    "codex_jsonl",
                    "turn",
                    "trace",
                    "span",
                    "parent",
                    "ok",
                    "echo hi",
                    '{"call_id":"call"}',
                ),
            )


def test_v1_migration_preserves_ids_precision_cursor_and_updates(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_db(path)
    # Simultaneous app/import startup must migrate once, under the same lock.
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: Store(str(path)), range(4)))
    store = stores[0]
    session = store.get_session("s")
    assert session["started_at"] == "1970-01-01T00:00:00.000000000Z"
    assert session["metadata"] == {"cwd": "/project"}
    assert session["classification"] == {"category": "coding", "model": "reviewer"}
    first = store.events("s", limit=1)
    assert first["next_cursor"] == 7
    event = first["items"][0]
    assert event["end_time"] == "1970-01-01T00:00:00.000000005Z"
    assert event["parent_span_id"] == "parent" and event["attributes"]["call_id"] == "call"
    assert [e["row_id"] for e in store.events("s", after=7)["items"]] == [11, 42]
    stats = store.stats("s")
    assert stats["elapsed_ms"] == 0.000008
    assert stats["timing"][0]["sum_ms"] == 0.000009
    assert stats["timing"][0]["active_ms"] == 0.000007
    incoming = Event(**{k: v for k, v in store.event("s", "open").items() if k != "row_id"})
    incoming.end_time = format_timestamp(10)
    assert store.ingest([Session(id="s", started_at=format_timestamp(3)), incoming])["inserted_events"] == 0
    assert store.event("s", "open")["end_time"] == format_timestamp(10)
    assert store.get_session("s") == session
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            dict((r["name"], r["type"]) for r in db.execute("PRAGMA table_info(events)"))["start_time"]
            == "TEXT"
        )
    assert Store(str(path)).event("s", "first") == event


def test_failed_migration_rolls_back_schema_and_data(tmp_path):
    path = tmp_path / "invalid.db"
    legacy_db(path, bad=True)
    with pytest.raises(sqlite3.OperationalError):
        Store(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 3
        assert db.execute("SELECT end_ns FROM events WHERE row_id=7").fetchone()[0] == "invalid"
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_v2'").fetchall()


def test_rfc3339_ordering_and_null_durations(tmp_path):
    store = Store(str(tmp_path / "order.db"))
    store.ingest(
        [
            Session(id="later", started_at="2026-09-06T00:00:00.000000002Z"),
            Session(id="earlier", started_at="2026-09-06T01:00:00.000000001+01:00"),
            Event(
                id="open",
                session_id="earlier",
                sequence=0,
                kind="tool",
                name="exec",
                start_time="2026-09-06T00:00:00.000000001Z",
            ),
        ]
    )
    assert [s["id"] for s in store.list_sessions()["items"]] == ["later", "earlier"]
    assert store.stats("earlier")["timing"][0]["active_ms"] is None
    assert store.stats("earlier")["timing"][0]["sum_ms"] is None
