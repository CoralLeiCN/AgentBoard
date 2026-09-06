import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from agentboard.domain import Session
from agentboard.store import Store

ROOT = Path(__file__).parents[2]


def setup_dev_data(root):
    script = tomllib.loads((ROOT / '.codex/environments/environment.toml').read_text())["setup"]["script"]
    return subprocess.run(["sh", "-c", script], cwd=root, capture_output=True, text=True)


@pytest.fixture
def worktrees(tmp_path, monkeypatch):
    # Exercise the actual shell setup, bypassing dependency installation in this test environment.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text('''#!/bin/sh
set -e
if [ "$*" = "sync --locked --extra dev" ]; then exit 0; fi
[ "$1" = run ] && [ "$2" = agentboard ] || exit 1
shift 2
exec "$AGENTBOARD_TEST_PYTHON" -c 'from agentboard.cli import main; main()' "$@"
''')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("AGENTBOARD_TEST_PYTHON", sys.executable)
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "backend"))
    main = tmp_path / "main checkout"
    main.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(main), *args], check=True, capture_output=True)

    git("init")
    (main / "config").mkdir()
    (main / "config/dev.toml").write_bytes((ROOT / "config/dev.toml").read_bytes())
    git("add", "config/dev.toml")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    trees = [tmp_path / "worktree one", tmp_path / "worktree two"]
    for tree in trees:
        git("worktree", "add", "--detach", str(tree))
    return main, trees


def test_worktree_copies_are_independent_and_reruns_preserve_changes(worktrees):
    main, trees = worktrees
    source = main / ".agentboard/baseline.db"
    baseline = Store(str(source))
    baseline.ingest([Session(id="seed", title="Baseline", started_at="2026-09-07T00:00:00Z")])
    for tree in trees:
        result = setup_dev_data(tree)
        assert result.returncode == 0, result.stderr
    first, second = [tree / ".agentboard/dev.db" for tree in trees]
    with sqlite3.connect(first) as db:
        db.execute("UPDATE sessions SET title='Local edit' WHERE id='seed'")
    assert setup_dev_data(trees[0]).returncode == 0
    assert Store(str(first)).get_session("seed")["title"] == "Local edit"
    assert Store(str(second)).get_session("seed")["title"] == "Baseline"
    assert baseline.get_session("seed")["title"] == "Baseline"
    manifest = json.loads(first.with_suffix(".db.snapshot.json").read_text())
    assert manifest["source"] == str(source.resolve())
    assert manifest["counts_at_creation"]["sessions"] == 1


def test_missing_baseline_fails_without_creating_database(worktrees):
    main, trees = worktrees
    result = setup_dev_data(trees[0])
    assert result.returncode != 0
    assert "Snapshot source does not exist" in result.stderr
    assert not (trees[0] / ".agentboard/dev.db").exists()
    assert not (main / ".agentboard/baseline.db").exists()


def test_invalid_baseline_fails_and_removes_incomplete_copy(worktrees):
    main, trees = worktrees
    source = main / ".agentboard/baseline.db"
    source.parent.mkdir()
    source.write_text("invalid database")
    result = setup_dev_data(trees[0])
    assert result.returncode != 0
    assert "not a database" in result.stderr
    assert source.read_text() == "invalid database"
    assert not (trees[0] / ".agentboard/dev.db").exists()


def test_setup_also_works_in_main_checkout(worktrees):
    main, _ = worktrees
    Store(str(main / ".agentboard/baseline.db"))
    result = setup_dev_data(main)
    assert result.returncode == 0, result.stderr
    assert (main / ".agentboard/dev.db").is_file()
