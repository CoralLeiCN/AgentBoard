"""Build a local shared baseline from explicitly selected, complete Codex rollouts."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agentboard.config import Settings
from agentboard.runtime import Runtime
from agentboard.snapshot import snapshot_database


def seed_database(paths, baseline, checkpoint):
    baseline, checkpoint = Path(baseline).resolve(), Path(checkpoint).resolve()
    paths = [Path(path).expanduser().resolve() for path in paths]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("Select explicit, existing rollout files; directories and empty selections are not allowed")
    if baseline == checkpoint:
        raise ValueError("Baseline and checkpoint must differ")
    for destination in (baseline, checkpoint):
        if destination.exists() or destination.with_suffix(destination.suffix + ".snapshot.json").exists():
            raise ValueError("Baseline/checkpoint already exists; preserve it and choose a new destination")
    baseline.parent.mkdir(parents=True, exist_ok=True)
    # A failed import must never leave a partial baseline for another worktree to copy.
    with (
        tempfile.TemporaryDirectory(prefix=".seed-", dir=baseline.parent) as temporary,
        Runtime(Settings(database=str(Path(temporary) / "seed.db"),
                         features={"import", "field_lineage"}, model_mode="dummy")) as runtime,
    ):
        store = runtime.store
        with store.connect() as db:
            db.execute("""CREATE TABLE dev_seed_sources (
                import_id INTEGER PRIMARY KEY REFERENCES raw_imports(id),
                source_path TEXT NOT NULL, sha256 TEXT NOT NULL)""")
        for path in paths:
            with path.open("rb") as handle:
                before = hashlib.file_digest(handle, "sha256").hexdigest()
            with path.open("rb") as lines:
                result = runtime.ingestion.import_file("codex", lines)
            if len(result["session_ids"]) != 1 or len(result["raw_import_ids"]) != 1:
                raise ValueError("Each selected rollout must produce one session and one complete raw archive")
            sid, archive_id = result["session_ids"][0], result["raw_import_ids"][0]
            digest = hashlib.sha256()
            for chunk in store.export_raw(sid, archive_id):
                digest.update(chunk)
            with path.open("rb") as handle:
                after = hashlib.file_digest(handle, "sha256").hexdigest()
            if not before == digest.hexdigest() == after:
                raise ValueError("Rollout changed during import or raw round-trip failed; use a fixed completed session")
            with store.connect() as db:
                db.execute("INSERT OR IGNORE INTO dev_seed_sources VALUES(?,?,?)", (archive_id, str(path), before))
        with store.connect() as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute(
                "PRAGMA foreign_key_check"
            ).fetchall():
                raise ValueError("Seed database integrity check failed")
        # Publish a consistent recovery checkpoint before exposing the shared baseline.
        backup = snapshot_database(store.path, checkpoint)
        published = snapshot_database(checkpoint, baseline)
    return {"baseline": published, "checkpoint": backup}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Complete, locally selected rollout files (never excerpts)")
    args = parser.parse_args()
    try:
        git_dir = Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], text=True
        ).strip())
        if git_dir.name != ".git" or not git_dir.is_dir():
            raise ValueError("Requires a normal clone with .git in the main checkout")
        local = git_dir.parent / ".agentboard"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        print(json.dumps(seed_database(
            args.paths, local / "baseline.db", local / "checkpoints" / f"baseline-{stamp}.db"
        ), indent=2))
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
