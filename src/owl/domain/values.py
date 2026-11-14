"""Field value types: parsing, normalisation and equality (CONTRACT §4).

Every decision ("is this a change?", "do two candidates conflict?") is made on *normalised*
values produced here, never on raw text. Parsers accept raw strings, native JSON-ish values, and
values that are already normalised (so ``parse_value`` is idempotent).

Decisions documented in tests:
- Money accepts abbreviations (``1m``, ``2 Mrd``, ``500k``) and symbols/names; the output currency
  is always the ISO code.
- ``billion`` means 10^9 (English); German ``Billion`` (10^12) is deliberately not supported.
- Dates: see ``_dates`` (dotted = day-first; ambiguous slash dates follow
  ``ParseOptions.date_order``).
- Percentages need an explicit ``%``/``percent``; native ints/floats are rejected (ambiguous), a
  native ``Decimal`` is taken as the already-normalised fraction.
- Enums match case-insensitively, then by Jaro-Winkler similarity (threshold and margin
  configurable).
- Decimal precision is applied when parsing (half-up), so values equal within the configured
  precision are equal.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from typing import cast

from rapidfuzz.distance import JaroWinkler

from owl.domain._currencies import resolve_currency, resolve_currency_text
from owl.domain._dates import parse_date
from owl.domain._numbers import LoneSeparator, parse_number


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOL = "bool"
    DATE = "date"
    MONEY = "money"
    PERCENTAGE = "percentage"
    ENUM = "enum"
    LIST = "list"


class DateOrder(StrEnum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str  # ISO 4217 alphabetic code


@dataclass(frozen=True)
class ListValue:
    items: tuple["Scalar", ...]
    ordered: bool = False


Scalar = str | int | Decimal | bool | date | Money
Value = Scalar | ListValue


@dataclass(frozen=True)
class ValueSpec:
    """What the parser needs to know about a field's type."""

    type: FieldType
    allowed_values: tuple[str, ...] | None = None  # enum, or list with enum items
    item_type: FieldType | None = None  # list only
    ordered: bool = False  # list only

    def __post_init__(self) -> None:
        if self.type is FieldType.LIST:
            if self.item_type is None:
                raise ValueError("a list field needs an item_type")
            if self.item_type is FieldType.LIST:
                raise ValueError("nested lists are not supported")
        enum_needed = self.type is FieldType.ENUM or self.item_type is FieldType.ENUM
        if enum_needed and not self.allowed_values:
            raise ValueError("an enum field needs non-empty allowed_values")

    @property
    def item_spec(self) -> "ValueSpec":
        if self.item_type is None:
            raise ValueError("not a list spec")
        return ValueSpec(self.item_type, allowed_values=self.allowed_values)


@dataclass(frozen=True)
class ParseOptions:
    date_order: DateOrder = DateOrder.DAY_FIRST
    decimal_places: int = 6
    enum_similarity_threshold: float = 0.92
    enum_similarity_margin: float = 0.03


DEFAULT_OPTIONS = ParseOptions()


class ValueParseError(ValueError):
    def __init__(self, raw: object, field_type: FieldType, reason: str) -> None:
        super().__init__(f"cannot parse {raw!r} as {field_type.value}: {reason}")
        self.raw = raw
        self.field_type = field_type
        self.reason = reason


# --- helpers -----------------------------------------------------------------------------------


def _canonical(d: Decimal) -> Decimal:
    if not d.is_finite():
        raise ValueError("not a finite number")
    with localcontext() as ctx:
        ctx.prec = 60
        if d == d.to_integral_value():
            return Decimal(int(d))
        return d.normalize()


