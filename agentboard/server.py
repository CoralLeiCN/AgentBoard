"""Carry resolved CLI settings into Uvicorn's reload worker processes."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import uvicorn

from .api import create_app
from .config import Settings

_SETTINGS_ENV = "_AGENTBOARD_RELOAD_SETTINGS"


def create_reload_app():
    values = json.loads(os.environ[_SETTINGS_ENV])
    values["features"] = set(values["features"])
    values["plugins"] = tuple(values["plugins"])
    return create_app(Settings(**values))


def run_reloading(settings, *, host, port):
    # Workers are spawned afresh on each edit. Preserve file settings and CLI
    # overrides instead of letting their factory fall back to the live defaults.
    previous = os.environ.get(_SETTINGS_ENV)
    os.environ[_SETTINGS_ENV] = json.dumps(asdict(settings), default=list)
    try:
        uvicorn.run(
            "agentboard.server:create_reload_app", factory=True,
            host=host, port=port, reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
        )
    finally:
        if previous is None:
            os.environ.pop(_SETTINGS_ENV, None)
        else:
            os.environ[_SETTINGS_ENV] = previous
