import pytest
from src.tools.knowledge_base.claim_fields import (
    CLAIM_FIELDS,
    FIELD_REGISTRY,
    ClaimIdField,
    DateOfLossField,
    PropertyTypeField,
)
from src.tools.knowledge_base.field_types import StringType, DateType, EnumType

def test_claim_fields_registry():
    assert len(CLAIM_FIELDS) > 0
    assert "general.claim_identification.claim_id" in FIELD_REGISTRY
    assert isinstance(FIELD_REGISTRY["general.claim_identification.claim_id"], ClaimIdField)

def test_field_delegation():
    field = ClaimIdField()
    assert isinstance(field.field_type, StringType)
    assert field.normalize("  CLM123  ") == "CLM123"
    assert field.compare("CLM123", "clm123") is True

def test_date_field_delegation():
    field = DateOfLossField()
    assert isinstance(field.field_type, DateType)
    assert field.normalize("28.03.2026") == "2026-03-28"

def test_enum_field_delegation():
    field = PropertyTypeField()
    assert isinstance(field.field_type, EnumType)
    assert field.normalize("res") == "residential"
    assert field.normalize("COMMERCIAL") == "commercial"

def test_field_repr():
    field = ClaimIdField()
    assert repr(field) == "<Field:general.claim_identification.claim_id>"

def test_all_fields_have_required_attributes():
    for field in CLAIM_FIELDS:
        assert hasattr(field, "field_name")
        assert hasattr(field, "description")
        assert hasattr(field, "example_value")
        assert hasattr(field, "field_type")
        assert field.field_name in FIELD_REGISTRY
