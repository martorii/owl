from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from owl.domain.serialisation import serialise_value
from owl.domain.values import (
    FieldType,
    ListValue,
    Money,
    Scalar,
    Value,
    ValueSpec,
    equality_key,
    parse_value,
)

ENUM = ValueSpec(FieldType.ENUM, allowed_values=("Property", "Casualty"))


@pytest.mark.parametrize(
    ("spec", "value", "expected"),
    [
        (ValueSpec(FieldType.STRING), "Acme Re", "Acme Re"),
        (ENUM, "Property", "Property"),
        (ValueSpec(FieldType.INTEGER), 42, 42),
        (ValueSpec(FieldType.DECIMAL), Decimal("1234.5"), "1234.5"),
        (ValueSpec(FieldType.DECIMAL), Decimal("1E+3"), "1000"),
        (ValueSpec(FieldType.BOOL), True, True),
        (ValueSpec(FieldType.BOOL), False, False),
        (ValueSpec(FieldType.DATE), date(2026, 3, 1), "2026-03-01"),
        (
            ValueSpec(FieldType.MONEY),
            Money(Decimal("5000000"), "USD"),
            {"amount": "5000000", "currency": "USD"},
        ),
        (ValueSpec(FieldType.PERCENTAGE), Decimal("0.15"), "15%"),
        (
            ValueSpec(FieldType.LIST, item_type=FieldType.DATE),
            ListValue((date(2026, 1, 1), date(2026, 2, 1))),
            ["2026-01-01", "2026-02-01"],
        ),
        (
            ValueSpec(FieldType.LIST, item_type=FieldType.MONEY, ordered=True),
            ListValue((Money(Decimal("1.5"), "EUR"),), ordered=True),
            [{"amount": "1.5", "currency": "EUR"}],
        ),
    ],
)
def test_json_forms(spec: ValueSpec, value: Value, expected: object) -> None:
    assert serialise_value(spec, value) == expected


def test_bool_is_not_serialised_as_number_and_integer_is_not_a_string() -> None:
    assert serialise_value(ValueSpec(FieldType.BOOL), True) is True
    assert type(serialise_value(ValueSpec(FieldType.INTEGER), 7)) is int


# --- round trip ------------------------------------------------------------------------------

_decimals = st.decimals(min_value=-(10**9), max_value=10**9, places=4, allow_nan=False)
_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=0x24F),
    min_size=1,
    max_size=12,
)

_SCALARS: dict[FieldType, st.SearchStrategy[Scalar]] = {
    FieldType.STRING: _text,
    FieldType.INTEGER: st.integers(-(10**12), 10**12),
    FieldType.DECIMAL: _decimals,
    FieldType.BOOL: st.booleans(),
    FieldType.DATE: st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 31)),
    FieldType.MONEY: st.builds(
        Money, st.decimals(min_value=0, max_value=10**12, places=2), st.sampled_from(["USD", "EUR"])
    ),
    FieldType.PERCENTAGE: st.decimals(min_value=0, max_value=10, places=4).map(lambda d: d / 100),
    FieldType.ENUM: st.sampled_from(["Property", "Casualty"]),
}


def _assert_round_trip(spec: ValueSpec, raw: object) -> None:
    value = parse_value(spec, raw)
    again = parse_value(spec, serialise_value(spec, value))
    assert equality_key(spec, again) == equality_key(spec, value)


@pytest.mark.parametrize("field_type", list(_SCALARS))
@given(data=st.data())
def test_scalar_round_trip(field_type: FieldType, data: st.DataObject) -> None:
    spec = ENUM if field_type is FieldType.ENUM else ValueSpec(field_type)
    raw = data.draw(_SCALARS[field_type])
    if field_type is FieldType.PERCENTAGE:
        assert isinstance(raw, Decimal)
        raw = f"{raw * 100:f}%"
    elif field_type is FieldType.STRING:
        assert isinstance(raw, str)
        raw = raw.strip() or "x"
    _assert_round_trip(spec, raw)


@pytest.mark.parametrize(
    "item_type",
    [t for t in _SCALARS if t is not FieldType.PERCENTAGE and t is not FieldType.STRING],
)
@pytest.mark.parametrize("ordered", [True, False])
@given(data=st.data())
def test_list_round_trip(item_type: FieldType, ordered: bool, data: st.DataObject) -> None:
    allowed = ("Property", "Casualty") if item_type is FieldType.ENUM else None
    spec = ValueSpec(FieldType.LIST, item_type=item_type, ordered=ordered, allowed_values=allowed)
    items = data.draw(st.lists(_SCALARS[item_type], min_size=1, max_size=4))
    _assert_round_trip(spec, items)
