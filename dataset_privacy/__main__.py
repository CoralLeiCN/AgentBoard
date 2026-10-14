"""Build a private review draft from turn JSON using Codex directly."""

import argparse
import json
import sys
from pathlib import Path

from .codex import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT, CodexConfig, CodexDetector
from .contracts import PrivacyError
from .pipeline import counts, load_source, plan, prepare


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "prepare"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument(
        "--limit",
        type=int,
        help="Select the first N source rows as targets; resolve context from the full source",
    )
    parser.add_argument("--chunk-characters", type=int, default=4096)
    parser.add_argument("--overlap", type=int, default=512)
    parser.add_argument("--max-chunks", type=int, default=256)
    parser.add_argument("--output", type=Path, help="New private run directory; parent must exist")
    parser.add_argument("--model", help=f"Model ID; normal Codex mode defaults to {DEFAULT_MODEL}")
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        help=f"Reasoning effort supported by the selected model (default: {DEFAULT_REASONING_EFFORT})",
    )
    parser.add_argument(
        "--base-url", help="Explicit private endpoint; omitted uses normal Codex configuration/login"
    )
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--api-key-env")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            projection, sha = load_source(args.source, args.limit, args.expected_sha256)
            chunks = plan(projection, args.chunk_characters, args.overlap)
            print(
                json.dumps({"source_sha256": sha, **counts(projection, chunks), "model_calls": 0}, indent=2)
            )
            return 0
        if args.output is None:
            parser.error("prepare requires --output")
        detector = CodexDetector(
            CodexConfig(
                model=args.model,
                base_url=args.base_url,
                executable=args.codex,
                timeout=args.timeout,
                api_key_env=args.api_key_env,
                reasoning_effort=args.reasoning_effort,
            )
        )
        manifest = prepare(
            args.source,
            args.output,
            detector,
            limit=args.limit,
            expected_sha256=args.expected_sha256,
            chunk_characters=args.chunk_characters,
            overlap=args.overlap,
            max_chunks=args.max_chunks,
        )
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "privacy_status": manifest["privacy_status"],
                    "counts": manifest["counts"],
                },
                indent=2,
            )
        )
        return 0
    except (PrivacyError, OSError) as exc:
        # OSError filenames and process output may carry private values: don't echo them.
        reason = str(exc) if isinstance(exc, PrivacyError) else type(exc).__name__
        print(f"Preparation failed: {reason}. Inspect the private run manifest if created.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
