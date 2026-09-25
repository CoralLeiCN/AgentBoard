"""Immutable filesystem bundles with pinned dependencies and recoverable staging."""

import contextlib
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

VERSION = 1
KINDS = {"dataset": "datasets", "experiment": "runs", "report": "runs"}
OUTCOMES = {"succeeded", "failed", "interrupted", "unknown"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("Invalid project or experiment name")
    return name


def identifier(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")
    return value


def relative(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError("Artifact paths must be safe relative POSIX paths")
    return name


def checked(root, name):
    path = Path(root)
    if path.is_symlink():
        raise ValueError("Archive symlinks are not supported")
    for part in relative(name).split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError("Archive symlinks are not supported")
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing a symlink destination")
    temporary = path.with_name("." + path.name + "-" + uuid4().hex)
    with temporary.open("xb") as stream:
        temporary.chmod(0o600)
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


@contextlib.contextmanager
def locked(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another operation holds the archive lock; retry later") from None
        yield
    finally:
        os.close(fd)


def checkout_roots():
    roots = set()
    for directory in (Path.cwd(), Path(__file__).parent):
        try:
            listing = subprocess.run(["git", "-C", str(directory), "worktree", "list", "--porcelain"],
                                     capture_output=True, text=True, check=True).stdout
            roots.update(Path(line[9:]).resolve() for line in listing.splitlines() if line.startswith("worktree "))
            common = subprocess.run(["git", "-C", str(directory), "rev-parse", "--path-format=absolute",
                                     "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
            roots.add(Path(common).resolve())
        except (OSError, subprocess.CalledProcessError):
            continue
    return roots


def inventory(root):
    """Never follow source or artifact symlinks; include empty and binary files."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a directory without symlinks")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink found in evidence inventory")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Only regular evidence files are supported")
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed while hashing")
        result.append({"path": relative(path.relative_to(root).as_posix()), "bytes": after.st_size,
                       "sha256": digest})
    return sorted(result, key=lambda entry: entry["path"])


class Archive:
    def __init__(self, data_home, *, forbidden_roots=()):
        if not data_home or not Path(data_home).expanduser().is_absolute():
            raise ValueError("AGENTBOARD_DATA_HOME must be an absolute path")
        self.home = Path(data_home).expanduser().resolve()
        self.root = self.home / "experiments"
        roots = checkout_roots() | {Path(p).resolve() for p in forbidden_roots}
        if ".git" in self.home.parts or any(self.home.is_relative_to(p) for p in roots):
            raise ValueError("Data home must be outside Git checkouts and metadata")
        for part in (self.root, *[self.root / d for d in ("datasets", "runs", "staging", "sync")]):
            if part.is_symlink():
                raise ValueError("Archive directories must not be symlinks")

    def initialize(self):
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("experiments", "experiments/datasets", "experiments/runs", "experiments/staging",
                     "experiments/sync"):
            checked(self.home, name).mkdir(exist_ok=True, mode=0o700)
        sync_dir(self.home)

    def begin(self, project, experiment, *, kind="experiment", metadata=None, inputs=(), bundle_id=None):
        if kind not in KINDS:
            raise ValueError("Unknown bundle kind")
        safe_name(project)
        safe_name(experiment)
        bid = identifier(bundle_id or str(uuid4()))
        allowed = {"started_at", "code", "environment", "configuration", "metrics", "relationships",
                   "provenance_gaps", "coverage", "source_provenance", "name"}
        if metadata and metadata.keys() - allowed:
            raise ValueError("Unknown recorder metadata fields")
        encoded(metadata or {})
        self.initialize()
        with locked(checked(self.root, "sync/archive.lock")):
            if any(checked(self.root, f"{d}/{bid}").exists() for d in ("datasets", "runs", "staging")):
                raise ValueError("Bundle identity already exists")
            stage = checked(self.root, f"staging/{bid}")
            stage.mkdir(mode=0o700)
            (stage / "bundle").mkdir(mode=0o700)
            (stage / "partials").mkdir(mode=0o700)
            record = {"format_version": VERSION, "id": bid, "kind": kind, "project": project,
                      "experiment": experiment, "recorded_at": now(), "started_at": now(),
                      "ended_at": None, "outcome": "unknown", "code": {}, "environment": {},
                      "configuration": {}, "metrics": {}, "relationships": [], "provenance_gaps": [],
                      "inputs": list(inputs), "files": []}
            record["dataset_id" if kind == "dataset" else "run_id"] = bid
            record.update(metadata or {})
            atomic_json(stage / "record.json", record)
            sync_dir(stage.parent)
        return Recorder(self, bid)

    def recorder(self, bid):
        return Recorder(self, identifier(bid))

    def locate(self, bid):
        identifier(bid)
        matches = [checked(self.root, f"{d}/{bid}") for d in ("datasets", "runs")
                   if checked(self.root, f"{d}/{bid}").is_dir()]
        if len(matches) != 1:
            raise ValueError("Bundle missing or conflicting identity")
        return matches[0]

    def inspect(self, bid):
        path = checked(self.locate(bid), "manifest.json")
        value = json.loads(path.read_bytes())
        if value.get("format_version") != VERSION:
            raise ValueError("Unsupported manifest version; retained but not interpreted")
        if value.get("id") != bid or value.get("kind") not in KINDS:
            raise ValueError("Manifest identity mismatch")
        if value.get("dataset_id" if value["kind"] == "dataset" else "run_id") != bid:
            raise ValueError("Missing typed bundle identity")
        if self.locate(bid).parent.name != KINDS[value["kind"]]:
            raise ValueError("Manifest kind disagrees with storage directory")
        return value

    def verify(self, bid, *, _visiting=None, _verified=None):
        visiting = set() if _visiting is None else _visiting
        verified = {} if _verified is None else _verified
        if bid in visiting:
            raise ValueError("Dependency cycle")
        if bid in verified:
            return verified[bid]
        visiting.add(bid)
        manifest = self.inspect(bid)
        safe_name(manifest["project"])
        safe_name(manifest["experiment"])
        if manifest.get("outcome") not in OUTCOMES:
            raise ValueError("Invalid execution outcome")
        entries = manifest["files"]
        expected = {}
        for entry in entries:
            name = relative(entry["path"])
            if name == "manifest.json" or name in expected:
                raise ValueError("Reserved or duplicate artifact path")
            if not all(k in entry for k in ("role", "media_type", "schema_version")):
                raise ValueError("Incomplete artifact inventory")
            expected[name] = {k: entry[k] for k in ("path", "bytes", "sha256")}
        actual = {e["path"]: e for e in inventory(self.locate(bid)) if e["path"] != "manifest.json"}
        if expected != actual:
            raise ValueError("Artifact inventory/hash mismatch")
        for reference in manifest["inputs"]:
            self.resolve(reference, _visiting=visiting, _verified=verified)
        visiting.remove(bid)
        result = {"id": bid, "kind": manifest["kind"], "manifest_sha256":
                  sha256(checked(self.locate(bid), "manifest.json")), "file_count": len(entries), "verified": True}
        verified[bid] = result
        return result

    def reference(self, bid, path):
        manifest = self.inspect(bid)
        self.verify(bid)
        entry = next((e for e in manifest["files"] if e["path"] == relative(path)), None)
        if entry is None:
            raise ValueError("Artifact is not in manifest")
        return {"backend": "filesystem", "project": manifest["project"], "id": bid,
                "manifest_sha256": sha256(checked(self.locate(bid), "manifest.json")),
                "path": path, "sha256": entry["sha256"]}

    def resolve(self, reference, *, _visiting=None, _verified=None):
        if reference.get("backend") != "filesystem":
            raise ValueError("Unsupported dataset/artifact storage provider")
        bid = reference["id"]
        result = self.verify(bid, _visiting=_visiting, _verified=_verified)
        manifest = self.inspect(bid)
        if (result["manifest_sha256"] != reference["manifest_sha256"]
                or manifest["project"] != reference["project"]):
            raise ValueError("Pinned manifest identity/hash mismatch")
        name = relative(reference["path"])
        entry = next((e for e in manifest["files"] if e["path"] == name), None)
        if not entry or entry["sha256"] != reference["sha256"]:
            raise ValueError("Pinned artifact hash mismatch")
        return checked(self.locate(bid), name)

    def list(self):
        result = []
        for directory in ("datasets", "runs", "staging"):
            parent = checked(self.root, directory)
            if not parent.exists():
                continue
            for path in sorted(parent.iterdir()):
                if path.is_symlink():
                    raise ValueError("Archive symlink")
                if not path.is_dir():
                    continue
                identifier(path.name)
                if directory == "staging":
                    result.append({"id": path.name, "state": "unfinished"})
                else:
                    try:
                        m = self.inspect(path.name)
                        result.append({k: m[k] for k in ("id", "kind", "project", "experiment", "outcome", "name")
                                       if k in m})
                    except (ValueError, KeyError, OSError) as exc:
                        result.append({"id": path.name, "state": "unreadable", "error": str(exc)})
        return result


class Recorder:
    def __init__(self, archive, bid):
        self.archive = archive
        self.id = identifier(bid)
        self.stage = checked(archive.root, f"staging/{bid}")
        if not checked(self.stage, "record.json").is_file():
            raise ValueError("Staging record missing; finalized bundles are immutable")

    def _record(self):
        return json.loads(checked(self.stage, "record.json").read_bytes())

    def add_file(self, source, path, *, role="evidence", media_type=None, schema_version=None):
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError("Evidence must be a regular file, not a symlink")
        relative(path)
        if path == "manifest.json":
            raise ValueError("Reserved manifest path")
        with locked(checked(self.stage, "writer.lock")):
            record = self._record()
            destination = checked(self.stage, "bundle/" + path)
            if destination.exists():
                raise ValueError("Artifact already recorded; choose a new path")
            temporary = checked(self.stage, "partials/" + uuid4().hex)
            before = source.stat()
            h = hashlib.sha256()
            with source.open("rb") as src, temporary.open("xb") as dst:
                temporary.chmod(0o600)
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
                    h.update(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            after = source.stat()
            if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    or h.hexdigest() != sha256(source)):
                raise ValueError("Evidence changed during copy; partial evidence retained")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.rename(temporary, destination)
            sync_dir(destination.parent)
            entry = {"path": path, "bytes": after.st_size, "sha256": h.hexdigest(), "role": role,
                     "media_type": media_type or mimetypes.guess_type(path)[0] or "application/octet-stream",
                     "schema_version": schema_version}
            record["files"].append(entry)
            atomic_json(self.stage / "record.json", record)
        return entry

    def add_json(self, value, path, **kwargs):
        temporary = checked(self.stage, "partials/" + uuid4().hex)
        with temporary.open("xb") as stream:
            temporary.chmod(0o600)
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            result = self.add_file(temporary, path, media_type="application/json", **kwargs)
        except BaseException:
            raise
        else:
            temporary.unlink()
            return result

    def add_sqlite_snapshot(self, source, path):
        temporary = checked(self.stage, "partials/" + uuid4().hex)
        source = Path(source).resolve(strict=True)
        src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(temporary)
            try:
                temporary.chmod(0o600)
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite snapshot integrity failure")
            finally:
                dst.close()
        finally:
            src.close()
        result = self.add_file(temporary, path, role="consistent-sqlite-snapshot")
        temporary.unlink()
        return result

    def finalize(self, *, outcome="succeeded", ended_at="now"):
        if outcome not in OUTCOMES:
            raise ValueError("Unknown outcome")
        with locked(checked(self.stage, "writer.lock")), locked(checked(self.archive.root, "sync/archive.lock")):
            record = self._record()
            if any((self.stage / "partials").iterdir()):
                raise ValueError("Partial artifacts remain; explicit interrupted recovery is required")
            record.update(outcome=outcome, ended_at=now() if ended_at == "now" else ended_at)
            expected = sorted(({k: e[k] for k in ("path", "bytes", "sha256")} for e in record["files"]),
                              key=lambda e: e["path"])
            actual = [e for e in inventory(self.stage / "bundle") if e["path"] != "manifest.json"]
            if expected != actual:
                raise ValueError("Missing, changed, or unrecorded evidence; staging retained")
            verified = {}
            for reference in record["inputs"]:
                self.archive.resolve(reference, _visiting={self.id}, _verified=verified)
            if record["started_at"] is None:
                if "Historical start time unavailable" not in record["provenance_gaps"]:
                    record["provenance_gaps"].append("Historical start time unavailable")
            if record["ended_at"] is None:
                record["provenance_gaps"].append("Execution end time unavailable")
            for name in ("started_at", "ended_at"):
                value = record[name]
                if value is not None:
                    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                        raise ValueError("Execution timestamps must be UTC RFC 3339 or null")
            for name in ("code", "environment"):
                if not record[name]:
                    record["provenance_gaps"].append(f"Execution {name} unavailable")
            atomic_json(self.stage / "bundle/manifest.json", record)
            destination = checked(self.archive.root, f"{KINDS[record['kind']]}/{self.id}")
            if destination.exists():
                raise ValueError("Finalized identity already exists")
            for directory in sorted((p for p in (self.stage / "bundle").rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                sync_dir(directory)
            sync_dir(self.stage / "bundle")
            os.rename(self.stage / "bundle", destination)
            sync_dir(destination.parent)
        shutil.rmtree(self.stage)
        sync_dir(self.stage.parent)
        return self.archive.verify(self.id)

    def recover(self):
        """Explicitly seal unfinished evidence; never infer an execution end time."""
        with locked(checked(self.stage, "writer.lock")):
            record = self._record()
            partials = checked(self.stage, "partials")
            for path in sorted(partials.iterdir()):
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Invalid partial artifact")
                destination = checked(self.stage, "bundle/raw/interrupted-partials/" + path.name)
                if destination.exists():
                    raise ValueError("Recovery artifact already exists; staging retained")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.rename(path, destination)
                sync_dir(destination.parent)
            old = {e["path"]: e for e in record["files"]}
            record["files"] = []
            for entry in inventory(self.stage / "bundle"):
                if entry["path"] == "manifest.json":
                    continue
                previous = old.pop(entry["path"], None)
                if previous and any(previous[k] != entry[k] for k in ("bytes", "sha256")):
                    raise ValueError("Previously completed artifact changed; cannot recover as intact")
                record["files"].append(previous or {**entry, "role": "interrupted-evidence",
                                                     "media_type": "application/octet-stream", "schema_version": None})
            if old:
                raise ValueError("Previously completed artifacts missing; staging retained")
            record["provenance_gaps"].append("Explicit interrupted recovery; partial evidence is not complete output")
            atomic_json(self.stage / "record.json", record)
        return self.finalize(outcome="interrupted", ended_at=None)
