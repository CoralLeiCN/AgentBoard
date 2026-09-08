import argparse
import heapq
import json
import sqlite3
import sys
from pathlib import Path

from .config import Settings
from .features import load_catalog
from .runtime import Runtime


def positive_count(value):
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if count < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return count


def recent_file_key(path):
    try:
        return (False, -path.stat().st_mtime_ns, str(path.absolute()))
    except OSError:
        # Keep unavailable paths eligible for the normal per-file error handling.
        return (True, 0, str(path.absolute()))


def import_files(paths, limit=None):
    files = (file for path in paths for file in (path.rglob("*.jsonl") if path.is_dir() else [path]))
    # Select globally without retaining every discovered path or reading file contents.
    return files if limit is None else heapq.nsmallest(limit, files, key=recent_file_key)


def main():
    parser = argparse.ArgumentParser(description="AgentBoard: local-first coding-agent traces")
    parser.add_argument("--config", type=Path, help="TOML settings file; explicit values override environment defaults")
    parser.add_argument("--database", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("features", help="List available features and the configured allowlist as JSON")
    serve = commands.add_parser("serve", help="Serve the API and trace explorer")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    ingest = commands.add_parser("import", help="Stream Codex rollout files or directories into storage")
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--agent", default="codex", help="Built-in agent adapter (default: codex)")
    ingest.add_argument(
        "--limit", type=positive_count, metavar="N",
        help="Attempt at most N files across all paths, newest modification time first (default: all files)",
    )
    export = commands.add_parser("export", help="Stream normalized events as JSONL")
    export.add_argument("session_id")
    export_mode = export.add_mutually_exclusive_group()
    export_mode.add_argument("--inputs-only", action="store_true")
    export_mode.add_argument("--raw", action="store_true", help="Export the latest archived rollout exactly")
    export.add_argument("--import-id", type=int, help="Select a raw archive version (requires --raw)")
    classify = commands.add_parser("classify", help="Classify session purposes with the configured model")
    classify.add_argument("session_ids", nargs="*", help="Session IDs to classify")
    classify.add_argument("--all", action="store_true", help="Classify all unclassified sessions")
    classify.add_argument("--limit", type=int, help="Maximum sessions to select with --all (newest first)")
    classify.add_argument("--force", action="store_true", help="Replace existing classifications too")
    commands.add_parser("repair-otlp-sessions", help="Repair session associations from preserved OTLP spans")
    snapshot = commands.add_parser("snapshot", help="Copy a live SQLite database into a new isolated database")
    snapshot.add_argument("--source", required=True, type=Path, help="Existing database opened read-only")
    resume = commands.add_parser("resume", help="Prepare or execute a native Codex branch with edited input")
    resume.add_argument("session_id")
    resume.add_argument("input_id")
    resume.add_argument("--prompt", required=True)
    resume.add_argument("--cwd", type=Path, default=Path.cwd())
    resume.add_argument(
        "--execute", action="store_true", help="Actually start Codex in a new read-only branch"
    )
    captures = commands.add_parser("captures", help="List retained raw capture outcomes")
    captures.add_argument("--after", type=int, default=0)
    captures.add_argument("--limit", type=positive_count, default=50)
    capture_export = commands.add_parser("capture-export", help="Export the exact captured source payload")
    capture_export.add_argument("capture_id", type=positive_count)
    reprocess = commands.add_parser("reprocess", help="Explicitly normalize retained raw data with current mappings")
    reprocess.add_argument("capture_id", type=positive_count)
    args = parser.parse_args()
    try:
        settings = Settings.from_file(args.config) if args.config else Settings()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.database:
        settings.database = args.database
    try:
        catalog = load_catalog(settings)
    except ValueError as exc:
        parser.error(str(exc))
    required = {"import": "import", "export": "export", "classify": "classification", "resume": "native_resume"}
    required.update({"captures": "raw_archive", "capture-export": "raw_archive"})
    if args.command == "capture-export" and "export" not in settings.features:
        parser.error("The export feature is disabled")
    if args.command in required and required[args.command] not in settings.features:
        parser.error(f"The {required[args.command]} feature is disabled")
    if args.command == "features":
        print(json.dumps(catalog.catalog(settings.features), indent=2))
        return
    if args.command == "import" and args.agent not in catalog.adapters(settings.features):
        parser.error(f"Unknown or disabled adapter: {args.agent}")
    if args.command == "export":
        if args.import_id is not None and not args.raw:
            parser.error("--import-id requires --raw")
        if args.raw and "raw_archive" not in settings.features:
            parser.error("The raw_archive feature is disabled")
        if args.inputs_only and "inputs" not in settings.features:
            parser.error("The inputs feature is disabled")
    if args.command == "classify":
        if bool(args.session_ids) == args.all:
            parser.error("Provide session IDs or --all")
        if args.limit is not None and (not args.all or args.limit < 1):
            parser.error("--limit requires --all and a positive integer")
    if args.command == "serve":
        host = args.host if args.host is not None else settings.host
        port = args.port if args.port is not None else settings.port
        if host not in ("127.0.0.1", "localhost", "::1") and not settings.api_token:
            parser.error("Set AGENTBOARD_API_TOKEN before binding outside loopback")
        import uvicorn

        from .api import create_app

        signals = [name.removeprefix("otlp_") for name in ("otlp_logs", "otlp_traces") if name in settings.features]
        print(f"AgentBoard {settings.environment}: http://{host}:{port} · {settings.database} · "
              f"live OTLP {', '.join(signals) if signals else 'disabled'}", file=sys.stderr)
        if settings.reload:
            from .server import run_reloading

            run_reloading(settings, host=host, port=port)
        else:
            uvicorn.run(create_app(settings, catalog=catalog), host=host, port=port)
        return
    if args.command == "snapshot":
        from .snapshot import snapshot_database

        try:
            print(json.dumps(snapshot_database(args.source, settings.database), indent=2))
        except (OSError, ValueError, sqlite3.Error) as exc:
            parser.error(str(exc))
        return
    with Runtime(settings, catalog) as runtime:
        store = runtime.store
        if args.command == "classify":
            from .models import ModelServiceError

            service = runtime.services_for("classification")
            selected = (store.classification_candidates(args.limit, args.force) if args.all
                        else list(dict.fromkeys(args.session_ids)))
            completed = skipped = failures = 0
            for sid in selected:
                try:
                    if store.get_session(sid)["classification"] and not args.force:
                        skipped += 1
                        result = {"status": "skipped", "reason": "Already classified; use --force to replace"}
                    else:
                        classification = service.run(sid)
                        result = {"status": "classified", "classification": classification}
                        completed += 1
                except (KeyError, ValueError, ModelServiceError, sqlite3.Error) as exc:
                    failures += 1
                    result = {"status": "error", "error": str(exc)}
                print(json.dumps({"session_id": sid, **result}), flush=True)
            print(f"Classification: {completed} classified, {skipped} skipped, {failures} failed", file=sys.stderr)
            if failures:
                raise SystemExit(1)
        elif args.command == "repair-otlp-sessions":
            print(json.dumps(store.repair_otlp_sessions()))
        elif args.command == "import":
            processed = failures = inserted = no_new_events = 0
            sessions = set()
            for file in import_files(args.paths, args.limit):
                processed += 1
                try:
                    with file.open("rb") as lines:
                        result = runtime.ingestion.import_file(args.agent, lines)
                    sessions.update(result["session_ids"])
                    inserted += result["inserted_events"]
                    no_new_events += result["inserted_events"] == 0
                    print(json.dumps({"file": str(file), **result}))
                except (OSError, ValueError, sqlite3.Error) as exc:
                    failures += 1
                    print(json.dumps({"file": str(file), "error": str(exc),
                                      **({"capture_id": exc.capture_id, "capture_status": "retained",
                                          "normalization_status": "failed"} if hasattr(exc, "capture_id") else {})}))
            # Keep stdout as per-file JSONL for scripts; show the summary after those records.
            sys.stdout.flush()
            print(
                f"\nImport summary:\n"
                f"  Files: {processed} processed, {processed - failures} succeeded, {failures} failed\n"
                f"  Unique sessions: {len(sessions)}\n"
                f"  New events: {inserted}\n"
                f"  Files with no new events: {no_new_events}",
                file=sys.stderr,
            )
            if failures:
                raise SystemExit(1)
        elif args.command == "export":
            service = runtime.services_for("export")
            service.get_session(args.session_id)
            if args.raw:
                for chunk in runtime.services_for("raw_archive").export_raw(args.session_id, args.import_id):
                    sys.stdout.buffer.write(chunk)
                return
            for event in service.export(args.session_id):
                if not args.inputs_only or event["kind"] == "user":
                    print(json.dumps(event))
        elif args.command == "resume":
            from .resume import execute_plan

            plan = runtime.services_for("native_resume").plan(args.session_id, args.input_id, args.prompt)
            print(json.dumps(execute_plan(plan, args.cwd) if args.execute else plan, indent=2))
        elif args.command == "captures":
            print(json.dumps(runtime.services_for("raw_archive").captures(args.limit, args.after), indent=2))
        elif args.command == "capture-export":
            for chunk in runtime.services_for("raw_archive").export_capture(args.capture_id):
                sys.stdout.buffer.write(chunk)
        elif args.command == "reprocess":
            try:
                print(json.dumps(runtime.ingestion.reprocess(args.capture_id)))
            except (ValueError, KeyError) as exc:
                parser.error(str(exc))
