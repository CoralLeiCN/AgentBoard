"""Shared purpose taxonomy and validation for models and external agents."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import Timestamp
from .prompts import render_prompt

TAXONOMY_VERSION = "session-purpose-v1"
REPORT_URL = (
    "https://cdn.openai.com/pdf/7ef17d82-96bf-4dd1-9df2-228f7f377a29/"
    "the-state-of-enterprise-ai_2025-report.pdf"
)


class PurposeCategory(StrEnum):
    WRITING = "writing"
    CODING = "coding"
    DEBUGGING = "bug-fixing"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    CREATIVE_MEDIA = "creative-media"
    GUIDANCE = "guidance"
    OTHER = "other"


# Metadata references enum members so category spelling has one source of truth.
PURPOSES = (
    (PurposeCategory.WRITING, "Writing & communication", "Drafting, editing, summarizing, or translating text."),
    (PurposeCategory.CODING, "Coding", "Implementing, refactoring, testing, or reviewing software."),
    (PurposeCategory.DEBUGGING, "Debugging", "Diagnosing or fixing a specific error, failure, or regression."),
    (PurposeCategory.RESEARCH, "Research & information gathering", "Finding, comparing, or synthesizing information."),
    (PurposeCategory.ANALYSIS, "Analysis & calculations", "Analyzing data, calculating, or interpreting quantitative results."),
    (PurposeCategory.CREATIVE_MEDIA, "Creative media", "Creating or editing images, designs, audio, or video."),
    (PurposeCategory.GUIDANCE, "How-to & procedural guidance", "Explaining how to do something, teaching, or giving advice."),
    (PurposeCategory.OTHER, "Other / unclear", "A purpose outside these categories or insufficient evidence to choose one."),
)
CATEGORIES = tuple(category.value for category in PurposeCategory)
CLASSIFICATION_PROMPT = render_prompt(
    "session_purpose", categories="\n".join(f"{key}: {definition}" for key, _, definition in PURPOSES),
)


class ClassificationLabel(BaseModel):
    """The model's complete output contract, independent of provider metadata."""

    model_config = ConfigDict(extra="forbid")

    category: PurposeCategory
    # Anchored whole-string form also works with decoders that treat pattern as a full match.
    reason: str = Field(min_length=1, max_length=2000, pattern=r"^[\s\S]*\S[\s\S]*$")

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value):
        if not value.strip():
            raise ValueError("A classification reason is required")
        return value.strip()


Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ClassificationRequest(ClassificationLabel):
    """External-agent submission; the legacy alias is declared only at this boundary."""

    category: PurposeCategory | Literal["debugging"]
    model: str = Field(min_length=1, max_length=200)
    input_sha256: Sha256 | None = None

    @field_validator("category")
    @classmethod
    def normalize_alias(cls, value):
        return PurposeCategory.DEBUGGING if value == "debugging" else value


class ClassificationResult(ClassificationLabel):
    """Validated stored/API result. Provenance is supplied by AgentBoard, not the LLM."""

    model: str = Field(min_length=1, max_length=200)
    provider: str
    dummy: bool
    content_origin: Literal["model_generated", "inferred"]
    taxonomy_version: str
    classified_at: Timestamp
    api: Literal["responses", "chat_completions"] | None = None
    fallback_reason: str | None = None
    provenance: Literal["externally_asserted"] | None = None
    prompt_sha256: Sha256 | None = None
    input_sha256: Sha256 | None = None
    output_schema_sha256: Sha256 | None = None
    input_selection: str | None = None
    input_source: str | None = None
    input_event_ids: list[str] | None = None
    input_chars: int | None = Field(default=None, ge=0)
    truncated: bool | None = None


def classification_schema():
    return ClassificationLabel.model_json_schema()


def classification_format():
    """Responses text.format; Chat Completions uses the same inner schema."""
    return {"type": "json_schema", "name": "session_purpose", "strict": True, "schema": classification_schema()}


def taxonomy():
    return {
        "version": TAXONOMY_VERSION,
        "source_url": REPORT_URL,
        "source_page": 14,
        "note": "Adapted from the report's task types, with debugging and other added for sessions.",
        "categories": [{"id": key, "label": label, "description": desc} for key, label, desc in PURPOSES],
        "aliases": {"debugging": "bug-fixing"},
    }
