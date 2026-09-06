"""Small reproducible ingestion probe, not a service-scale throughput claim."""

import argparse
import tempfile
import time
import tracemalloc
from pathlib import Path

from agentboard.domain import Event, Session
from agentboard.store import Store
from agentboard.timestamps import format_timestamp

parser = argparse.ArgumentParser()
parser.add_argument("--events", type=int, default=10000)
args = parser.parse_args()


def batch():
    yield Session(id="benchmark", started_at=format_timestamp(1000000000))
    for i in range(args.events):
        yield Event(
            id=str(i),
            session_id="benchmark",
            sequence=i,
            kind="tool",
            name="test",
            start_time=format_timestamp(1000000000 + i * 1000000),
            end_time=format_timestamp(1000000000 + (i + 1) * 1000000),
            timing="measured",
            source="benchmark",
        )


with tempfile.TemporaryDirectory() as tmp:
    store = Store(str(Path(tmp) / "trace.db"))
    tracemalloc.start()
    start = time.perf_counter()
    result = store.ingest(batch())
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    start = time.perf_counter()
    store.stats("benchmark")
    print(
        {
            **result,
            "seconds": round(elapsed, 3),
            "events_per_second": round(args.events / elapsed),
            "peak_python_mib": round(peak / 1024**2, 2),
            "stats_ms": round((time.perf_counter() - start) * 1000, 2),
        }
    )
