"""Closed vocabularies shared across the domain (CONTRACT §3, §5)."""

from enum import StrEnum


class Lob(StrEnum):
    """Line of business."""

    CASUALTY = "casualty"
    PROPERTY = "property"


class BusinessType(StrEnum):
    DIRECT = "direct"
    REINSURANCE = "reinsurance"


class DocumentType(StrEnum):
    CONTRACT = "contract"
    SLIP = "slip"
    ENDORSEMENT = "endorsement"
    EMAIL = "email"
    CLAIMS_REPORT = "claims_report"
    OTHER = "other"
