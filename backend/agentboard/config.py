import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import get_origin, get_type_hints

from .features import DEFAULT_FEATURES


def environment_list(name, default=""):
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


@dataclass
class Settings:
    environment: str = "local"
    host: str = "127.0.0.1"
    port: int = 4318
    reload: bool = False
    database: str = field(default_factory=lambda: os.getenv("AGENTBOARD_DATABASE", "agentboard.db"))
    features: set[str] = field(
        default_factory=lambda: set(environment_list("AGENTBOARD_FEATURES", ",".join(sorted(DEFAULT_FEATURES))))
    )
    api_token: str = field(default_factory=lambda: os.getenv("AGENTBOARD_API_TOKEN", ""))
    model_base_url: str = field(
        default_factory=lambda: os.getenv("AGENTBOARD_MODEL_BASE_URL", "http://localhost:30000/v1")
    )
    model: str = field(default_factory=lambda: os.getenv("AGENTBOARD_MODEL", ""))
    model_key: str = field(default_factory=lambda: os.getenv("AGENTBOARD_MODEL_KEY", "local"))
    model_mode: str = field(default_factory=lambda: os.getenv("AGENTBOARD_MODEL_MODE", "auto"))
    model_api: str = field(default_factory=lambda: os.getenv("AGENTBOARD_MODEL_API", "chat_completions"))
    model_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("AGENTBOARD_MODEL_TIMEOUT_SECONDS", "30")))
    max_body_bytes: int = 32 * 1024 * 1024
    ingest_concurrency: int = 4
    max_model_chars: int = 60000
    allowed_hosts: list[str] = field(
        default_factory=lambda: os.getenv(
            "AGENTBOARD_ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver"
        ).split(",")
    )
    def __post_init__(self):
        if os.getenv("AGENTBOARD_PLUGINS"):
            raise ValueError("AGENTBOARD_PLUGINS is no longer supported; use built-in features")
        if self.max_body_bytes <= 0 or self.ingest_concurrency <= 0:
            raise ValueError("Body limit and ingestion concurrency must be positive")

    @classmethod
    def from_file(cls, path):
        """Explicit TOML values override environment defaults; paths are file-relative."""
        path = Path(path).expanduser().resolve()
        with path.open("rb") as handle:
            values = tomllib.load(handle)
        known = {f.name for f in fields(cls)}
        unknown = values.keys() - known
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        types = get_type_hints(cls)
        for name, value in values.items():
            expected = types[name]
            collection = get_origin(expected)
            if collection in (set, list, tuple):
                if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                    raise ValueError(f"{name} must be an array of strings")
                values[name] = collection(value)
            elif type(value) is not expected:
                raise ValueError(f"{name} must be {expected.__name__}")
        if "database" in values:
            database = Path(values["database"]).expanduser()
            values["database"] = str((path.parent / database).resolve())
        settings = cls(**values)
        if not 1 <= settings.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return settings
