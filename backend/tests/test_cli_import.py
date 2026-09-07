"""CLI batch selection against synthetic rollouts and isolated SQLite databases."""

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import FIXTURE

from agentboard.cli import main
from agentboard.store import Store


def rollout(path, sid, modified=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FIXTURE.read_text().replace("demo-codex-checkout", sid), encoding="utf-8")
    timestamp = 1_700_000_000_000_000_000 + modified
    os.utime(path, ns=(timestamp, timestamp))
    return path


def run_import(monkeypatch, capsys, database, paths, limit=None):
    argv = ["agentboard", "--database", str(database), "import", *map(str, paths)]
    if limit is not None:
        argv.extend(["--limit", str(limit)])
    monkeypatch.setattr(sys, "argv", argv)
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, [json.loads(line) for line in captured.out.splitlines()], captured.err


def saved_sessions(database):
    with Store(str(database)).connect() as db:
        return {row[0] for row in db.execute("SELECT id FROM sessions")}


def test_limit_selects_newest_globally_across_files_and_nested_directories(monkeypatch, capsys, tmp_path):
    sessions = tmp_path / "sessions"
    archived = tmp_path / "archived"
    rollout(sessions / "old.jsonl", "old", 1)
    recent = rollout(sessions / "nested/recent.jsonl", "recent", 30)
    newest = rollout(archived / "newest.jsonl", "newest", 40)
    rollout(archived / "older.jsonl", "older", 2)
    rollout(archived / "ignored.txt", "ignored", 100)
    explicit = rollout(tmp_path / "explicit trace.txt", "explicit", 20)
    database = tmp_path / "test.db"

    code, rows, summary = run_import(monkeypatch, capsys, database, [sessions, explicit, archived], 3)

    assert code == 0
    assert [Path(row["file"]) for row in rows] == [newest, recent, explicit]
    assert saved_sessions(database) == {"newest", "recent", "explicit"}
    assert "Files: 3 processed, 3 succeeded, 0 failed" in summary
    assert "Unique sessions: 3" in summary


def test_equal_modification_times_use_absolute_path_order(monkeypatch, capsys, tmp_path):
    first = rollout(tmp_path / "a.jsonl", "a")
    second = rollout(tmp_path / "b.jsonl", "b")
    third = rollout(tmp_path / "c.jsonl", "c")
    monkeypatch.chdir(tmp_path)

    code, rows, _ = run_import(monkeypatch, capsys, tmp_path / "test.db", [third, Path("b.jsonl"), first], 2)

    assert code == 0
    assert [Path(row["file"]).absolute() for row in rows] == [first, second]


@pytest.mark.parametrize("limit", [1, 10, None])
def test_single_file_and_oversized_limit(monkeypatch, capsys, tmp_path, limit):
    source = rollout(tmp_path / "source.jsonl", "one")
    code, rows, summary = run_import(monkeypatch, capsys, tmp_path / "test.db", [source], limit)
    assert code == 0
    assert len(rows) == 1 and rows[0]["session_ids"] == ["one"]
    assert "Files: 1 processed, 1 succeeded, 0 failed" in summary


@pytest.mark.parametrize("limit", ["0", "-1", "no", "1.5"])
def test_invalid_limit_is_rejected_before_database_creation(monkeypatch, capsys, tmp_path, limit):
    database = tmp_path / "test.db"
    code, rows, error = run_import(monkeypatch, capsys, database, [tmp_path], limit)
    assert code == 2
    assert rows == []
    assert "--limit: must be a positive integer" in error
    assert not database.exists()


