"""Separate experiment configuration and CLI; never constructs the trace Runtime."""

import json
import os
import sys
import tomllib
from pathlib import Path

from .archive import Archive


def configure(commands):
    parser = commands.add_parser("experiments", help="Record and inspect offline experiment archives")
    parser.add_argument("--data-home", type=Path)
    parser.add_argument("--archive-config", type=Path, help="Machine TOML (default: ~/.agentboard/config.toml)")
    parser.add_argument("--mode", choices=("filesystem", "mlflow"))
    sub = parser.add_subparsers(dest="experiment_command", required=True)
    sub.add_parser("list")
    for name in ("inspect", "verify", "recover"):
        sub.add_parser(name).add_argument("id")
    read = sub.add_parser("read", help="Verify and stream one artifact without executing it")
    read.add_argument("id")
    read.add_argument("path")
    report = sub.add_parser("coverage", help="Regenerate classification coverage from pinned saved inputs")
    report.add_argument("recipe", type=Path)
    curate = sub.add_parser("curate", help="Prepare label review and embedding-based duplicate suggestions")
    source = curate.add_mutually_exclusive_group(required=True)
    source.add_argument("--recipe", type=Path)
    source.add_argument("--current", action="store_true", help="Use the pinned current coverage recipe and Sol reference")
    curate.add_argument("--similarity-threshold", type=float)
    curate.add_argument("--embedding-provider", choices=("local", "remote"), help="Default: local Sentence Transformers")
    curate.add_argument("--embedding-model", help="Local Hugging Face namespace/model or remote model ID")
    curate.add_argument("--embedding-revision", help="Pinned local model commit SHA")
    curate.add_argument("--embedding-batch-size", type=int)
    curate.add_argument("--embedding-base-url", help="Remote API base URL; requests POST <base>/embeddings")
    curate.add_argument("--embedding-api-key-env", help="Environment variable containing the remote bearer token")
    curate.add_argument("--embedding-timeout-seconds", type=float)
    sub.add_parser("curations", help="List saved review workspaces")
    review = sub.add_parser("review", help="Serve the local dataset review UI (no models or trace database)")
    review.add_argument("id")
    review.add_argument("--port", type=int, default=4320)
    export = sub.add_parser("curation-export", help="Save an immutable curated dataset with all decision history")
    export.add_argument("id")
    export.add_argument("--revision", type=int, required=True)
    return parser


def archive_settings(args):
    config_path = args.archive_config or Path.home() / ".agentboard/config.toml"
    values = {}
    if config_path.exists() or args.archive_config:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
        if set(config) - {"experiments"}:
            raise ValueError("Archive config supports only [experiments]")
        values = config.get("experiments", {})
        if not isinstance(values, dict) or values.keys() - {"data_home", "mode"}:
            raise ValueError("Unknown archive configuration field")
        if any(not isinstance(v, str) for v in values.values()):
            raise ValueError("Archive configuration values must be strings")
    mode = args.mode or values.get("mode", os.getenv("AGENTBOARD_EXPERIMENT_MODE", "filesystem"))
    if mode != "filesystem":
        raise ValueError("Only filesystem experiment mode is implemented; MLflow publication is unavailable")
    data_home = args.data_home or values.get("data_home", os.getenv("AGENTBOARD_DATA_HOME"))
    return Archive(data_home)


def execute(args):
    archive = archive_settings(args)
    command = args.experiment_command
    if command == "list":
        result = archive.list()
    elif command == "inspect":
        result = archive.inspect(args.id)
    elif command == "verify":
        result = archive.verify(args.id)
    elif command == "recover":
        result = archive.recorder(args.id).recover()
    elif command == "read":
        reference = archive.reference(args.id, args.path)
        with archive.resolve(reference).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sys.stdout.buffer.write(chunk)
        return
    elif command == "coverage":
        from .coverage import record_coverage

        result = record_coverage(archive, json.loads(args.recipe.read_bytes()))
    elif command in ("curate", "curations", "review", "curation-export"):
        from .curation import CurationStore

        store = CurationStore(archive)
        if command == "curate":
            recipe = store.current_recipe() if args.current else json.loads(args.recipe.read_bytes())
            if args.similarity_threshold is not None:
                recipe["similarity_threshold"] = args.similarity_threshold
            for field in ("embedding_provider", "embedding_model", "embedding_revision", "embedding_batch_size",
                          "embedding_base_url", "embedding_api_key_env", "embedding_timeout_seconds"):
                value = getattr(args, field, None)
                if value is not None:
                    recipe[field] = value
            result = store.create(recipe)
        elif command == "curations":
            result = store.list()
        elif command == "curation-export":
            result = store.export(args.id, args.revision)
        else:
            import uvicorn

            from .curation_api import create_review_app

            if not 1 <= args.port <= 65535 or args.port == 4318:
                raise ValueError("Use a valid review port other than live collector port 4318")
            uvicorn.run(create_review_app(store, args.id), host="127.0.0.1", port=args.port)
            return
    print(json.dumps(result, indent=2))
