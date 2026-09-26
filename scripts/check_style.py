"""Check formatting in the adopted curation and classifier scopes; use --fix to apply it."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PATHS = (
    "backend/agentboard/experiments/curation.py",
    "backend/agentboard/experiments/curation_api.py",
    "backend/agentboard/experiments/embedding_similarity.py",
    "backend/tests/curation_fixtures.py",
    "backend/tests/test_curation.py",
    "backend/tests/test_embedding_similarity.py",
    "backend/tests/test_remote_embeddings.py",
    "backend/tests/test_embedding_endpoint_e2e.py",
    "examples/dataset_curation.py",
    "scripts/check_style.py",
    "cronjob/classifier",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="Apply Ruff and Prettier formatting")
    args = parser.parse_args()
    npm = shutil.which("npm")
    if not npm or not (ROOT / "node_modules/prettier").is_dir():
        print("Install Node.js/npm and run npm ci before checking frontend formatting.", file=sys.stderr)
        return 1
    commands = [
        [sys.executable, "-m", "ruff", "format", *([] if args.fix else ["--check"]), *PYTHON_PATHS],
        [npm, "run", "format" if args.fix else "format:check"],
    ]
    failed = False
    for command in commands:
        failed |= subprocess.run(command, cwd=ROOT, check=False).returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
