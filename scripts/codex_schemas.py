"""Maintain the versioned app-server schema baseline without starting an agent."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1] / "schemas" / "codex-app-server"
GENERATOR = ["app-server", "generate-json-schema", "--out", "generated", "--experimental"]
SOURCE = "https://learn.chatgpt.com/docs/app-server#message-schema"


def check_references(value, document, filename):
    """Check local JSON pointers only; this is not full JSON Schema validation."""
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            if not isinstance(ref, str) or (ref != "#" and not ref.startswith("#/")):
                raise ValueError(f"{filename}: unsupported reference {ref!r}; review the checker")
            target = document
            try:
                for part in unquote(ref[2:]).split("/") if ref != "#" else []:
                    key = part.replace("~1", "/").replace("~0", "~")
                    target = target[int(key)] if isinstance(target, list) else target[key]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError(f"{filename}: unresolved reference {ref}") from exc
        for child in value.values():
            check_references(child, document, filename)
    elif isinstance(value, list):
        for child in value:
            check_references(child, document, filename)


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Schema symlink is not supported: {path}")
        if not path.is_file():
            continue
        if path.suffix != ".json":
            raise ValueError(f"Unexpected generated file: {path}")
        data = path.read_bytes()
        document = json.loads(data)
        if not isinstance(document, (dict, bool)):
            raise ValueError(f"Expected a JSON Schema object or boolean: {path}")
        check_references(document, document, path.name)
        files[path.relative_to(directory).as_posix()] = hashlib.sha256(data).hexdigest()
    if not files:
        raise ValueError(f"No generated schemas in {directory}")
    return files


def verify(root=ROOT):
    if root.is_symlink() or (root / "generated").is_symlink():
        raise ValueError("Schema baseline directories must not be symlinks")
    if {p.name for p in root.iterdir()} != {"manifest.json", "generated"}:
        raise ValueError("Schema baseline must contain only manifest.json and generated/")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("manifest_version") != 1 or manifest.get("generator") != GENERATOR:
        raise ValueError("Unrecognized schema manifest or generator flags")
    if manifest.get("source") != SOURCE or not manifest.get("codex_version", "").startswith("codex-cli "):
        raise ValueError("Schema manifest is missing its Codex provenance")
    if inventory(root / "generated") != manifest.get("files"):
        raise ValueError("Generated schemas differ from manifest hashes; review changes before regenerating")
    return manifest


def run_codex(executable, *args):
    return subprocess.run(
        [executable, *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def generate(root, executable):
    version = run_codex(executable, "--version")
    if not version.startswith("codex-cli "):
        raise ValueError(f"Unrecognized Codex version output: {version!r}")
    output = root / "generated"
    run_codex(executable, "app-server", "generate-json-schema", "--out", str(output), "--experimental")
    if run_codex(executable, "--version") != version:
        raise ValueError("Codex changed version during generation; retry with a fixed executable")
    manifest = {
        "manifest_version": 1,
        "source": SOURCE,
        "codex_version": version,
        "generator": GENERATOR,
        "files": inventory(output),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def differences(before, after):
    old, new = before.get("files", {}), after["files"]
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
    }


def sync(root=ROOT, executable="codex", update=False):
    before = verify(root) if root.exists() else {}
    # Check mode only writes to the OS temp directory, never the repository.
    if update:
        root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="agentboard-codex-schemas-", dir=root.parent if update else None
    ) as tmp:
        stage = Path(tmp) / "snapshot"
        stage.mkdir()
        after = generate(stage, executable)
        delta = differences(before, after)
        print(f"Codex: {before.get('codex_version', 'no baseline')} -> {after['codex_version']}")
        for kind, names in delta.items():
            print(f"{kind}: {len(names)}")
            for name in names[:20]:
                print(f"  {name}")
            if len(names) > 20:
                print(f"  ... {len(names) - 20} more; inspect the generated Git diff after update")
        if before == after:
            print("Schema baseline matches; no changes.")
            return 0
        if not update:
            print("Schema drift detected. Review with the update workflow; baseline was not modified.")
            return 1
        # Replace the complete managed directory so removed upstream files cannot linger.
        # Keep the prior baseline until the new directory has been installed successfully.
        backup = Path(tmp) / "previous"
        if root.exists():
            root.rename(backup)
        try:
            stage.rename(root)
        except OSError:
            if backup.exists():
                backup.rename(root)
            raise
        print("Updated schema baseline. Review the manifest and generated files before committing.")
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "check", "update"))
    parser.add_argument(
        "--codex", default="codex", help="Codex executable to inspect; no upgrade is installed"
    )
    args = parser.parse_args()
    try:
        if args.command == "verify":
            manifest = verify()
            print(f"Verified {len(manifest['files'])} schemas from {manifest['codex_version']} (offline).")
            return 0
        return sync(executable=args.codex, update=args.command == "update")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Schema workflow failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
