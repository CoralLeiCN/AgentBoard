"""Static parser ownership and lazy construction."""

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AdapterDefinition:
    name: str
    requires: frozenset[str]
    factory: Callable

    def __post_init__(self):
        object.__setattr__(self, "requires", frozenset(self.requires))


def codex(field_lineage):
    from .codex import CodexAdapter

    return CodexAdapter(field_lineage=field_lineage)


ADAPTERS = (AdapterDefinition("codex", frozenset({"import"}), codex),)


def validate_adapters(features, definitions=ADAPTERS):
    names = set()
    for adapter in definitions:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", adapter.name) or adapter.name in names:
            raise ValueError("Invalid or duplicate adapter")
        if not adapter.requires or not adapter.requires <= features or not callable(adapter.factory):
            raise ValueError("Invalid adapter ownership or factory")
        names.add(adapter.name)


def build_adapters(features):
    return {a.name: a.factory("field_lineage" in features) for a in ADAPTERS if a.requires <= features}
