import json
import sqlite3

import pytest

from agentboard.snapshot import snapshot_database
from agentboard.store import Store
from scripts.seed_dev_data import seed_database


def rollout(path, sid):
    records = [
        {"timestamp": "2026-09-07T00:00:00Z", "type": "session_meta", "payload": {"id": sid}},
        {"timestamp": "2026-09-07T00:00:01Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "Synthetic seed test"}},
        {"timestamp": "2026-09-07T00:00:02Z", "type": "future_record", "payload": {"opaque": "keep all"}},
    ]
    # Preserve CRLF and a missing final newline as well as unknown record types.
    content = "\r\n".join(json.dumps(r) for r in records).encode()
    path.write_bytes(content)
    return content


def test_seed_checkpoint_and_worktree_restore_retain_every_raw_byte(tmp_path):
    first, second = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    bodies = {"one": rollout(first, "one"), "two": rollout(second, "two")}
    baseline, checkpoint = tmp_path / "baseline.db", tmp_path / "checkpoints/initial.db"
    result = seed_database([first, second], baseline, checkpoint)
    assert result["baseline"]["counts_at_creation"]["raw_lines"] == 6
    # The source rollouts are no longer required to restore or inspect the full data.
    first.unlink()
    second.unlink()
    restored = tmp_path / "restored.db"
    snapshot_database(checkpoint, restored)
    for database in (baseline, checkpoint, restored):
        store = Store(str(database))
        for sid, body in bodies.items():
            assert b"".join(store.export_raw(sid)) == body
        with sqlite3.connect(database) as db:
            assert db.execute("select count(*) from dev_seed_sources").fetchone()[0] == 2
            assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    # Editing the restored working copy cannot change either recovery source.
    with sqlite3.connect(restored) as db:
        db.execute("update sessions set title='Worktree change'")
    assert Store(str(baseline)).get_session("one")["title"] != "Worktree change"
    assert Store(str(checkpoint)).get_session("one")["title"] != "Worktree change"


def test_failed_seed_never_publishes_a_partial_baseline(tmp_path):
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    rollout(good, "one")
    bad.write_text("not json\n")
    baseline, checkpoint = tmp_path / "baseline.db", tmp_path / "checkpoint.db"
    with pytest.raises(ValueError):
        seed_database([good, bad], baseline, checkpoint)
    assert not baseline.exists() and not checkpoint.exists()
    assert not list(tmp_path.glob(".seed-*"))


def test_seed_refuses_to_overwrite_existing_data(tmp_path):
    source = tmp_path / "source.jsonl"
    rollout(source, "one")
    baseline = tmp_path / "baseline.db"
    baseline.write_bytes(b"existing local data")
    with pytest.raises(ValueError, match="already exists"):
        seed_database([source], baseline, tmp_path / "checkpoint.db")
    assert baseline.read_bytes() == b"existing local data"
