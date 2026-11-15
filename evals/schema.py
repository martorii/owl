"""Golden-set case schema (CONTRACT §14.1). One ``GoldenCase`` per line of ``cases.jsonl``.

Values (``true_record``, ``current_values``, planted and expected values) use the JSON forms of
CONTRACT §6.1, so they can be fed to the domain parsers unchanged.
"""

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from owl.domain.suggestions import Action, Mode
from owl.domain.taxonomy import BusinessType, DocumentType, Lob


class Tag(StrEnum):
    CONFLICT = "conflict"
    RESTATED_OLD_VALUE = "restated_old_value"
    FORMAT_VARIANT = "format_variant"
    OCR_NOISE = "ocr_noise"
    LONG_BUNDLE = "long_bundle"
    NO_EVIDENCE = "no_evidence"
    DISTRACTOR = "distractor"


class CurrentKind(StrEnum):
    CORRECT = "correct"
    MISSING = "missing"
    WRONG = "wrong"
    CORRECT_FORMAT_VARIANT = "correct_format_variant"


class PlantedRole(StrEnum):
    """What a planted quote is, relative to the true record.

    ``true`` and ``conflicting`` are what an ideal extractor reports as candidates.
    ``superseded`` is an older value in an earlier-dated document, which the supersession
    guardrail (§7.3) must drop.
    ``restated_old`` is an old value restated inside the document that also gives the new one (an
    ideal extractor reports only the new value). ``distractor`` belongs to another policy, party or
    period.
    """

    TRUE = "true"
    CONFLICTING = "conflicting"
    SUPERSEDED = "superseded"
    RESTATED_OLD = "restated_old"
    DISTRACTOR = "distractor"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoldenDocument(_Model):
    document_id: UUID
    filename: str
    document_type: DocumentType
    document_date: date
    text: str


class Planted(_Model):
    field: str
    role: PlantedRole
    document_id: UUID
    quote: str = Field(min_length=1)  # literally present in the document text
    value: JsonValue  # the canonical value the quote states (§6.1 form)


class ExpectedSuggestion(_Model):
    field: str
    action: Action
    current_value: JsonValue
    proposed_value: JsonValue


class ExpectedConflict(_Model):
    field: str
    current_value: JsonValue
    values: list[JsonValue]  # the distinct values; evidence is not part of the expectation


class Expected(_Model):
    suggestions: list[ExpectedSuggestion]
    conflicts: list[ExpectedConflict]


class GoldenCase(_Model):
    case_id: str
    lob: Lob
    business_type: BusinessType
    mode: Mode
    tags: list[Tag]
    documents: list[GoldenDocument] = Field(
        min_length=1
    )  # oldest first, as DocumentBundle orders them
    true_record: dict[str, JsonValue]
    current_values: dict[str, JsonValue]  # empty in extraction mode
    current_kinds: dict[str, CurrentKind]  # per field of the true record; empty in extraction mode
    planted: list[Planted]
    expected: Expected
