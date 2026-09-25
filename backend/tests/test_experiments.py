import json
import os
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agentboard.experiments import Archive
from agentboard.experiments.archive import atomic_json, locked, sha256
from agentboard.experiments.cli import archive_settings
from agentboard.experiments.coverage import record_coverage
from agentboard.experiments.coverage_analysis import VERSION, calculate


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path / "durable")


def complete(archive, value=None, kind="experiment", inputs=()):
    recorder = archive.begin("test", "classification", kind=kind, inputs=inputs)
    recorder.add_json({"value": 1} if value is None else value, "outputs/result.json", schema_version="test-v1")
    return recorder.finalize()["id"]


def test_exact_raw_bytes_and_immutable_finalization(archive, tmp_path):
    source = tmp_path / "source"
    data = b'{"unknown":"event"}\r\n\x00\xff\n'
    source.write_bytes(data)
    run = archive.begin("test", "raw")
    run.add_file(source, "raw/original.jsonl", role="raw-response")
    result = run.finalize(outcome="failed")
    assert result["verified"]
    assert (archive.locate(run.id) / "raw/original.jsonl").read_bytes() == data
    manifest = archive.inspect(run.id)
    assert manifest["outcome"] == "failed" and manifest["run_id"] == run.id
    assert "Execution code unavailable" in manifest["provenance_gaps"]
    with pytest.raises(ValueError, match="already exists"):
        archive.begin("test", "raw", bundle_id=run.id)
    with pytest.raises(ValueError, match="immutable"):
        archive.recorder(run.id)
    (archive.locate(run.id) / "raw/original.jsonl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        archive.verify(run.id)


@pytest.mark.parametrize("name", ["/etc/passwd", "../escape", "a/../../b", "a\\b", "a//b", "./x", "manifest.json"])
def test_reject_artifact_path_escapes(archive, tmp_path, name):
    source = tmp_path / "source"
    source.write_text("data")
    run = archive.begin("test", "paths")
    with pytest.raises(ValueError):
        run.add_file(source, name)


def test_roots_symlinks_and_project_names(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(checkout, target_is_directory=True)
    for root in (checkout / "data", alias / "data", tmp_path / ".git/data"):
        with pytest.raises(ValueError, match="outside Git"):
            Archive(root, forbidden_roots=[checkout])
    with pytest.raises(ValueError, match="absolute"):
        Archive("relative")
    home = tmp_path / "home"
    home.mkdir()
    (home / "experiments").symlink_to(checkout, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Archive(home)
    store = Archive(tmp_path / "real")
    with pytest.raises(ValueError, match="Invalid project"):
        store.begin("../project", "group")
    run = store.begin("project", "group")
    (run.stage / "bundle/raw").symlink_to(checkout, target_is_directory=True)
    source = tmp_path / "file"
    source.write_text("data")
    with pytest.raises(ValueError, match="symlink"):
        run.add_file(source, "raw/escape")


def test_root_rejects_other_worktrees_even_outside_current_checkout(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", str(main)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(main), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True)
    other = tmp_path / "other"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach", str(other)], check=True, capture_output=True)
    monkeypatch.chdir(main)
    with pytest.raises(ValueError, match="outside Git"):
        Archive(other / "data")
    durable = Archive(tmp_path / "shared")
    bid = complete(durable)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(other)
    assert durable.verify(bid)["verified"]


def test_manifest_and_dependency_integrity_and_restore(archive, tmp_path):
    dataset = complete(archive, kind="dataset")
    reference = archive.reference(dataset, "outputs/result.json")
    run = complete(archive, inputs=[reference])
    restored = tmp_path / "restored"
    shutil.copytree(archive.home, restored)
    shutil.rmtree(archive.home)
    other = Archive(restored)
    assert other.verify(run)["verified"]
    assert json.loads(other.resolve(reference).read_text()) == {"value": 1}
    with pytest.raises(ValueError, match="storage provider"):
        other.resolve({**reference, "backend": "huggingface"})
    with pytest.raises(ValueError, match="Pinned manifest"):
        other.resolve({**reference, "manifest_sha256": "0" * 64})
    shutil.rmtree(other.locate(dataset))
    with pytest.raises(ValueError, match="missing"):
        other.verify(run)


def test_cycles_unsupported_versions_and_untracked_evidence(archive):
    bid = complete(archive)
    path = archive.locate(bid) / "manifest.json"
    manifest = json.loads(path.read_text())
    original = path.read_bytes()
    manifest["inputs"] = [{"backend": "filesystem", "id": bid}]
    atomic_json(path, manifest)
    with pytest.raises(ValueError, match="cycle"):
        archive.verify(bid)
    manifest["format_version"] = 99
    atomic_json(path, manifest)
    with pytest.raises(ValueError, match="Unsupported manifest"):
        archive.inspect(bid)
    path.write_bytes(original)
    (archive.locate(bid) / "unexpected").write_text("extra")
    with pytest.raises(ValueError, match="inventory"):
        archive.verify(bid)


def test_interrupted_recovery_retains_partials_and_missing_timestamp(archive):
    run = archive.begin("test", "interrupted", metadata={"started_at": None})
    run.add_json({"completed": True}, "raw/first.json")
    (run.stage / "partials/incomplete").write_bytes(b"partial\xff")
    with pytest.raises(ValueError, match="Partial artifacts"):
        run.finalize()
    assert archive.list() == [{"id": run.id, "state": "unfinished"}]
    result = run.recover()
    manifest = archive.inspect(result["id"])
    assert manifest["outcome"] == "interrupted" and manifest["ended_at"] is None
    assert (archive.locate(run.id) / "raw/interrupted-partials/incomplete").read_bytes() == b"partial\xff"


def test_missing_evidence_and_disk_failure_cannot_finalize(archive, monkeypatch):
    run = archive.begin("test", "failure")
    run.add_json({"evidence": 1}, "raw/source.json")
    (run.stage / "bundle/raw/source.json").unlink()
    with pytest.raises(ValueError, match="Missing"):
        run.finalize()
    with pytest.raises(ValueError, match="missing"):
        run.recover()
    broken = archive.begin("test", "disk")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        broken.add_json({"more": 1}, "outputs/result.json")
    assert not (archive.root / "runs" / broken.id).exists()
    assert list((broken.stage / "partials").iterdir())


def test_recovery_does_not_overwrite_completed_artifact(archive):
    run = archive.begin("test", "recovery-collision")
    run.add_json({"original": True}, "raw/interrupted-partials/collision")
    completed = run.stage / "bundle/raw/interrupted-partials/collision"
    original = completed.read_bytes()
    partial = run.stage / "partials/collision"
    partial.write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="already exists"):
        run.recover()
    assert completed.read_bytes() == original
    assert partial.read_bytes() == b"incomplete"


def test_multiple_recorders_and_lock_collision(archive):
    a, b = archive.begin("test", "a"), archive.begin("test", "b")
    a.add_json({"run": "a"}, "outputs/result.json")
    b.add_json({"run": "b"}, "outputs/result.json")
    with locked(a.stage / "writer.lock"):
        with pytest.raises(ValueError, match="lock"):
            a.add_json({"blocked": True}, "outputs/blocked.json")
    b.finalize()
    a.recover()
    assert a.id != b.id and len(archive.list()) == 2


def test_sqlite_snapshot_includes_committed_wal(archive, tmp_path):
    source = tmp_path / "live.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE data(value TEXT)")
    connection.execute("INSERT INTO data VALUES('retained')")
    connection.commit()
    run = archive.begin("test", "snapshot")
    run.add_sqlite_snapshot(source, "raw/snapshot.db")
    run.finalize()
    with sqlite3.connect(archive.locate(run.id) / "raw/snapshot.db") as db:
        assert db.execute("SELECT value FROM data").fetchone()[0] == "retained"
    connection.close()


def test_artifact_file_and_directory_with_same_prefix_finalize(archive, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic bytes\r\n")
    run = archive.begin("test", "inventory-order")
    run.add_file(source, "raw/rollouts/first.jsonl")
    run.add_file(source, "raw/rollouts.tar.gz")
    result = run.finalize()
    assert result["verified"] and result["file_count"] == 2
    assert (archive.locate(run.id) / "raw/rollouts.tar.gz").read_bytes() == source.read_bytes()


def settings(**kwargs):
    return SimpleNamespace(data_home=None, mode=None, archive_config=None, **kwargs)


def test_config_precedence_and_filesystem_ignores_tracking(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://must-not-contact.invalid")
    monkeypatch.setenv("AGENTBOARD_DATA_HOME", str(tmp_path / "environment"))
    args = settings()
    assert archive_settings(args).home == tmp_path / "environment"
    config = tmp_path / "config.toml"
    config.write_text(f'[experiments]\ndata_home = "{tmp_path / "config-home"}"\n')
    args.archive_config = config
    assert archive_settings(args).home == tmp_path / "config-home"
    args.data_home = tmp_path / "cli"
    assert archive_settings(args).home == tmp_path / "cli"
    args.mode = "mlflow"
    with pytest.raises(ValueError, match="unavailable"):
        archive_settings(args)
    args.mode = None
    config.write_text('[experiments]\nunknown="value"\n')
    with pytest.raises(ValueError, match="Unknown"):
        archive_settings(args)


def test_cli_isolated_from_trace_runtime_and_models(tmp_path, monkeypatch, capsys):
    from agentboard import cli

    monkeypatch.setattr(cli, "Runtime", lambda *a: pytest.fail("Trace Runtime must not be constructed"))
    monkeypatch.setenv("AGENTBOARD_PLUGINS", "invalid-trace-config")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://must-not-contact.invalid")
    monkeypatch.setattr(sys, "argv", ["agentboard", "experiments", "--data-home", str(tmp_path / "data"), "list"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == []
    assert not (tmp_path / "data").exists()


def target(number, digest=None):
    return {"original_codex_session_id": "session", "original_codex_turn_id": f"turn-{number}",
            "classification_input_sha256": digest or str(number) * 64}


def payload():
    labels = [{**target(i), "category": "coding", "reason": "Synthetic coding task", "reasoning_effort": "low"}
              for i in (1, 2)]
    return {"format_version": VERSION, "targets": [target(1), target(2, "a" * 64), target(3)],
            "sources": [{"format": "independent-turn-v1", "value": {
                "model": "synthetic", "reasoning_effort": "low", "classifications": labels}}]}


def test_coverage_exact_inputs_pending_and_configurations():
    p = payload()
    r = calculate(p)
    assert r["targets_available_all_configurations"] == 1 and r["targets_pending_any_configuration"] == 2
    assert r["classifiers"][0]["reasons"] == {"available": 1, "changed_input": 1, "missing": 1}
    p["sources"][0]["value"]["classifications"][0]["reasoning_effort"] = "high"
    with pytest.raises(ValueError, match="configuration"):
        calculate(p)


def test_coverage_duplicate_invalid_and_unmatched_policies():
    p = payload()
    p["targets"].append(p["targets"][0])
    with pytest.raises(ValueError, match="Duplicate target"):
        calculate(p)
    p = payload()
    rows = p["sources"][0]["value"]["classifications"]
    rows.append(rows[0])
    with pytest.raises(ValueError, match="Duplicate historical"):
        calculate(p)
    rows.pop()
    rows[0]["reason"] = ""
    rows.append({**target(4), "category": "other", "reason": "Outside", "reasoning_effort": "low"})
    r = calculate(p)
    assert r["target_count"] == 3 and r["classifiers"][0]["reasons"]["invalid_result"] == 1
    assert len(r["classifiers"][0]["outside_requested_subset"]) == 1
    p["sources"][0]["format"] = "new-schema"
    with pytest.raises(ValueError, match="Unsupported historical"):
        calculate(p)


def test_comparison_coverage_keeps_missing_model_results_pending():
    label = {"category": "coding", "reason": "Synthetic coding task", "reasoning_effort": "low"}
    rows = [{**target(1), "classifications": {"first": label, "second": label}},
            {**target(2), "classifications": {"first": label}},
            {**target(3), "classifications": {"first": None, "second": None}}]
    p = {"format_version": VERSION, "targets": [target(1), target(2), target(3)], "sources": [
        {"format": "turn-comparison-v1", "value": {"experiment": {"models": [
            {"model": model, "reasoning_effort": "low"} for model in ("first", "second")]},
            "turn_results": rows}}]}
    result = calculate(p)
    assert result["classifiers"][0]["reasons"] == {"available": 2, "missing": 1}
    assert result["classifiers"][1]["reasons"] == {"available": 1, "missing": 2}
    assert result["targets_pending_any_configuration"] == 2
    rows.append({**target(3), "classifications": {}})
    with pytest.raises(ValueError, match="Duplicate historical target"):
        calculate(p)


def test_offline_report_regeneration_after_restore(archive, tmp_path):
    p = payload()
    dataset = complete(archive, p["targets"], "dataset")
    original = complete(archive, p["sources"][0]["value"])
    before = sha256(archive.locate(original) / "manifest.json")
    recipe = {"format_version": VERSION, "project": "test", "experiment": "coverage",
              "dataset": archive.reference(dataset, "outputs/result.json"), "results": [
                  {"format": "independent-turn-v1", "reference": archive.reference(original, "outputs/result.json")} ]}
    first = record_coverage(archive, recipe)
    second = record_coverage(archive, recipe)
    assert first["id"] != second["id"] and first["pending"] == 2
    assert sha256(archive.locate(original) / "manifest.json") == before
    output = archive.locate(first["id"]) / "outputs/coverage.json"
    assert output.read_bytes() == (archive.locate(second["id"]) / "outputs/coverage.json").read_bytes()
    restore = tmp_path / "restored"
    shutil.copytree(archive.home, restore)
    shutil.rmtree(archive.home)
    restored = Archive(restore)
    bundle = restored.locate(first["id"])
    assert restored.verify(first["id"])["verified"]
    regenerated = tmp_path / "regenerated.json"
    subprocess.run([sys.executable, "-I", str(bundle / "inputs/analysis.py"),
                    str(bundle / "inputs/analysis-input.json"), str(regenerated)], check=True)
    assert regenerated.read_bytes() == (bundle / "outputs/coverage.json").read_bytes()
