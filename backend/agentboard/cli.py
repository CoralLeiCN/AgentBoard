import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .adapters import adapters
from .config import Settings
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="AgentBoard: local-first coding-agent traces")
    parser.add_argument("--config", type=Path, help="TOML settings file; explicit values override environment defaults")
    parser.add_argument("--database", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the API and trace explorer")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    ingest = commands.add_parser("import", help="Stream Codex rollout files or directories into storage")
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--agent", default="codex", choices=list(adapters()))
    export = commands.add_parser("export", help="Stream normalized events as JSONL")
    export.add_argument("session_id")
    export_mode = export.add_mutually_exclusive_group()
    export_mode.add_argument("--inputs-only", action="store_true")
    export_mode.add_argument("--raw", action="store_true", help="Export the latest archived rollout exactly")
    export.add_argument("--import-id", type=int, help="Select a raw archive version (requires --raw)")
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
    args = parser.parse_args()
    try:
        settings = Settings.from_file(args.config) if args.config else Settings()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.database:
        settings.database = args.database
    if args.command == "serve":
        host = args.host if args.host is not None else settings.host
        port = args.port if args.port is not None else settings.port
        if host not in ("127.0.0.1", "localhost", "::1") and not settings.api_token:
            parser.error("Set AGENTBOARD_API_TOKEN before binding outside loopback")
        import uvicorn

        from .api import create_app

        print(f"AgentBoard {settings.environment}: http://{host}:{port} · {settings.database} · "
              f"live OTLP {'enabled' if settings.otlp_enabled else 'disabled'}", file=sys.stderr)
        if settings.reload:
            from .server import run_reloading

            run_reloading(settings, host=host, port=port)
        else:
            uvicorn.run(create_app(settings), host=host, port=port)
        return
    if args.command == "snapshot":
        from .snapshot import snapshot_database

        try:
            print(json.dumps(snapshot_database(args.source, settings.database), indent=2))
        except (OSError, ValueError, sqlite3.Error) as exc:
            parser.error(str(exc))
        return
    store = Store(settings.database)
    if args.command == "repair-otlp-sessions":
        print(json.dumps(store.repair_otlp_sessions()))
    elif args.command == "import":
        processed = failures = inserted = no_new_events = 0
        sessions = set()
        for path in args.paths:
            files = path.rglob("*.jsonl") if path.is_dir() else [path]
            for file in files:
                processed += 1
                try:
                    with file.open(encoding="utf-8", newline="") as lines:
                        result = store.ingest(adapters()[args.agent].parse(lines))
                    sessions.update(result["session_ids"])
                    inserted += result["inserted_events"]
                    no_new_events += result["inserted_events"] == 0
                    print(json.dumps({"file": str(file), **result}))
                except (OSError, ValueError) as exc:
                    failures += 1
                    print(json.dumps({"file": str(file), "error": str(exc)}))
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
        if args.import_id is not None and not args.raw:
            parser.error("--import-id requires --raw")
        store.get_session(args.session_id)
        if args.raw:
            for chunk in store.export_raw(args.session_id, args.import_id):
                sys.stdout.buffer.write(chunk)
            return
        for event in store.export(args.session_id):
            if not args.inputs_only or event["kind"] == "user":
                print(json.dumps(event))
    elif args.command == "resume":
        if "replay" not in settings.features:
            parser.error("The replay feature is disabled")
        from .resume import execute_plan, make_plan

        plan = make_plan(store, args.session_id, args.input_id, args.prompt)
        print(json.dumps(execute_plan(plan, args.cwd) if args.execute else plan, indent=2))
