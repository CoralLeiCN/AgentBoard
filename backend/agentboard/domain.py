import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from .timestamps import normalize_timestamp

Timestamp = Annotated[str, BeforeValidator(normalize_timestamp)]


@dataclass(frozen=True)
class RawLine:
    sequence: int
    text: str


@dataclass(frozen=True)
class RawTraceEnd:
    session_id: str
    mapping_version: str


def stable_id(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def is_user_input_tool(name: str) -> bool:
    # Async questions return immediately; their tool lifetime is not a user wait.
    return name.rsplit(".", 1)[-1] == "request_user_input"


class Session(BaseModel):
    id: str
    agent: str = "codex"
    title: str = "Untitled session"
    started_at: Timestamp
    metadata: dict = Field(default_factory=dict)
    identity_kind: Literal["session", "unattributed_trace", "unattributed_resource"] = "session"
    field_lineage: dict = Field(default_factory=dict, exclude=True)


class Event(BaseModel):
    id: str
    session_id: str
    sequence: int
    kind: Literal["user", "assistant", "llm", "tool", "user_wait", "event"]
    name: str
    start_time: Timestamp
    end_time: Timestamp | None = None
    timing: Literal["measured", "estimated", "unknown"] = "unknown"
    source: str = "codex_jsonl"
    turn_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    status: str = "ok"
    text: str = ""
    attributes: dict = Field(default_factory=dict)
    # Import-only provenance; persisted separately from the normalized event.
    raw_line_numbers: list[int] = Field(default_factory=list, exclude=True)
    field_lineage: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("End timestamp precedes start")
        return self
