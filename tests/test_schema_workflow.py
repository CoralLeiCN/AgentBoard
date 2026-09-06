"""Schema refresh tests use a local fake CLI; no Codex/model access is required."""

import json
import subprocess
import sys

import pytest

from scripts.codex_schemas import ROOT, sync, verify


def fake_codex(tmp_path, version="0.1.0", files=None, fail=False):
    files = files if files is not None else {"Example.json": {"type": "string"}}
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print('codex-cli {version}')\n"
        "    sys.exit(0)\n"
        "assert sys.argv[1:3] == ['app-server', 'generate-json-schema']\n"
        "assert sys.argv[-1] == '--experimental'\n"
        f"if {fail!r}: sys.exit(3)\n"
        "out = Path(sys.argv[sys.argv.index('--out') + 1])\n"
        f"for name, schema in {files!r}.items():\n"
        "    path = out / name\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    path.write_text(json.dumps(schema))\n"
    )
    executable.chmod(0o755)
    return str(executable)


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_repository_schema_baseline_is_intact():
    # Runs in the regular test suite and CI without invoking the installed Codex.
    manifest = verify(ROOT)
    assert "ClientRequest.json" in manifest["files"]
    assert "v2/ThreadReadParams.json" in manifest["files"]


def test_noop_check_and_update_preserve_baseline(tmp_path):
    root = tmp_path / "baseline"
    executable = fake_codex(tmp_path)
    assert sync(root, executable, update=True) == 0
    before = contents(root)
    assert sync(root, executable) == 0
    assert sync(root, executable, update=True) == 0
    assert contents(root) == before


def test_upgrade_reports_drift_and_replaces_removed_files(tmp_path, capsys):
    root = tmp_path / "baseline"
    executable = fake_codex(tmp_path, files={"Old.json": {}, "Changed.json": {"type": "string"}})
    sync(root, executable, update=True)
    before = contents(root)
    executable = fake_codex(
        tmp_path, version="0.2.0", files={"v2/New.json": {}, "Changed.json": {"type": "integer"}}
    )
    assert sync(root, executable) == 1
    assert contents(root) == before
    output = capsys.readouterr().out
    assert "added: 1" in output and "removed: 1" in output and "changed: 1" in output
    assert sync(root, executable, update=True) == 0
    assert not (root / "generated/Old.json").exists()
    assert verify(root)["codex_version"] == "codex-cli 0.2.0"
    assert sync(root, executable) == 0


def test_version_change_is_drift_even_if_schemas_are_identical(tmp_path):
    root = tmp_path / "baseline"
    sync(root, fake_codex(tmp_path), update=True)
    before = contents(root)
    assert sync(root, fake_codex(tmp_path, version="0.2.0")) == 1
    assert contents(root) == before


@pytest.mark.parametrize("change", ["edit", "remove", "add"])
def test_offline_verification_catches_unrecorded_changes(tmp_path, change):
    root = tmp_path / "baseline"
    sync(root, fake_codex(tmp_path), update=True)
    schema = root / "generated/Example.json"
    if change == "edit":
        schema.write_text('{"type": "number"}')
    elif change == "remove":
        schema.unlink()
    else:
        (root / "generated/Extra.json").write_text("{}")
    with pytest.raises(ValueError):
        verify(root)


@pytest.mark.parametrize("failure", ["process", "reference", "empty"])
def test_failed_generation_preserves_previous_snapshot(tmp_path, failure):
    root = tmp_path / "baseline"
    sync(root, fake_codex(tmp_path), update=True)
    before = contents(root)
    files = {} if failure == "empty" else {"Broken.json": {"$ref": "#/definitions/Missing"}}
    executable = fake_codex(tmp_path, files=files, fail=failure == "process")
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        sync(root, executable, update=True)
    assert contents(root) == before
    verify(root)


def test_failed_directory_replacement_restores_previous_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "baseline"
    sync(root, fake_codex(tmp_path), update=True)
    before = contents(root)
    original_rename = type(root).rename

    def fail_install(path, target):
        if path.name == "snapshot":
            raise OSError("Synthetic installation failure")
        return original_rename(path, target)

    monkeypatch.setattr(type(root), "rename", fail_install)
    with pytest.raises(OSError, match="Synthetic"):
        sync(root, fake_codex(tmp_path, version="0.2.0"), update=True)
    assert contents(root) == before


def test_internal_references_are_resolved_with_json_pointer_escaping(tmp_path):
    root = tmp_path / "baseline"
    schema = {"definitions": {"a/b~c": {"type": "string"}}, "$ref": "#/definitions/a~1b~0c"}
    sync(root, fake_codex(tmp_path, files={"Example.json": schema}), update=True)
    assert json.loads((root / "generated/Example.json").read_text()) == schema
    verify(root)
