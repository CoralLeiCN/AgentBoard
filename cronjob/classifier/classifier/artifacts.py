"""Versioned, verified local experiment artifacts (never pickle or remote code)."""

import hashlib
import importlib.metadata
import json
import platform
from dataclasses import asdict
from pathlib import Path

VERSION = "turn-classifiers-v1"


def config_dict(config):
    """Snapshot a script's dataclass as JSON-compatible artifact metadata."""
    def path_value(value):
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Unsupported configuration value: {type(value).__name__}")

    return json.loads(json.dumps(asdict(config), default=path_value, allow_nan=False))


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def read_rows(path):
    path = Path(path)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    value = read_json(path)
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array or JSONL rows")
    return value


def write_json(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value))
    Path(path).chmod(0o600)


def write_rows(path, rows):
    with Path(path).open("xb") as stream:
        for row in rows:
            stream.write(encoded(row))
    Path(path).chmod(0o600)


def new_directory(path):
    path = Path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    return path


def seal(path, kind, **metadata):
    manifest = {"format_version": VERSION, "kind": kind, **metadata, "files": inventory(path)}
    write_json(Path(path) / "manifest.json", manifest)
    return manifest


def verify(path, kind):
    path = Path(path)
    manifest = read_json(path / "manifest.json")
    if manifest.get("format_version") != VERSION or manifest.get("kind") != kind:
        raise ValueError("Incompatible classifier artifact")
    actual = [f for f in inventory(path) if f["path"] != "manifest.json"]
    if actual != manifest["files"]:
        raise ValueError("Classifier artifact hash mismatch or unexpected files")
    return manifest


def fingerprint(path):
    return digest(inventory(path))


def environment():
    result = {"python": platform.python_version()}
    for name in ("torch", "transformers", "sentence-transformers", "lightgbm", "numpy"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def manifest_hash(path):
    return sha256(Path(path) / "manifest.json")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root):
    """Hash every regular file without following evidence symlinks."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a directory without symlinks")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink found in evidence inventory")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Only regular evidence files are supported")
        before = path.stat()
        checksum = sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed while hashing")
        entries.append({"path": path.relative_to(root).as_posix(), "bytes": after.st_size, "sha256": checksum})
    return sorted(entries, key=lambda entry: entry["path"])
