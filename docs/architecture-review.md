# Architecture and specification review

Reviewed and implemented 2026-09-08 against the [principles](principles.md), [specification](specification.md) and [modular feature plan](modular-features-plan.md). Scope: capture correctness, first-party modular boundaries, shared HTTP/CLI behavior, performance structure, compatibility and feature verification. Existing inference/analysis gaps remain in the [gap register](data-quality-gaps.md).

## Findings and changes

| Finding | Implemented correction |
| --- | --- |
| Disabling raw inspection discarded future evidence; malformed imports rolled archives back; OTLP lost unknown wire fields | Schema v9 captures complete original payloads before interpretation, independently of analysis flags. Capture commits use SQLite `FULL`; failed normalization retains bytes and an attempt outcome. Explicit CLI reprocessing uses retained evidence. |
| Experimental dynamic Python plugins exceeded the agreed first-party scope | Static frozen feature/adapter declarations replace module loading. Legacy plugin configuration fails clearly. Metadata listing does no storage/client/parser work. |
| Features could mutate the application or access the raw store | Each capability returns a router using specific named services. Core owns route validation, auth, schema, UI placement, admission and lifecycle. |
| Analysis depended on inspection | Lineage and token usage now read core raw data without `raw_archive`. Only optional derived work is skipped when disabled; canonical correctness remains active. |
| Raw streaming relied on SQLite thread affinity | Reproduced failure when successive HTTP iterator steps run on different workers. Read-only streaming connections now permit sequential worker handoff; ordinary write connections retain thread affinity. Regression covers capture BLOBs and line archives. |
| Performance and implementation claims were insufficiently grounded | Record fixed before/after profiles below; synchronize current-behavior docs, configuration migration and remaining verification gaps. Grafana findings are linked in the [design plan](modular-features-plan.md#what-grafana-teaches). |

## Measurements

Run `uv run --extra dev python examples/feature_benchmark.py --requests 20`. [Recorded JSON](measurements/2026-09-08-feature-profiles.json) includes exact source fingerprints and Python identity. The after measurement includes mandatory capture with SQLite `FULL` commits; subsequent diagnostic and streaming-thread fixes do not change the measured import/read workload.

Environment: macOS ARM64, Python 3.13.3. Each profile starts a fresh process/database, using 20 repeats of the 5,408-byte synthetic Codex fixture and session-list reads in two worker threads. The empty profile reads an empty database. Startup measures app construction/lifespan after Python/module import, not whole-process cold start. The model profile enables all features with dummy mode and performs no model call. Measurements are single runs, sensitive to scheduling/cache noise.

Values are **before → after**:

| Profile | App startup ms | Import median ms | Query p95 ms | Repeated input bytes/s | Peak RSS MiB | DB/disk bytes |
| --- | --- | --- | --- | --- | --- | --- |
| Empty | 28.77 → 19.06 | — | 2.66 → 2.40 | — | 56.59 → 55.12 | 118,784 → 135,168 |
| Import only | 28.77 → 21.37 | 7.16 → 7.42 | 4.65 → 3.38 | 640,909 → 658,732 | 58.20 → 55.84 | 126,976 → 159,744 |
| Default tracing | 31.85 → 26.14 | 11.50 → 11.28 | 3.75 → 3.92 | 363,263 → 444,112 | 58.80 → 56.91 | 262,144 → 282,624 |
| Model features | 37.68 → 30.92 | 11.22 → 11.83 | 3.43 → 3.23 | 428,433 → 418,810 | 59.48 → 57.41 | 262,144 → 282,624 |

CPU seconds for the same runs: empty 0.050 → 0.040; import-only 0.202 → 0.193; default 0.327 → 0.275; model profile 0.298 → 0.295. The JSON also records medians, wall time and inserted counts. Each ingestion profile inserts 24 unique events; repeated-input throughput is mostly deduplicated retries, not sustained unique-event capacity.

The old import-only profile retained **zero** full source bytes, so it is not an equivalent completeness/durability workload. All new ingestion profiles retain the full 5,408-byte source plus attempt metadata; successful Codex imports also keep the line archive for evidence indexing. Storage increased, while startup decreased in these runs. This does not establish a statistically significant overall speedup.

PERF-01 remains partially verified. Still needed: agreed throughput/latency/resource budgets, growing representative datasets, existing-data empty-profile timing, sustained concurrent writes/reads, stats/export tail latency, filesystem write volume, optional-analysis load and upstream agent overhead. One SQLite writer, full snapshot storage, duplicate transport/line archives, uncached usage/unified scans and parser pending state are concrete scaling costs. Measure these before selecting lossless storage optimization, materialization or another implementation language.

## Verification

Final automated result: **417 backend tests passed, 5 skipped; 36 frontend tests passed.**

- Backend suite exercises all 13 capabilities, dependency-valid singleton/chain profiles, disabled HTTP/OpenAPI/CLI behavior, migration, lazy imports, lifecycle failure, raw completeness and reprocessing. It also retains timing, attribution, lineage, usage/pricing, auth, replay, native protocol and stored-schema integrity coverage.
- Frontend Node suite verifies rendering, capability guards/requests, provenance, usage, classification and view persistence. Lint and whitespace checks pass.
- Wheel build and extracted-wheel smoke verify feature modules, capture services and bundled UI, including a failed import whose original bytes can be downloaded.
- Codex in-app browser uses only the synthetic isolated review database on port 4319. Default tracing, model-enabled, empty and selective (`import`, `field_lineage`, `token_usage`) profiles are checked. Existing exporters and the live collector are unchanged.

Browser checks cover demo import; session/event browsing and filtering; timeline/parallel labels; raw, normalized and field-evidence views; usage; single/page dummy classification; native plan generation; dummy edited replay; malformed-file capture diagnostics; and desktop/narrow layouts. The empty profile keeps browsing with no optional controls or requests. The selective profile calculates usage and field evidence without raw inspection or export. Server access logs and frontend request-guard tests verify disabled endpoints are not requested.

Browser export actions reached successful API responses, and exact export content is covered by backend tests. Codex's browser did not emit a download event for the Blob download, so saving a file to the user's download folder is not verified. Live external-model calls and actual native Codex execution are outside this run: five opt-in external-service tests skip without endpoint configuration; dummy/mock/fake-protocol coverage is explicit. Representative browser checks are not exhaustive accessibility or capacity certification.

## Upgrade and operational effects

Stop older service processes before opening schema v9. Migration preserves old archives/rows and creates capture tables; it does not reconstruct uncaptured history or renormalize old events. Remove the experimental `plugins` TOML setting and unset `AGENTBOARD_PLUGINS`. Feature IDs remain stable, with `raw_archive` now controlling inspection only.

Capture and reprocessing outcomes are available through the API/CLI; no dedicated capture-list dashboard or automatic retry job is added. Retained payloads support future interpretation but existing merge rules still reject or preserve conflicting rewritten history. Cross-source field lineage, unsupported-record accounting and analysis snapshots remain documented correctness work. Storage has no pruning/deletion policy.
