"""SQLite adapter. SQL is kept here so parsers and the HTTP contract can survive a storage migration."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import RawLine, RawTraceEnd, Session
from .otlp import session_identity
from .parallel import NOTE as PARALLEL_NOTE
from .parallel import parallel_groups
from .timestamps import format_timestamp, timestamp_ns

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, agent TEXT NOT NULL, title TEXT NOT NULL,
 started_at TEXT NOT NULL, metadata TEXT NOT NULL, classification TEXT,
 identity_kind TEXT NOT NULL DEFAULT 'session'
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at DESC, id);
CREATE TABLE IF NOT EXISTS events (
 row_id INTEGER PRIMARY KEY, id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
 sequence INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
 start_time TEXT NOT NULL, end_time TEXT, timing TEXT NOT NULL, source TEXT NOT NULL,
 turn_id TEXT, trace_id TEXT, span_id TEXT, parent_span_id TEXT,
 status TEXT NOT NULL, text TEXT NOT NULL, attributes TEXT NOT NULL,
 UNIQUE(session_id, id)
);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, sequence, row_id);
CREATE INDEX IF NOT EXISTS events_kind ON events(session_id, kind, sequence, row_id);
CREATE INDEX IF NOT EXISTS events_time ON events(session_id, start_time);
CREATE INDEX IF NOT EXISTS events_otel_identity ON events(source, trace_id, span_id);
CREATE INDEX IF NOT EXISTS events_external_id ON events(id);
CREATE TABLE IF NOT EXISTS otlp_session_evidence (
 event_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, direct_session_id TEXT, evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS otlp_evidence_trace ON otlp_session_evidence(trace_id);
CREATE INDEX IF NOT EXISTS otlp_evidence_session ON otlp_session_evidence(direct_session_id);
CREATE TABLE IF NOT EXISTS raw_imports (
 id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
 sha256 TEXT, mapping_version TEXT, byte_count INTEGER, line_count INTEGER,
 imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(session_id, sha256, mapping_version)
);
CREATE TABLE IF NOT EXISTS raw_lines (
 import_id INTEGER NOT NULL REFERENCES raw_imports(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, content BLOB NOT NULL,
 PRIMARY KEY(import_id, sequence)
);
CREATE TABLE IF NOT EXISTS event_raw_sources (
 event_row_id INTEGER PRIMARY KEY REFERENCES events(row_id) ON DELETE CASCADE,
 import_id INTEGER NOT NULL REFERENCES raw_imports(id) ON DELETE CASCADE,
 line_numbers TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_identity ON sessions(identity_kind,started_at DESC,id);
PRAGMA user_version = 6;
"""


