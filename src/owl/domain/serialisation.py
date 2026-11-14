"""Value serialisation to the JSON forms of CONTRACT §6.1.

Every serialised value parses back (``parse_value``) to the same normalised value, so a response
value can be sent back unchanged in ``current_values``.
"""

from datetime import date
from decimal import Decimal

from owl.domain.values import (
    FieldType,
    ListValue,
    Money,
    Scalar,
    Value,
    ValueSpec,
    format_value,
)

type JsonValue = str | int | bool | dict[str, str] | list[JsonValue]


def serialise_value(spec: ValueSpec, value: Value) -> JsonValue:
    if isinstance(value, ListValue):
        item_spec = spec.item_spec
        return [_scalar(item_spec, item) for item in value.items]
    return _scalar(spec, value)


def _scalar(spec: ValueSpec, value: Scalar) -> JsonValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, Money):
        return {"amount": format(value.amount, "f"), "currency": value.currency}
    if isinstance(value, Decimal):
        # decimals are strings (never floats); percentages carry their percent sign
        return (
            format_value(spec, value) if spec.type is FieldType.PERCENTAGE else format(value, "f")
        )
    if isinstance(value, date):
        return value.isoformat()
    return value
