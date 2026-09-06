"""Explicitly registered agent adapters; no per-request dynamic code loading."""

from .codex import CodexAdapter


def adapters():
    return {"codex": CodexAdapter()}