class Store:
    def __init__(self, path: str):
        if path == ":memory:":
            raise ValueError("Use a file-backed SQLite database; connections are scoped per operation")
        self.path = path
        Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # Changing journal mode may return BUSY immediately despite busy_timeout
            # when several processes first open a legacy database concurrently.
            deadline = time.monotonic() + 5
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if (getattr(exc, "sqlite_errorcode", 0) & 255) not in (
                        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED
                    ) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.025)
            # The version check and migration share a write lock, including on concurrent startup.
            # Do not use executescript: it commits before running, breaking atomic migration.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6):
                raise ValueError("Unsupported database schema version")
            if version == 1:
                self._migrate_timestamps(db)
            if version in (2, 3, 4):
                columns = {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}
                if "identity_kind" not in columns:
                    db.execute("ALTER TABLE sessions ADD COLUMN identity_kind TEXT NOT NULL DEFAULT 'session'")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            if version < 5:
                # Distinguish legacy trace buckets using recorded relationships, not ID shape.
                db.execute("""UPDATE sessions SET identity_kind='unattributed_trace'
                    WHERE id IN (SELECT session_id FROM events GROUP BY session_id HAVING
                        count(*)=sum(source='otlp_trace' AND session_id=trace_id))
                    AND NOT EXISTS (SELECT 1 FROM otlp_session_evidence WHERE direct_session_id=sessions.id)
                    AND NOT EXISTS (SELECT 1 FROM raw_imports WHERE session_id=sessions.id)""")

    @staticmethod
    def _migrate_timestamps(db):
        """Replace v1 integer columns atomically, preserving row IDs and related data."""
        db.create_function("rfc3339", 1, lambda value: format_timestamp(value) if value is not None else None)
        for statement in SCHEMA.split(";"):
            if statement.strip().startswith(
                ("CREATE TABLE IF NOT EXISTS sessions", "CREATE TABLE IF NOT EXISTS events")
            ):
                db.execute(statement.replace("sessions", "sessions_v2").replace("events", "events_v2"))
        db.execute("""INSERT INTO sessions_v2(id,agent,title,started_at,metadata,classification)
            SELECT id,agent,title,rfc3339(started_ns),metadata,classification FROM sessions""")
        db.execute("""INSERT INTO events_v2
            SELECT row_id,id,session_id,sequence,kind,name,rfc3339(start_ns),rfc3339(end_ns),
            timing,source,turn_id,trace_id,span_id,parent_span_id,status,text,attributes FROM events""")
        db.execute("DROP TABLE events")
        db.execute("DROP TABLE sessions")
        db.execute("ALTER TABLE sessions_v2 RENAME TO sessions")
        db.execute("ALTER TABLE events_v2 RENAME TO events")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=NORMAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, items):
        """One transaction per import/batch; iteration keeps raw JSONL out of memory."""
        sessions = set()
        otlp_traces = set()
        inserted = 0
        raw_id = None
        raw_import_ids = []
        digest = hashlib.sha256()
        byte_count = line_count = 0
        with self.connect() as db:
            for item in items:
                if isinstance(item, RawLine):
                    if raw_id is None:
                        raw_id = db.execute("INSERT INTO raw_imports DEFAULT VALUES").lastrowid
                    content = item.text.encode("utf-8")
                    digest.update(content)
                    byte_count += len(content)
                    line_count += 1
                    db.execute("INSERT INTO raw_lines VALUES(?,?,?)", (raw_id, item.sequence, content))
                elif isinstance(item, RawTraceEnd):
                    previous = db.execute(
                        "SELECT id FROM raw_imports WHERE session_id=? AND sha256=? AND mapping_version=?",
                        (item.session_id, digest.hexdigest(), item.mapping_version),
                    ).fetchone()
                    if previous:
                        db.execute("UPDATE event_raw_sources SET import_id=? WHERE import_id=?",
                                   (previous["id"], raw_id))
                        db.execute("DELETE FROM raw_imports WHERE id=?", (raw_id,))
                        raw_import_ids.append(previous["id"])
                    else:
                        db.execute(
                            """UPDATE raw_imports SET session_id=?, sha256=?, mapping_version=?,
                            byte_count=?, line_count=? WHERE id=?""",
                            (
                                item.session_id,
                                digest.hexdigest(),
                                item.mapping_version,
                                byte_count,
                                line_count,
                                raw_id,
                            ),
                        )
                        raw_import_ids.append(raw_id)
                    raw_id = None
                    digest = hashlib.sha256()
                    byte_count = line_count = 0
                elif isinstance(item, Session):
                    sessions.add(item.id)
                    db.execute(
                        """INSERT INTO sessions(id,agent,title,started_at,metadata,identity_kind) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                        title=CASE WHEN excluded.title='Untitled session' THEN sessions.title ELSE excluded.title END,
                        agent=CASE WHEN excluded.agent='codex' THEN 'codex' ELSE sessions.agent END,
                        started_at=min(sessions.started_at,excluded.started_at),
                        identity_kind=CASE WHEN excluded.identity_kind='session' THEN 'session'
                            ELSE sessions.identity_kind END,
                        metadata=json_patch(sessions.metadata,excluded.metadata)""",
                        (item.id, item.agent, item.title, item.started_at, json.dumps(item.metadata), item.identity_kind),
                    )
                else:
                    d = item.model_dump()
                    if item.source == "otlp_trace" and "otel" in item.attributes:
                        # OTLP span identity survives correction of its session association.
                        prior = db.execute(
                            "SELECT session_id,attributes FROM events WHERE source=? AND trace_id=? AND span_id=?",
                            (item.source, item.trace_id, item.span_id),
                        ).fetchone()
                        if prior:
                            d["session_id"] = prior["session_id"]
                        self._index_otlp_identity(db, {
                            **d, "attributes": json.loads(prior["attributes"]) if prior else d["attributes"]
                        })
                        otlp_traces.add(item.trace_id)
                    d["attributes"] = json.dumps(d["attributes"])
                    keys = ",".join(d)
                    placeholders = ",".join("?" for _ in d)
                    cursor = db.execute(
                        f"INSERT OR IGNORE INTO events({keys}) VALUES({placeholders})", tuple(d.values())
                    )
                    inserted += cursor.rowcount
                    updated = False
                    # Reimporting older records moves recognized input requests out of tool time.
                    if cursor.rowcount == 0 and item.kind == "user_wait":
                        updated = db.execute(
                            """UPDATE events SET kind=?, attributes=json_patch(attributes,?)
                            WHERE session_id=? AND id=? AND kind='tool'""",
                            (item.kind, d["attributes"], item.session_id, item.id),
                        ).rowcount > 0
                    # Growing rollouts can complete a previously unfinished tool call.
                    if cursor.rowcount == 0 and item.end_time is not None:
                        completed = db.execute(
                            """UPDATE events SET end_time=?, timing=?, status=?, attributes=?
                            WHERE session_id=? AND id=? AND end_time IS NULL""",
                            (
                                item.end_time,
                                item.timing,
                                item.status,
                                d["attributes"],
                                item.session_id,
                                item.id,
                            ),
                        ).rowcount > 0
                        updated = updated or completed
                    if raw_id is not None and item.raw_line_numbers:
                        stored = self.event_row(db.execute(
                            "SELECT * FROM events WHERE session_id=? AND id=?",
                            (item.session_id, item.id),
                        ).fetchone())
                        row_id = stored.pop("row_id")
                        if updated:
                            # A partial update may combine old content with new timing.
                            # Do not claim a single archive explains that hybrid event.
                            db.execute("DELETE FROM event_raw_sources WHERE event_row_id=?", (row_id,))
                        if stored == item.model_dump():
                            db.execute(
                                """INSERT INTO event_raw_sources VALUES(?,?,?)
                                ON CONFLICT(event_row_id) DO NOTHING""",
                                (row_id, raw_id, json.dumps(sorted(set(item.raw_line_numbers)))),
                            )
            if raw_id is not None:
                raise ValueError("Incomplete raw trace import")
            if otlp_traces:
                self._reconcile_otlp(db, otlp_traces)
                self._remove_empty_otel_sessions(db, sessions)
                sessions = {r[0] for trace in otlp_traces for r in db.execute(
                    "SELECT DISTINCT session_id FROM events WHERE source='otlp_trace' AND trace_id=?",
                    (trace,),
                )} | {sid for sid in sessions if db.execute(
                    "SELECT 1 FROM sessions WHERE id=?", (sid,)
                ).fetchone()}
        return {
            "session_ids": sorted(sessions),
            "inserted_events": inserted,
            "raw_import_ids": raw_import_ids,
        }

    @staticmethod
    def _index_otlp_identity(db, event):
        raw = event["attributes"]["otel"]
        direct, evidence = session_identity(raw.get("resource", {}), raw.get("scope", {}), raw["record"])
        db.execute("""INSERT OR REPLACE INTO otlp_session_evidence VALUES(?,?,?,?)""",
                   (event["id"], event["trace_id"], direct, json.dumps(evidence)))

    @staticmethod
    def _remove_empty_otel_sessions(db, ids):
        # Only discard generated, empty shells; retain user metadata, labels and raw archives.
        for sid in ids:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if (row and row["title"] == "Untitled session" and not row["classification"]
                    and set(json.loads(row["metadata"])) <= {"service.name"}):
                db.execute("""DELETE FROM sessions WHERE id=?
                    AND NOT EXISTS(SELECT 1 FROM events WHERE session_id=?)
                    AND NOT EXISTS(SELECT 1 FROM raw_imports WHERE session_id=?)""", (sid, sid, sid))

    @classmethod
    def _reconcile_otlp(cls, db, traces):
        changed = 0
        old_sessions = set()
        for trace in traces:
            rows = db.execute("""SELECT e.row_id,e.id,e.session_id,e.start_time,s.agent,
                    e.span_id,e.parent_span_id,i.direct_session_id,i.evidence
                FROM otlp_session_evidence i JOIN events e ON e.id=i.event_id
                JOIN sessions s ON s.id=e.session_id
                WHERE i.trace_id=? AND e.source='otlp_trace' AND e.trace_id=?""", (trace, trace)).fetchall()
            candidates = {sid for row in rows for sid in json.loads(row["evidence"])["candidates"]}
            inferred = next(iter(candidates)) if len(candidates) == 1 else None
            by_span = {r["span_id"]: r for r in rows}
            owners = {}

            def ancestor_owner(span_id):
                path, seen, owner = [], set(), None
                while span_id and span_id in by_span and span_id not in seen:
                    if span_id in owners:
                        owner = owners[span_id]
                        break
                    seen.add(span_id)
                    path.append(span_id)
                    parent_row = by_span[span_id]
                    if parent_row["direct_session_id"]:
                        owner = parent_row["direct_session_id"]
                        break
                    span_id = parent_row["parent_span_id"]
                for visited in path:
                    owners[visited] = owner
                return owner

            for row in rows:
                ancestor = ancestor_owner(row["parent_span_id"])
                target = row["direct_session_id"] or ancestor or inferred or trace
                basis = (json.loads(row["evidence"])["basis"] if row["direct_session_id"] else
                         "ancestor span conversation identity" if ancestor else
                         "unique conversation identity in trace" if inferred else "unattributed trace")
                # Store the association separately; decoded OTLP evidence stays intact.
                association = {"method": "otlp-session-v3", "basis": basis,
                               "session_id": target, "trace_id": trace}
                if row["session_id"] != target:
                    identity_kind = "unattributed_trace" if basis == "unattributed trace" else "session"
                    db.execute("""INSERT INTO sessions(id,agent,title,started_at,metadata,identity_kind)
                        VALUES(?,?,'Untitled session',?,'{}',?) ON CONFLICT(id) DO UPDATE SET
                        started_at=min(sessions.started_at,excluded.started_at),
                        identity_kind=excluded.identity_kind""",
                               (target, row["agent"], row["start_time"], identity_kind))
                    association["previous_session_id"] = row["session_id"]
                    db.execute("""UPDATE events SET session_id=?,
                        attributes=json_set(attributes,'$.agentboard_session_association',json(?))
                        WHERE row_id=?""", (target, json.dumps(association), row["row_id"]))
                    old_sessions.add(row["session_id"])
                    changed += 1
        cls._remove_empty_otel_sessions(db, old_sessions)
        return changed

    def repair_otlp_sessions(self):
        """Rebuild correlation from preserved decoded spans; retain IDs and row cursors."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            traces = set()
            for row in db.execute("SELECT id,trace_id,attributes FROM events WHERE source='otlp_trace'"):
                event = dict(row)
                event["attributes"] = json.loads(event["attributes"])
                if "otel" not in event["attributes"] or not event["trace_id"]:
                    continue
                self._index_otlp_identity(db, event)
                traces.add(event["trace_id"])
            return {"traces": len(traces), "reassigned_events": self._reconcile_otlp(db, traces)}

    def raw_imports(self, sid):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM raw_imports WHERE session_id=? ORDER BY id DESC", (sid,))
            ]

    def raw_import(self, sid, import_id=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM raw_imports WHERE session_id=? AND (? IS NULL OR id=?) ORDER BY id DESC LIMIT 1",
                (sid, import_id, import_id),
            ).fetchone()
            if row is None:
                raise KeyError("Raw trace unavailable; reimport the original rollout")
            return dict(row)

    def export_raw(self, sid, import_id=None):
        archive = self.raw_import(sid, import_id)
        with self.connect() as db:
            for row in db.execute(
                "SELECT content FROM raw_lines WHERE import_id=? ORDER BY sequence", (archive["id"],)
            ):
                yield row["content"]

    def event_raw(self, sid, event_id):
        """Return exact source lines bound to this stored event, never the latest archive by guesswork."""
        with self.connect() as db:
            db.execute("BEGIN")
            event = db.execute("SELECT * FROM events WHERE session_id=? AND id=?", (sid, event_id)).fetchone()
            if event is None:
                raise KeyError("Event not found")
            attrs = json.loads(event["attributes"])
            if event["source"] == "codex_jsonl" and (
                event["kind"] == "llm"
                or event["kind"] == "user_wait" and attrs.get("wait_type") == "between_turns"
            ):
                # Also exclude boundary links saved by earlier importer versions.
                return {
                    "event_id": event_id, "available": False, "lines": [],
                    "reason": "No raw JSONL record: AgentBoard inferred this interval. "
                              "Records used only to calculate its duration are not shown.",
                }
            source = db.execute(
                """SELECT r.*, s.line_numbers FROM event_raw_sources s
                JOIN raw_imports r ON r.id=s.import_id WHERE s.event_row_id=?""", (event["row_id"],)
            ).fetchone()
            if source is None:
                reason = ("No verified source-line mapping. Reimport the original rollout to link matching events."
                          if event["source"] in ("codex_jsonl", "codex_item")
                          else "This event does not originate from a Codex JSONL file.")
                return {"event_id": event_id, "available": False, "reason": reason, "lines": []}
            archive = dict(source)
            numbers = json.loads(archive.pop("line_numbers"))
            lines = [{"line_number": row["sequence"], "text": row["content"].decode("utf-8")}
                     for row in db.execute(
                         """SELECT sequence,content FROM raw_lines WHERE import_id=?
                         AND sequence IN (SELECT value FROM json_each(?)) ORDER BY sequence""",
                         (archive["id"], json.dumps(numbers)),
                     )]
            return {"event_id": event_id, "available": True, "archive": archive, "lines": lines}

    @staticmethod
    def session_row(row):
        if row is None:
            raise KeyError("Session not found")
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"])
        d["classification"] = json.loads(d["classification"]) if d["classification"] else None
        return d

    @staticmethod
    def event_row(row):
        d = dict(row)
        d["attributes"] = json.loads(d["attributes"])
        return d

    def get_session(self, sid):
        with self.connect() as db:
            return self.session_row(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())

    def list_sessions(self, limit=50, offset=0, q="", category="", identity_kind="session"):
        where = "WHERE (title LIKE ? OR id LIKE ?)"
        args = [f"%{q}%", f"%{q}%"]
        if identity_kind == "unattributed":
            where += " AND identity_kind<>'session'"
        elif identity_kind == "session":
            where += " AND identity_kind='session'"
        elif identity_kind != "all":
            raise ValueError("Unknown identity kind")
        if category:
            where += " AND json_extract(classification,'$.category')=?"
            args.append(category)
        with self.connect() as db:
            db.execute("BEGIN")
            identity_counts = dict(db.execute("SELECT identity_kind,count(*) FROM sessions GROUP BY identity_kind"))
            total = db.execute(f"SELECT count(*) FROM sessions {where}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM sessions {where} ORDER BY started_at DESC,id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return {
            "items": [self.session_row(r) for r in rows],
            "total": total,
            "identity_counts": identity_counts,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def events(self, sid, limit=200, after=0, kind="", q="", source="", timeline=False):
        # row_id cursor remains stable when a live batch arrives or a rollout is reimported.
        where = "session_id=? AND row_id>?"
        args = [sid, after]
        if timeline:
            where += " AND (kind IN ('llm','tool','user_wait') OR end_time IS NOT NULL)"
        for column, value in (("kind", kind), ("source", source)):
            if value:
                where += f" AND {column}=?"
                args.append(value)
        if q:
            where += " AND (text LIKE ? OR name LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY row_id LIMIT ?", (*args, limit + 1)
            ).fetchall()
        return {
            "items": [self.event_row(r) for r in rows[:limit]],
            "next_cursor": rows[limit - 1]["row_id"] if len(rows) > limit else None,
        }

    def unified(self, sid, limit=200, after=0, kind="", q=""):
        from .unified import timeline

        self.get_session(sid)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE session_id=? AND source IN ('codex_jsonl','codex_item')",
                (sid,),
            )
            events = [self.event_row(row) for row in rows]
        return timeline(events, limit, after, kind, q)

    def export(self, sid, kind=""):
        after = 0
        while True:
            page = self.events(sid, 500, after, kind=kind)
            yield from page["items"]
            if page["next_cursor"] is None:
                return
            after = page["next_cursor"]

    def ordered_events(self, sid, before_sequence=None):
        """Bounded-memory history traversal, using the session/sequence index."""
        with self.connect() as db:
            sql = "SELECT * FROM events WHERE session_id=?"
            args = [sid]
            if before_sequence is not None:
                sql += " AND sequence<?"
                args.append(before_sequence)
            for row in db.execute(sql + " ORDER BY sequence,row_id", args):
                yield self.event_row(row)

    def event(self, sid, eid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM events WHERE session_id=? AND id=?", (sid, eid)).fetchone()
            if row is None:
                raise KeyError("Event not found")
            return self.event_row(row)

    def stats(self, sid, source=""):
        self.get_session(sid)
        where = "session_id=?" + (" AND source=?" if source else "")
        args = (sid, source) if source else (sid,)
        with self.connect() as db:
            counts = dict(db.execute(f"SELECT kind,count(*) FROM events WHERE {where} GROUP BY kind", args))
            sources = [r[0] for r in db.execute(f"SELECT DISTINCT source FROM events WHERE {where}", args)]
            bounds = db.execute(
                f"SELECT min(start_time),max(coalesce(end_time,start_time)) FROM events WHERE {where}", args
            ).fetchone()
            timeline_count = db.execute(
                f"""SELECT count(*) FROM events WHERE {where}
                    AND (kind IN ('llm','tool','user_wait') OR end_time IS NOT NULL)""", args
            ).fetchone()[0]
            timing = []
            # Separate sources and quality: never add log, trace and inferred duplicates together.
            groups = db.execute(
                f"""SELECT source,kind,timing,count(*)
                FROM events WHERE {where} AND kind IN ('llm','tool','user_wait') GROUP BY source,kind,timing""",
                args,
            ).fetchall()
            for origin, kind, quality, count in groups:
                intervals = db.execute(
                    """SELECT start_time,end_time FROM events
                    WHERE session_id=? AND source=? AND kind=? AND timing=? AND end_time IS NOT NULL
                    ORDER BY start_time,end_time""",
                    (sid, origin, kind, quality),
                )
                union, summed, start, end = 0, 0, None, None
                for a, b in intervals:
                    a, b = timestamp_ns(a), timestamp_ns(b)
                    summed += b - a
                    if start is None:
                        start, end = a, b
                    elif a > end:
                        union += end - start
                        start, end = a, b
                    else:
                        end = max(end, b)
                if start is not None:
                    union += end - start
                timing.append(
                    {
                        "source": origin,
                        "kind": kind,
                        "timing": quality,
                        "count": count,
                        "sum_ms": summed / 1e6 if start is not None else None,
                        "active_ms": union / 1e6 if start is not None else None,
                    }
                )
        return {
            "counts": counts,
            "timeline_count": timeline_count,
            "sources": sources,
            "elapsed_ms": (timestamp_ns(bounds[1]) - timestamp_ns(bounds[0])) / 1e6
            if bounds[0] is not None
            else 0,
            "timing": timing,
            "note": "Active time merges overlapping intervals within each group. Sources/qualities are not additive. Historical LLM time is an estimate, not model latency. User wait covers blocking input requests and inferred gaps between turns (which may include idle time); unrecorded approvals and asynchronous questions cannot be isolated. Unfinished waits have no duration.",
        }

    def parallel_groups(self, sid, source=""):
        self.get_session(sid)
        with self.connect() as db:
            rows = db.execute(
                """SELECT id,session_id,kind,source,timing,turn_id,trace_id,parent_span_id,start_time,end_time
                FROM events WHERE session_id=? AND kind='tool' AND end_time>start_time
                AND (?='' OR source=?)""",
                (sid, source, source),
            )
            groups = parallel_groups([dict(row) for row in rows])
        return {"items": groups, "scope": "full_session_source", "note": PARALLEL_NOTE}

    def classify(self, sid, result):
        self.get_session(sid)
        with self.connect() as db:
            db.execute("UPDATE sessions SET classification=? WHERE id=?", (json.dumps(result), sid))