def test_failed_file_consumes_limit_and_does_not_stop_selected_batch(monkeypatch, capsys, tmp_path):
    directory = tmp_path / "rollouts"
    bad = rollout(directory / "bad.jsonl", "bad", 30)
    # A complete trace followed by malformed input must leave no partial session/archive.
    with bad.open("a", encoding="utf-8") as stream:
        stream.write("{")
    good = rollout(directory / "good.jsonl", "good", 20)
    rollout(directory / "excluded.jsonl", "excluded", 10)
    database = tmp_path / "test.db"

    code, rows, summary = run_import(monkeypatch, capsys, database, [directory], 2)

    assert code == 1
    assert [Path(row["file"]) for row in rows] == [bad, good]
    assert "error" in rows[0] and rows[1]["inserted_events"] > 0
    assert saved_sessions(database) == {"good"}
    assert "Files: 2 processed, 1 succeeded, 1 failed" in summary
    assert "Unique sessions: 1" in summary
    assert f"New events: {rows[1]['inserted_events']}" in summary


def test_missing_paths_sort_last_and_report_selected_errors(monkeypatch, capsys, tmp_path):
    source = rollout(tmp_path / "source.jsonl", "one")
    missing = tmp_path / "missing.jsonl"
    code, rows, summary = run_import(monkeypatch, capsys, tmp_path / "test.db", [missing, source], 2)
    assert code == 1
    assert [Path(row["file"]) for row in rows] == [source, missing]
    assert "error" in rows[1]
    assert "Files: 2 processed, 1 succeeded, 1 failed" in summary


def test_overlapping_arguments_count_repeated_files_toward_limit(monkeypatch, capsys, tmp_path):
    directory = tmp_path / "rollouts"
    source = rollout(directory / "newest.jsonl", "same", 20)
    rollout(directory / "older.jsonl", "older", 10)
    database = tmp_path / "test.db"

    code, rows, summary = run_import(monkeypatch, capsys, database, [source, directory], 2)

    assert code == 0
    assert [Path(row["file"]) for row in rows] == [source, source]
    assert rows[0]["inserted_events"] > 0 and rows[1]["inserted_events"] == 0
    assert saved_sessions(database) == {"same"}
    assert "Files: 2 processed, 2 succeeded, 0 failed" in summary
    assert "Unique sessions: 1" in summary
    assert "Files with no new events: 1" in summary


def test_limit_keeps_existing_data_and_selects_unchanged_reimports(monkeypatch, capsys, tmp_path):
    directory = tmp_path / "rollouts"
    first = rollout(directory / "first.jsonl", "first", 10)
    recent = rollout(directory / "recent.jsonl", "recent", 20)
    database = tmp_path / "test.db"
    assert run_import(monkeypatch, capsys, database, [first, recent])[0] == 0

    code, rows, summary = run_import(monkeypatch, capsys, database, [directory], 1)

    assert code == 0
    assert len(rows) == 1 and Path(rows[0]["file"]) == recent
    assert rows[0]["inserted_events"] == 0
    assert saved_sessions(database) == {"first", "recent"}
    assert "New events: 0" in summary
    assert "Files with no new events: 1" in summary


@pytest.mark.parametrize("limit", [5, None])
def test_empty_directory(monkeypatch, capsys, tmp_path, limit):
    directory = tmp_path / "empty"
    directory.mkdir()
    code, rows, summary = run_import(monkeypatch, capsys, tmp_path / "test.db", [directory], limit)
    assert code == 0 and rows == []
    assert "Files: 0 processed, 0 succeeded, 0 failed" in summary
    assert "Unique sessions: 0" in summary


def test_unlimited_import_preserves_argument_order_and_repeats(monkeypatch, capsys, tmp_path):
    older = rollout(tmp_path / "older.jsonl", "older", 10)
    newer = rollout(tmp_path / "newer.jsonl", "newer", 20)
    code, rows, summary = run_import(monkeypatch, capsys, tmp_path / "test.db", [older, newer, older])
    assert code == 0
    assert [Path(row["file"]) for row in rows] == [older, newer, older]
    assert "Files: 3 processed, 3 succeeded, 0 failed" in summary
    assert "Unique sessions: 2" in summary
