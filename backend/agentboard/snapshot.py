"""Make an explicit, consistent SQLite snapshot without opening the source as a Store."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def snapshot_database(source, destination):
    source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    if source == destination:
        raise ValueError("Snapshot source and destination must differ")
    if not source.is_file():
        raise ValueError("Snapshot source does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = destination.with_suffix(destination.suffix + ".snapshot.json")
    if manifest.exists():
        raise ValueError("Snapshot manifest already exists; choose a new destination")
    try:
        destination.touch(mode=0o600, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("Snapshot destination already exists; choose a new destination") from exc
    try:
        src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
                counts = {table: dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                          for table in ("sessions", "events", "raw_imports", "raw_lines")}
            finally:
                dst.close()
        finally:
            src.close()
        with destination.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        record = {"source": str(source), "database": str(destination),
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "sha256_at_creation": digest, "counts_at_creation": counts}
        with manifest.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
        return {**record, "manifest": str(manifest)}
    except Exception:
        # Only this invocation's new snapshot may be removed on failure.
        destination.unlink(missing_ok=True)
        raise
