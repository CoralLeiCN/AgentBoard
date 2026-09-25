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
    print(json.dumps(result, indent=2))
