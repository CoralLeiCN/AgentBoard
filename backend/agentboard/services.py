"""Core-owned, feature-specific interfaces. No application or repository handles."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class InputServices:
    get_session: Callable
    events: Callable


@dataclass(frozen=True)
class UnifiedServices:
    unified: Callable


@dataclass(frozen=True)
class UsageServices:
    usage: Callable
    pricing: Callable
    export_enabled: bool


@dataclass(frozen=True)
class ParallelServices:
    parallel_groups: Callable


@dataclass(frozen=True)
class ExportServices:
    get_session: Callable
    export: Callable


@dataclass(frozen=True)
class ArchiveServices:
    get_session: Callable
    raw_imports: Callable
    event_raw: Callable
    raw_import: Callable
    export_raw: Callable
    captures: Callable
    capture: Callable
    export_capture: Callable
    export_enabled: bool


@dataclass(frozen=True)
class LineageServices:
    field_lineage: Callable


@dataclass(frozen=True)
class ClassificationServices:
    get_session: Callable
    classify: Callable
    run: Callable
    input: Callable


@dataclass(frozen=True)
class ReplayServices:
    run: Callable


@dataclass(frozen=True)
class ResumeServices:
    plan: Callable


@dataclass(frozen=True)
class ImportServices:
    adapters: frozenset[str]
    receive: Callable


@dataclass(frozen=True)
class ReceiverServices:
    receive: Callable