def _decimal_from_native(raw: object, lone: LoneSeparator) -> Decimal:
    if isinstance(raw, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(repr(raw))
    if isinstance(raw, str):
        return parse_number(raw, lone)
    raise ValueError("expected a number or a string")


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


# --- scalar parsers (raise ValueError; wrapped into ValueParseError by parse_value) --------------


def _string(raw: object) -> str:
    if not isinstance(raw, str):
        raise ValueError("expected a string")
    text = " ".join(unicodedata.normalize("NFC", raw).split())
    if not text:
        raise ValueError("empty string")
    return text


def _integer(raw: object) -> int:
    d = _decimal_from_native(raw, LoneSeparator.GROUP_IF_THREE_DIGITS)
    if not d.is_finite() or d != d.to_integral_value():
        raise ValueError("not a whole number")
    return int(d)


def _decimal(raw: object, options: ParseOptions) -> Decimal:
    d = _decimal_from_native(raw, LoneSeparator.DECIMAL)
    if not d.is_finite():
        raise ValueError("not a finite number")
    with localcontext() as ctx:
        ctx.prec = 60
        d = d.quantize(Decimal(1).scaleb(-options.decimal_places), rounding=ROUND_HALF_UP)
    return _canonical(d)


_TRUE = frozenset({"yes", "y", "true", "t", "1", "ja", "j", "oui", "si", "sí", "sì"})
_FALSE = frozenset({"no", "n", "false", "f", "0", "nein", "non"})


def _bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        key = _fold(raw)
        if key in _TRUE:
            return True
        if key in _FALSE:
            return False
    raise ValueError("expected yes/no or true/false")


def _date(raw: object, options: ParseOptions) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return parse_date(raw, day_first=options.date_order is DateOrder.DAY_FIRST)
    raise ValueError("expected a date or a string")


_MULTIPLIERS: dict[str, int] = {
    **dict.fromkeys(["k", "tsd", "thousand", "tausend"], 10**3),
    **dict.fromkeys(["m", "mm", "mn", "mln", "mio", "million", "millions", "millionen"], 10**6),
    **dict.fromkeys(["b", "bn", "bln", "mrd", "md", "milliard", "milliarde", "milliarden"], 10**9),
    **dict.fromkeys(["billion", "billions"], 10**9),
}
_NUMBER_SPAN = re.compile(
    r"\d(?:[\d.,'\N{RIGHT SINGLE QUOTATION MARK}\N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE} ]*\d)?"
)


def _money_from_text(text: str) -> Money:
    s = unicodedata.normalize("NFKC", text).strip()
    match = _NUMBER_SPAN.search(s)
    if match is None:
        raise ValueError("no amount found")
    before, after = s[: match.start()], s[match.end() :]
    if re.search(r"\d", before + after):
        raise ValueError("more than one number")
    amount = parse_number(match[0], LoneSeparator.GROUP_IF_THREE_DIGITS)
    multiplier = 1
    words = after.split()
    if words and (m := _MULTIPLIERS.get(words[0].rstrip(".").casefold())) is not None:
        multiplier = m
        words = words[1:]
    currency_text = f"{before} {' '.join(words)}"
    if not currency_text.strip(" .,"):
        raise ValueError("currency missing")
    currency = resolve_currency_text(currency_text)
    if currency is None:
        raise ValueError(f"unknown or ambiguous currency {currency_text.strip()!r}")
    with localcontext() as ctx:
        ctx.prec = 60
        return Money(_canonical(amount * multiplier), currency)


def _money(raw: object) -> Money:
    if isinstance(raw, Money):
        currency = resolve_currency(raw.currency)
        if currency is None:
            raise ValueError("unknown currency")
        return Money(_canonical(raw.amount), currency)
    if isinstance(raw, Mapping):
        fields = dict(cast("Mapping[str, object]", raw))
        if set(fields) != {"amount", "currency"}:
            raise ValueError("expected exactly 'amount' and 'currency'")
        currency_raw = fields["currency"]
        currency = resolve_currency(currency_raw) if isinstance(currency_raw, str) else None
        if currency is None:
            raise ValueError(f"unknown currency {currency_raw!r}")
        amount = _decimal_from_native(fields["amount"], LoneSeparator.GROUP_IF_THREE_DIGITS)
        return Money(_canonical(amount), currency)
    if isinstance(raw, str):
        return _money_from_text(raw)
    raise ValueError("expected text or {'amount', 'currency'}")


_PERCENT = re.compile(
    r"(?P<num>.+?)\s*(?:%|pct|percent|per\s*cent|per\s*cento|prozent|procent|pour\s*cent"
    r"|por\s*ciento|por\s*cento)\.?",
    re.IGNORECASE,
)


def _percentage(raw: object) -> Decimal:
    if isinstance(raw, Decimal):  # already-normalised fraction
        return _canonical(raw)
    if isinstance(raw, str):
        match = _PERCENT.fullmatch(unicodedata.normalize("NFKC", raw).strip())
        if match is None:
            raise ValueError("a percentage needs an explicit % sign or the word 'percent'")
        number = parse_number(match["num"], LoneSeparator.DECIMAL)
        with localcontext() as ctx:
            ctx.prec = 60
            return _canonical(number.scaleb(-2))
    raise ValueError("expected text such as '15%'")


def _enum(raw: object, allowed: tuple[str, ...], options: ParseOptions) -> str:
    if not isinstance(raw, str):
        raise ValueError("expected a string")
    key = _fold(raw)
    if not key:
        raise ValueError("empty value")
    scored = sorted(
        ((JaroWinkler.normalized_similarity(key, _fold(a)), a) for a in allowed),
        key=lambda pair: pair[0],
        reverse=True,
    )
    for _, candidate in scored:
        if _fold(candidate) == key:
            return candidate
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < options.enum_similarity_threshold:
        raise ValueError(f"not one of {list(allowed)}")
    if best_score - runner_up < options.enum_similarity_margin:
        raise ValueError(f"ambiguous between several of {list(allowed)}")
    return best


# --- lists -------------------------------------------------------------------------------------


def _split_list(text: str) -> list[str]:
    """Quote-aware split of ``[a; b]`` / ``a; b`` / ``a, b`` / newline-separated text."""
    s = text.strip()
    if s.startswith("[") != s.endswith("]"):
        raise ValueError("unbalanced brackets")
    if s.startswith("["):
        s = s[1:-1]
    # ';' and newlines win over ',' so that items may contain commas ("USD 1,000").
    outside = "".join(c for c, quoted in _scan_quotes(s) if not quoted)
    delimiters = ";\n" if (";" in outside or "\n" in outside) else ","
    parts: list[str] = []
    current: list[str] = []
    for char, quoted in _scan_quotes(s):
        if not quoted and char in delimiters:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    cleaned = [_unquote(p.strip()) for p in parts]
    return [p for p in cleaned if p]


def _scan_quotes(s: str) -> list[tuple[str, bool]]:
    result: list[tuple[str, bool]] = []
    inside = False
    for char in s:
        if char == '"':
            inside = not inside
            result.append((char, True))
        else:
            result.append((char, inside))
    return result


def _unquote(item: str) -> str:
    if len(item) >= 2 and item.startswith('"') and item.endswith('"'):
        return item[1:-1]
    return item


def _list(spec: ValueSpec, raw: object, options: ParseOptions) -> ListValue:
    if isinstance(raw, ListValue):
        raw_items: Sequence[object] = raw.items
    elif isinstance(raw, str):
        raw_items = _split_list(raw)
    elif isinstance(raw, (list, tuple)):
        raw_items = cast("Sequence[object]", raw)
    else:
        raise ValueError("expected a list or a bracketed/delimited string")
    item_spec = spec.item_spec
    parsed: list[Scalar] = []
    seen: set[object] = set()
    for item in raw_items:
        try:
            value = parse_value(item_spec, item, options)
        except ValueParseError as exc:
            raise ValueError(f"item {item!r}: {exc.reason}") from exc
        assert not isinstance(value, ListValue)
        if not spec.ordered:
            key = equality_key(item_spec, value)
            if key in seen:
                continue
            seen.add(key)
        parsed.append(value)
    if not parsed:
        raise ValueError("empty list")
    return ListValue(tuple(parsed), spec.ordered)


# --- public API --------------------------------------------------------------------------------


def parse_value(spec: ValueSpec, raw: object, options: ParseOptions = DEFAULT_OPTIONS) -> Value:
    """Parse and normalise ``raw`` for the given type. Raises ``ValueParseError``.

    Idempotent: parsing an already-normalised value returns an equal value.
    """
    try:
        return _parse(spec, raw, options)
    except ValueParseError:
        raise
    except (ValueError, ArithmeticError) as exc:
        raise ValueParseError(raw, spec.type, str(exc)) from exc


def _parse(spec: ValueSpec, raw: object, options: ParseOptions) -> Value:
    match spec.type:
        case FieldType.STRING:
            return _string(raw)
        case FieldType.INTEGER:
            return _integer(raw)
        case FieldType.DECIMAL:
            return _decimal(raw, options)
        case FieldType.BOOL:
            return _bool(raw)
        case FieldType.DATE:
            return _date(raw, options)
        case FieldType.MONEY:
            return _money(raw)
        case FieldType.PERCENTAGE:
            return _percentage(raw)
        case FieldType.ENUM:
            return _enum(raw, spec.allowed_values or (), options)
        case FieldType.LIST:
            return _list(spec, raw, options)


def equality_key(spec: ValueSpec, value: Value) -> object:
    """A hashable key such that two values are equal iff their keys are equal."""
    if isinstance(value, ListValue):
        item_spec = spec.item_spec
        keys = tuple(equality_key(item_spec, item) for item in value.items)
        return keys if spec.ordered else frozenset(keys)
    if spec.type is FieldType.STRING and isinstance(value, str):
        return value.casefold()
    return (type(value).__name__, value)


def values_equal(spec: ValueSpec, a: Value, b: Value) -> bool:
    """CONTRACT §4 equality on normalised values."""
    return equality_key(spec, a) == equality_key(spec, b)


def format_value(spec: ValueSpec, value: Value) -> str:
    """Canonical text for a normalised value; ``parse_value`` reads it back."""
    if isinstance(value, ListValue):
        item_spec = spec.item_spec
        return "[" + "; ".join(_format_item(item_spec, item) for item in value.items) + "]"
    return _format_scalar(spec, value)


def _format_scalar(spec: ValueSpec, value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Money):
        return f"{format(value.amount, 'f')} {value.currency}"
    if isinstance(value, Decimal):
        if spec.type is FieldType.PERCENTAGE:
            with localcontext() as ctx:
                ctx.prec = 60
                return f"{format(_canonical(value.scaleb(2)), 'f')}%"
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _format_item(item_spec: ValueSpec, item: Scalar) -> str:
    text = _format_scalar(item_spec, item)
    return f'"{text}"' if isinstance(item, str) else text
