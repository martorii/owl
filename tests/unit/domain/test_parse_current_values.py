from datetime import date
from decimal import Decimal

import pytest

from owl.domain.catalog import Applicability, Catalog, FieldDefinition
from owl.domain.suggestions import CurrentValuesError, WarningCode, parse_current_values
from owl.domain.taxonomy import BusinessType, Lob
from owl.domain.values import FieldType, Money

ALL = Applicability(frozenset(Lob), frozenset(BusinessType))
PROPERTY_ONLY = Applicability(frozenset({Lob.PROPERTY}), frozenset(BusinessType))


def defn(name: str, type: FieldType, applies_to: Applicability = ALL) -> FieldDefinition:
    return FieldDefinition(name, type, "A field.", applies_to, "g")


CATALOG = Catalog(
    1,
    (
        defn("inception", FieldType.DATE),
        defn("limit", FieldType.MONEY),
        defn("name", FieldType.STRING),
        defn("tiv", FieldType.DECIMAL, PROPERTY_ONLY),
    ),
)
ACTIVE = CATALOG.active_fields(Lob.CASUALTY, BusinessType.DIRECT)


def test_parses_to_normalised_values() -> None:
    parsed = parse_current_values(
        CATALOG, ACTIVE, {"inception": "01.03.2026", "limit": "USD 5m", "name": "Acme"}
    )
    assert parsed.values == {
        "inception": date(2026, 3, 1),
        "limit": Money(Decimal("5000000"), "USD"),
        "name": "Acme",
    }
    assert parsed.warnings == ()


def test_empty_is_fine() -> None:
    parsed = parse_current_values(CATALOG, ACTIVE, {})
    assert parsed.values == {}
    assert parsed.warnings == ()


def test_null_means_no_current_value() -> None:
    parsed = parse_current_values(CATALOG, ACTIVE, {"inception": None, "name": "Acme"})
    assert parsed.values == {"name": "Acme"}
    assert parsed.warnings == ()


def test_inactive_field_is_ignored_with_warning() -> None:
    parsed = parse_current_values(CATALOG, ACTIVE, {"tiv": "100", "name": "Acme"})
    assert parsed.values == {"name": "Acme"}
    assert [(w.code, w.field) for w in parsed.warnings] == [
        (WarningCode.FIELD_INACTIVE_IGNORED, "tiv")
    ]


def test_inactive_field_with_bad_value_is_still_just_a_warning() -> None:
    parsed = parse_current_values(CATALOG, ACTIVE, {"tiv": "not a number"})
    assert parsed.values == {}
    assert len(parsed.warnings) == 1


def test_unknown_field_is_an_error() -> None:
    with pytest.raises(CurrentValuesError, match="nope"):
        parse_current_values(CATALOG, ACTIVE, {"nope": "x"})


def test_unparseable_value_is_an_error() -> None:
    with pytest.raises(CurrentValuesError, match="inception"):
        parse_current_values(CATALOG, ACTIVE, {"inception": "someday"})


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(CurrentValuesError) as info:
        parse_current_values(CATALOG, ACTIVE, {"nope": "x", "inception": "someday", "limit": "?"})
    assert len(info.value.problems) == 3
