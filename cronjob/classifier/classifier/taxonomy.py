"""Pinned experiment label contract, independent of the AgentBoard runtime.

These IDs match the session-purpose-v1 rubric used by the saved turn experiments.
Changing the rubric requires an explicit taxonomy version and dataset revision.
"""

TAXONOMY_VERSION = "session-purpose-v1"
CATEGORIES = (
    "writing",
    "coding",
    "bug-fixing",
    "research",
    "analysis",
    "creative-media",
    "guidance",
    "other",
)
