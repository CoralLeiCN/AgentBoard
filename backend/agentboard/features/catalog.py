"""Immutable built-in declarations. Importing this module performs no service work."""

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    description: str
    requires: frozenset[str] = frozenset()
    default_enabled: bool = True
    router_factory: Callable | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name) or not self.description.strip():
            raise ValueError("Invalid feature declaration")
        object.__setattr__(self, "requires", frozenset(self.requires))


def _import(services):
    from .importing import router

    return router(services)


def _otlp_logs(services):
    from .otlp_logs import router

    return router(services)


def _otlp_traces(services):
    from .otlp_traces import router

    return router(services)


def _raw_archive(services):
    from .raw_archive import router

    return router(services)


def _field_lineage(services):
    from .field_lineage import router

    return router(services)


def _unified_timeline(services):
    from .unified_timeline import router

    return router(services)


def _parallel_groups(services):
    from .parallel_groups import router

    return router(services)


def _token_usage(services):
    from .token_usage import router

    return router(services)


def _inputs(services):
    from .inputs import router

    return router(services)


def _export(services):
    from .export import router

    return router(services)


def _classification(services):
    from .classification import router

    return router(services)


def _replay(services):
    from .replay import router

    return router(services)


def _native_resume(services):
    from .native_resume import router

    return router(services)


BUILTIN_FEATURES = (
    FeatureDefinition('import', 'Import rollout files through HTTP and CLI.', frozenset(()), True, _import),
    FeatureDefinition('otlp_logs', 'Collect complete OTLP log payloads.', frozenset(()), True, _otlp_logs),
    FeatureDefinition('otlp_traces', 'Collect complete OTLP trace payloads.', frozenset(()), True, _otlp_traces),
    FeatureDefinition('raw_archive', 'Inspect retained source archives and capture outcomes.', frozenset(()), True, _raw_archive),
    FeatureDefinition('field_lineage', 'Calculate and inspect field provenance.', frozenset(()), True, _field_lineage),
    FeatureDefinition('unified_timeline', 'Correlate rollout events and measured item timings.', frozenset(()), True, _unified_timeline),
    FeatureDefinition('parallel_groups', 'Infer overlapping tool activity.', frozenset(()), True, _parallel_groups),
    FeatureDefinition('token_usage', 'Inspect token usage and estimated costs.', frozenset(()), True, _token_usage),
    FeatureDefinition('inputs', 'Browse extracted human-attributed inputs.', frozenset(()), True, _inputs),
    FeatureDefinition('export', 'Export normalized events and available source evidence.', frozenset(()), True, _export),
    FeatureDefinition('classification', 'Classify session purpose with a configured model.', frozenset(()), False, _classification),
    FeatureDefinition('replay', 'Replay an edited input through a configured model.', frozenset(('inputs',)), False, _replay),
    FeatureDefinition('native_resume', 'Prepare or execute a native Codex branch.', frozenset(('inputs',)), False, _native_resume),
 )
DEFAULT_FEATURES = frozenset(f.name for f in BUILTIN_FEATURES if f.default_enabled)


class Catalog:
    def __init__(self, definitions=BUILTIN_FEATURES):
        self.definitions = tuple(definitions)
        if len({f.name for f in self.definitions}) != len(self.definitions):
            raise ValueError("Duplicate feature declaration")

    def validate(self, enabled):
        enabled = frozenset(enabled)
        known = {f.name: f for f in self.definitions}
        if unknown := enabled - known.keys():
            raise ValueError(f"Unknown features: {', '.join(sorted(unknown))}")
        visiting, visited = set(), set()

        def visit(name):
            if name not in known:
                raise ValueError(f"Unknown feature dependency: {name}")
            if name in visiting:
                raise ValueError(f"Feature dependency cycle includes: {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in sorted(known[name].requires):
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            if name in enabled and (missing := known[name].requires - enabled):
                raise ValueError(f"Feature {name!r} requires: {', '.join(sorted(missing))}")

        for name in known:
            visit(name)
        return enabled

    def catalog(self, enabled):
        enabled = self.validate(enabled)
        return [{"name": f.name, "description": f.description, "requires": sorted(f.requires),
                 "enabled": f.name in enabled, "default_enabled": f.default_enabled} for f in self.definitions]

    def enabled_features(self, enabled):
        enabled = self.validate(enabled)
        known = {f.name: f for f in self.definitions}
        ordered, visited = [], set()

        def append(name):
            if name in visited:
                return
            for dependency in sorted(known[name].requires):
                append(dependency)
            visited.add(name)
            ordered.append(known[name])

        for feature in self.definitions:
            if feature.name in enabled:
                append(feature.name)
        return tuple(ordered)

    def adapters(self, enabled):
        from ..adapters.catalog import ADAPTERS

        self.validate(enabled)
        return tuple(a.name for a in ADAPTERS if a.requires <= enabled)


def load_catalog(settings):
    catalog = Catalog()
    catalog.validate(settings.features)
    from ..adapters.catalog import validate_adapters

    validate_adapters({f.name for f in catalog.definitions})
    return catalog
