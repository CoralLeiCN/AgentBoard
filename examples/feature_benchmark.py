"""Repeatable synthetic HTTP workload; no live collector or production capacity claims."""

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROFILES = {
    "empty": set(),
    "import_only": {"import"},
    "default": None,
    "models": None,
}


def measure(profile, count):
    from fastapi.testclient import TestClient

    from agentboard.api import create_app
    from agentboard.config import Settings
    from agentboard.features import DEFAULT_FEATURES

    body = (Path(__file__).parent / "fixtures/codex-session.jsonl").read_bytes()
    features = PROFILES[profile]
    if features is None:
        features = set(DEFAULT_FEATURES)
    if profile == "models":
        features |= {"classification", "replay", "native_resume"}
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "benchmark.db"
        started = time.perf_counter()
        cpu = time.process_time()
        app = create_app(Settings(database=str(database), features=features, model_mode="dummy"))
        with TestClient(app) as client:
            startup_ms = (time.perf_counter() - started) * 1000
            durations = []

            def query(_):
                start = time.perf_counter()
                response = client.get("/api/v1/sessions")
                assert response.status_code == 200
                return (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                queries = pool.map(query, range(count))
                inserted = 0
                if "import" in features:
                    for _ in range(count):
                        tick = time.perf_counter()
                        response = client.post("/api/v1/import/codex", content=body)
                        assert response.status_code == 200, response.text
                        inserted += response.json()["inserted_events"]
                        durations.append((time.perf_counter() - tick) * 1000)
                query_ms = list(queries)
            elapsed = time.perf_counter() - start
            with app.state.store.connect() as db:
                archived = db.execute("SELECT coalesce(sum(byte_count),0) FROM raw_imports").fetchone()[0]
            return {
                "profile": profile, "requests_per_kind": count, "fixture_bytes": len(body),
                "startup_ms": round(startup_ms, 2), "wall_seconds": round(elapsed, 3),
                "cpu_seconds": round(time.process_time() - cpu, 3),
                "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                      / (1024**2 if sys.platform == "darwin" else 1024), 2),
                "disk_bytes": sum(p.stat().st_size for p in Path(tmp).iterdir()),
                "query_median_ms": round(statistics.median(query_ms), 2),
                "query_p95_ms": round(sorted(query_ms)[int((len(query_ms) - 1) * .95)], 2),
                "import_median_ms": round(statistics.median(durations), 2) if durations else None,
                "input_bytes_per_second": round(len(body) * count / elapsed) if durations else None,
                "inserted_events": inserted, "archived_source_bytes": archived,
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--requests", type=int, default=20)
    args = parser.parse_args()
    if args.requests < 2:
        parser.error("--requests must be at least 2")
    if args.profile:
        print(json.dumps(measure(args.profile, args.requests)))
    else:
        source = Path(__file__).resolve().parents[1] / "backend/agentboard"
        digest = hashlib.sha256()
        for path in sorted(source.rglob("*.py")):
            digest.update(str(path.relative_to(source)).encode())
            digest.update(path.read_bytes())
        print(json.dumps({
            "source_sha256": digest.hexdigest(), "python": sys.version,
            "workload": "Synthetic fixed-file retries with concurrent session-list reads; each profile uses a fresh process/database.",
            "profiles": [json.loads(subprocess.check_output([
                sys.executable, __file__, "--profile", profile, "--requests", str(args.requests),
            ])) for profile in PROFILES],
        }, indent=2))
