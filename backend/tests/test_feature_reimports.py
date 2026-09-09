"""Synthetic growing/conflicting rollouts keep normalization correct across derived-lineage settings."""

import copy
import hashlib
import io
import json

import pytest
from test_codex import records, wait_record
from test_input_origin import CONTEXT, prompt

from agentboard.adapters.codex import CodexAdapter
from agentboard.store import Store


def source(rows):
    return "\n".join(json.dumps(row) for row in rows)


def ingest(store, rows):
    return store.ingest(CodexAdapter().parse(io.StringIO(source(rows))))


def normalized(store, sid):
    return [{key: value for key, value in event.items() if key != "row_id"}
            for event in store.export(sid)]


@pytest.mark.parametrize("profiles", [(False, False), (True, False), (False, True)])
def test_growing_mirror_input_matches_archived_normalization(tmp_path, profiles):
    rows = [records()[0], prompt(1, "Build it", True), prompt(2, CONTEXT), prompt(3, "Build it")]
    sid = rows[0]["payload"]["id"]
    baseline = Store(str(tmp_path / "archived.db"))
    for raw, snapshot in zip(profiles, (rows[:2], rows), strict=True):
        store = Store(str(tmp_path / "configured.db"), retain_lineage=raw)
        ingest(store, snapshot)
        ingest(baseline, snapshot)
        assert normalized(store, sid) == normalized(baseline, sid)
    inputs = list(store.export(sid, "user"))
    assert len(inputs) == 1 and inputs[0]["sequence"] == 4
    if not any(profiles):
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM rollout_line_fingerprints").fetchone()[0] == len(rows)
            for table in ("event_field_sources",):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("profiles", [(False, False, False), (True, False, True), (False, True, False)])
def test_prefix_full_short_retry_preserves_input_and_wait_metrics(tmp_path, profiles):
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"), prompt(3, CONTEXT),
            prompt(9, "Next"), wait_record(10, "task_complete")]
    sid = rows[0]["payload"]["id"]
    baseline = Store(str(tmp_path / "archived.db"))
    for raw, snapshot in zip(profiles, (rows[:4], rows, rows[:3]), strict=True):
        store = Store(str(tmp_path / "configured.db"), retain_lineage=raw)
        ingest(store, snapshot)
        ingest(baseline, snapshot)
        assert normalized(store, sid) == normalized(baseline, sid)
        assert store.stats(sid) == baseline.stats(sid)
    assert store.stats(sid)["counts"]["user"] == 2
    assert store.stats(sid)["counts"]["user_wait"] == 1


@pytest.mark.parametrize("profiles", [(False, False), (True, False), (False, True)])
def test_conflicting_source_cannot_remove_retained_input_or_wait(tmp_path, profiles):
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"), prompt(9, "Next")]
    sid = rows[0]["payload"]["id"]
    path = str(tmp_path / "conflict.db")
    store = Store(path, retain_lineage=profiles[0])
    ingest(store, rows)
    original = normalized(store, sid)
    changed = copy.deepcopy(rows)
    # A now-ignored source record produces no normalized event to compare directly.
    changed[-1].update(type="world_state", payload={"synthetic": "changed"})
    store = Store(path, retain_lineage=profiles[1])
    ingest(store, changed)
    assert normalized(store, sid) == original
    # Remember both observed hashes so an original snapshot remains conservative too.
    ingest(store, rows)
    assert normalized(store, sid) == original
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM rollout_line_fingerprints").fetchone()[0] == len(rows) + 1


@pytest.mark.parametrize("lineage", [False])
def test_archived_tool_completed_with_evidence_disabled_rebinds_only_on_reimport(tmp_path, lineage):
    rows = [records()[0], prompt(1, "Run synthetic test"),
            wait_record(2, "function_call", call_id="call", name="exec_command", arguments="synthetic test"),
            wait_record(4, "function_call_output", call_id="call", output="Synthetic success")]
    path = str(tmp_path / "completion.db")
    archived = Store(path)
    first = ingest(archived, rows[:-1])
    sid = rows[0]["payload"]["id"]
    tool = next(archived.export(sid, "tool"))
    assert archived.event_raw(sid, tool["id"])["available"]
    store = Store(path, retain_lineage=lineage)
    completed = ingest(store, rows)
    assert completed["raw_import_ids"]
    assert store.event(sid, tool["id"])["end_time"] is not None
    assert store.event_raw(sid, tool["id"])["available"]
    fields = store.field_lineage(sid, tool["id"])["fields"]
    assert fields["/start_time"]["available"]
    assert not fields["/end_time"]["available"]
    assert not fields["/attributes/output"]["available"]
    assert first["raw_import_ids"][0] in {item["id"] for item in store.raw_imports(sid)}
    assert len(store.raw_imports(sid)) == 2

    reenabled = Store(path)
    assert not reenabled.field_lineage(sid, tool["id"])["fields"]["/end_time"]["available"]
    final = ingest(reenabled, rows)
    assert final["inserted_events"] == 0
    assert reenabled.event_raw(sid, tool["id"])["available"]
    fields = reenabled.field_lineage(sid, tool["id"])["fields"]
    assert fields["/end_time"]["available"]
    assert fields["/attributes/output"]["available"]
    with reenabled.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_v7_upgrade_backfills_fingerprints_before_conflicting_reimport_without_lineage(tmp_path):
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"), prompt(9, "Next")]
    path = str(tmp_path / "legacy.db")
    legacy = Store(path)
    ingest(legacy, rows)
    sid = rows[0]["payload"]["id"]
    original = normalized(legacy, sid)
    with legacy.connect() as db:
        db.execute("DROP TABLE rollout_line_fingerprints")
        db.execute("PRAGMA user_version=7")
    upgraded = Store(path, retain_lineage=False)
    with upgraded.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        hashes = db.execute("SELECT sequence,sha256 FROM rollout_line_fingerprints ORDER BY sequence").fetchall()
        assert [(row[0], row[1]) for row in hashes] == [
            (index, hashlib.sha256(line.encode()).digest())
            for index, line in enumerate(source(rows).splitlines(), 1)
        ]
    changed = copy.deepcopy(rows)
    changed[-1].update(type="world_state", payload={})
    ingest(upgraded, changed)
    assert normalized(upgraded, sid) == original
    reopened = Store(path)
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM rollout_line_fingerprints").fetchone()[0] == len(rows) + 1


def test_hashes_preserve_physical_blank_lines_and_ignore_line_endings(tmp_path):
    rows = [records()[0], prompt(1, "First")]
    store = Store(str(tmp_path / "line-endings.db"))
    lines = ["", json.dumps(rows[0]), " ", json.dumps(rows[1])]
    for text in ("\r\n".join(lines), "\n".join(lines), "\n".join(lines) + "\n"):
        store.ingest(CodexAdapter().parse(io.StringIO(text)))
    with store.connect() as db:
        saved = db.execute("SELECT sequence,sha256 FROM rollout_line_fingerprints ORDER BY sequence").fetchall()
        assert [(row[0], row[1]) for row in saved] == [
            (index, hashlib.sha256(line.encode()).digest()) for index, line in enumerate(lines, 1)
        ]
        assert db.execute("SELECT count(*) FROM raw_lines").fetchone()[0] > 0
