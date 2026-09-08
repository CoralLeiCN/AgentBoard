"""Core repository for original payloads, independent of normalization transactions."""

import hashlib
import json

CAPTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS captured_payloads (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, content_type TEXT NOT NULL,
 content_encoding TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
 payload BLOB NOT NULL,
 UNIQUE(source, content_type, content_encoding, sha256)
);
CREATE TABLE IF NOT EXISTS captures (
 id INTEGER PRIMARY KEY, payload_id INTEGER NOT NULL REFERENCES captured_payloads(id),
 captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','normalized','failed')),
 result TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS captures_payload ON captures(payload_id);
"""


class CaptureRepository:
    def __init__(self, connect):
        self._connect = connect

    def capture(self, stream, source, content_type, content_encoding="identity"):
        """The caller owns a stable seekable binary snapshot; copy in bounded chunks."""
        digest, size = hashlib.sha256(), 0
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        key = (source, content_type, content_encoding, digest.hexdigest())
        with self._connect() as db:
            # Original evidence must survive a power loss after acknowledgement.
            # Derived transactions can retain the Store's cheaper NORMAL policy.
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id FROM captured_payloads
                WHERE source=? AND content_type=? AND content_encoding=? AND sha256=?""", key).fetchone()
            if row:
                payload_id = row["id"]
            else:
                payload_id = db.execute("""INSERT INTO captured_payloads
                    (source,content_type,content_encoding,sha256,byte_count,payload)
                    VALUES(?,?,?,?,?,zeroblob(?))""", (*key, size, size)).lastrowid
                stream.seek(0)
                if size:
                    with db.blobopen("captured_payloads", "payload", payload_id) as blob:
                        while chunk := stream.read(1024 * 1024):
                            blob.write(chunk)
            capture_id = db.execute("INSERT INTO captures(payload_id) VALUES(?)", (payload_id,)).lastrowid
        stream.seek(0)
        return capture_id

    def finish(self, capture_id, *, result=None, error=None):
        with self._connect() as db:
            db.execute("UPDATE captures SET status=?,result=?,error=? WHERE id=?", (
                "failed" if error is not None else "normalized", json.dumps(result) if result is not None else None,
                error, capture_id,
            ))

    @staticmethod
    def _metadata(row):
        value = dict(row)
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def list(self, limit=50, after=0):
        with self._connect() as db:
            rows = db.execute("""SELECT c.*,p.source,p.content_type,p.content_encoding,p.sha256,p.byte_count
                FROM captures c JOIN captured_payloads p ON p.id=c.payload_id
                WHERE c.id>? ORDER BY c.id LIMIT ?""", (after, limit + 1)).fetchall()
        return {"items": [self._metadata(r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def get(self, capture_id):
        with self._connect() as db:
            row = db.execute("""SELECT c.*,p.source,p.content_type,p.content_encoding,p.sha256,p.byte_count
                FROM captures c JOIN captured_payloads p ON p.id=c.payload_id WHERE c.id=?""",
                             (capture_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown capture")
        return self._metadata(row)

    def export(self, capture_id):
        metadata = self.get(capture_id)
        if metadata["byte_count"]:
            with self._connect(stream=True) as db, db.blobopen(
                "captured_payloads", "payload", metadata["payload_id"], readonly=True,
            ) as blob:
                while chunk := blob.read(1024 * 1024):
                    yield chunk
