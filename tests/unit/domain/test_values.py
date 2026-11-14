from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from owl.domain.values import (
    DateOrder,
    FieldType,
    ListValue,
    Money,
    ParseOptions,
    Value,
    ValueParseError,
    ValueSpec,
    format_value,
    parse_value,
    values_equal,
)

S = ValueSpec(FieldType.STRING)
I = ValueSpec(FieldType.INTEGER)  # noqa: E741
DEC = ValueSpec(FieldType.DECIMAL)
B = ValueSpec(FieldType.BOOL)
DT = ValueSpec(FieldType.DATE)
M = ValueSpec(FieldType.MONEY)
P = ValueSpec(FieldType.PERCENTAGE)
LOB = ValueSpec(FieldType.ENUM, allowed_values=("Property", "Casualty", "Marine"))


def money(amount: str, currency: str) -> Money:
    return Money(Decimal(amount), currency)


def ok(spec: ValueSpec, raw: object) -> Value:
    return parse_value(spec, raw)


def bad(spec: ValueSpec, raw: object) -> None:
    with pytest.raises(ValueParseError):
        parse_value(spec, raw)


# --- spec validation ---------------------------------------------------------------------------


def test_spec_validation() -> None:
    with pytest.raises(ValueError, match="allowed_values"):
        ValueSpec(FieldType.ENUM)
    with pytest.raises(ValueError, match="item_type"):
        ValueSpec(FieldType.LIST)
    with pytest.raises(ValueError, match="nested"):
        ValueSpec(FieldType.LIST, item_type=FieldType.LIST)
    with pytest.raises(ValueError, match="allowed_values"):
        ValueSpec(FieldType.LIST, item_type=FieldType.ENUM)


def test_error_carries_context() -> None:
    with pytest.raises(ValueParseError) as exc:
        parse_value(I, "abc")
    assert exc.value.raw == "abc"
    assert exc.value.field_type is FieldType.INTEGER
    assert "abc" in str(exc.value)


# --- string / integer / decimal / bool ---------------------------------------------------------


def test_string() -> None:
    assert ok(S, "  Hello \n  World ") == "Hello World"
    bad(S, "   ")
    bad(S, 5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,000", 1000),
        ("1 000", 1000),
        ("1.000", 1000),
        ("-5", -5),
        ("+7", 7),
        ("1.000,00", 1000),
        (5, 5),
        (5.0, 5),
        (Decimal(7), 7),
    ],
)
def test_integer(raw: object, expected: int) -> None:
    assert ok(I, raw) == expected


@pytest.mark.parametrize("raw", ["1.5", "1,5", "1e3", "abc", "", True, 5.5, None, "1 2"])
def test_integer_rejects(raw: object) -> None:
    bad(I, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.5", "1.5"),
        ("1,5", "1.5"),
        ("1.234,5678", "1234.5678"),
        ("1.250", "1.25"),
        ("0.1234567", "0.123457"),  # rounded to 6 places, half up
        ("2", "2"),
        (0.1, "0.1"),
        (3, "3"),
    ],
)
def test_decimal(raw: object, expected: str) -> None:
    assert ok(DEC, raw) == Decimal(expected)


def test_decimal_equality_within_precision() -> None:
    a, b = ok(DEC, "0.1234561"), ok(DEC, "0.1234564")
    assert values_equal(DEC, a, b)
    assert not values_equal(DEC, ok(DEC, "0.1234561"), ok(DEC, "0.1234569"))
    coarse = ParseOptions(decimal_places=2)
    assert parse_value(DEC, "1.004", coarse) == parse_value(DEC, "1.001", coarse)


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "1e5", float("nan"), float("inf"), True])
def test_decimal_rejects(raw: object) -> None:
    bad(DEC, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("yes", True),
        ("Y", True),
        ("TRUE", True),
        ("Ja", True),
        ("oui", True),
        ("1", True),
        (True, True),
        ("no", False),
        ("N", False),
        ("false", False),
        ("Nein", False),
        ("non", False),
        ("0", False),
        (False, False),
        (0, False),
        (1, True),
    ],
)
def test_bool(raw: object, expected: bool) -> None:
    assert ok(B, raw) is expected


@pytest.mark.parametrize("raw", ["maybe", "", 2, "yes no", None])
def test_bool_rejects(raw: object) -> None:
    bad(B, raw)


# --- date --------------------------------------------------------------------------------------


def test_date() -> None:
    assert ok(DT, "1. März 2026") == date(2026, 3, 1)
    assert ok(DT, date(2026, 3, 1)) == date(2026, 3, 1)
    bad(DT, "nope")
    bad(DT, 20260301)


def test_date_order_option() -> None:
    assert parse_value(DT, "01/03/2026") == date(2026, 3, 1)
    month_first = ParseOptions(date_order=DateOrder.MONTH_FIRST)
    assert parse_value(DT, "01/03/2026", month_first) == date(2026, 1, 3)


# --- money -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("1,000,000 USD", "1000000", "USD"),
        ("USD 1,000,000", "1000000", "USD"),
        ("USD 1m", "1000000", "USD"),
        ("usd 1M", "1000000", "USD"),
        ("1m USD", "1000000", "USD"),
        ("5mm USD", "5000000", "USD"),
        ("$1.5m", "1500000", "USD"),
        ("$ 5 million", "5000000", "USD"),
        ("US$ 5 million", "5000000", "USD"),
        ("US dollars 5m", "5000000", "USD"),
        ("$5m USD", "5000000", "USD"),
        ("€ 1.500.000,00", "1500000", "EUR"),
        ("1,5 Mio. EUR", "1500000", "EUR"),
        ("2 Mrd EUR", "2000000000", "EUR"),
        ("2 Mrd. €", "2000000000", "EUR"),
        ("500 tsd EUR", "500000", "EUR"),
        ("5 euros", "5", "EUR"),
        ("£2bn", "2000000000", "GBP"),
        ("2.5 billion GBP", "2500000000", "GBP"),
        ("CHF 500k", "500000", "CHF"),
        ("1 000 000 CHF", "1000000", "CHF"),
        ("CHF 1'000'000", "1000000", "CHF"),
        ("A$100k", "100000", "AUD"),
        ("C$ 2m", "2000000", "CAD"),
        ("¥5m", "5000000", "JPY"),
        ("JPY 500,000,000", "500000000", "JPY"),
        ("SEK 12,5 Mio", "12500000", "SEK"),
        ("USD1,000.50", "1000.5", "USD"),
        ("1000000.50 EUR", "1000000.5", "EUR"),
        ("1.000 USD", "1000", "USD"),
        ("0.5m ZAR", "500000", "ZAR"),
        ("Pfund 3k", "3000", "GBP"),
        ("1,000,000 USD.", "1000000", "USD"),
    ],
)
def test_money(raw: str, amount: str, currency: str) -> None:
    assert ok(M, raw) == money(amount, currency)


@pytest.mark.parametrize(
    "raw",
    [
        "1,000,000",  # currency missing
        "USD",
        "",
        "XYZ 5m",  # unknown code
        "5-7m USD",
        "5 USD 7 EUR",
        "1m 2k USD",
        "USD EUR 5",  # conflicting currencies
        "-5 USD",
        "kr 5",  # ambiguous symbol, not guessed
        "five million USD",
        "5 foo USD",
        7,
        None,
    ],
)
def test_money_rejects(raw: object) -> None:
    bad(M, raw)


def test_money_native() -> None:
    assert ok(M, {"amount": "5000000", "currency": "usd"}) == money("5000000", "USD")
    assert ok(M, {"amount": 5, "currency": "EUR"}) == money("5", "EUR")
    assert ok(M, {"amount": 1.5, "currency": "€"}) == money("1.5", "EUR")
    assert ok(M, money("5.00", "USD")) == money("5", "USD")
    bad(M, {"amount": "5"})
    bad(M, {"amount": "x", "currency": "USD"})
    bad(M, {"amount": "5", "currency": "XYZ"})
    bad(M, {"amount": "5", "currency": "USD", "extra": 1})


def test_money_equality() -> None:
    assert values_equal(M, ok(M, "USD 1m"), ok(M, "1,000,000.00 USD"))
    assert not values_equal(M, ok(M, "USD 1m"), ok(M, "EUR 1m"))
    assert not values_equal(M, ok(M, "USD 1m"), ok(M, "USD 2m"))


# --- percentage --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15%", "0.15"),
        ("15 %", "0.15"),
        ("15,5 %", "0.155"),
        ("15 percent", "0.15"),
        ("15 per cent", "0.15"),
        ("15 Prozent", "0.15"),
        ("15 pour cent", "0.15"),
        ("0.5%", "0.005"),
        ("-2%", "-0.02"),
        ("100 %", "1"),
        (Decimal("0.15"), "0.15"),
    ],
)
def test_percentage(raw: object, expected: str) -> None:
    assert ok(P, raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["15", "0.15", "%", "abc%", "15%%", "", 15, 0.15, True])
def test_percentage_rejects(raw: object) -> None:
    bad(P, raw)


# --- enum --------------------------------------------------------------------------------------


def test_enum_exact_and_case() -> None:
    assert ok(LOB, "property") == "Property"
    assert ok(LOB, "  CASUALTY ") == "Casualty"


def test_enum_fuzzy_jaro_winkler() -> None:
    assert ok(LOB, "Propertty") == "Property"
    assert ok(LOB, "Casualy") == "Casualty"


def test_enum_threshold_is_configurable() -> None:
    exact_only = ParseOptions(enum_similarity_threshold=1.0)
    with pytest.raises(ValueParseError):
        parse_value(LOB, "Propertty", exact_only)
    assert parse_value(LOB, "property", exact_only) == "Property"


def test_enum_rejects_unrelated_and_ambiguous() -> None:
    bad(LOB, "Aviation")
    bad(LOB, "")
    bad(LOB, 5)
    ambiguous = ValueSpec(FieldType.ENUM, allowed_values=("Property A", "Property B"))
    bad(ambiguous, "Property C")


def test_enum_equality_is_exact_on_canonical_value() -> None:
    assert values_equal(LOB, ok(LOB, "property"), ok(LOB, "PROPERTY"))
    assert not values_equal(LOB, ok(LOB, "property"), ok(LOB, "marine"))


# --- list --------------------------------------------------------------------------------------

LS = ValueSpec(FieldType.LIST, item_type=FieldType.STRING)
LS_ORD = ValueSpec(FieldType.LIST, item_type=FieldType.STRING, ordered=True)
LM = ValueSpec(FieldType.LIST, item_type=FieldType.MONEY)
LD = ValueSpec(FieldType.LIST, item_type=FieldType.DATE)
LE = ValueSpec(FieldType.LIST, item_type=FieldType.ENUM, allowed_values=("Property", "Casualty"))


def items(spec: ValueSpec, raw: object) -> tuple[object, ...]:
    value = ok(spec, raw)
    assert isinstance(value, ListValue)
    return value.items


@pytest.mark.parametrize(
    "raw",
    [
        ["a", "b", "c"],
        ("a", "b", "c"),
        "[a, b, c]",
        "[a; b; c]",
        "a; b; c",
        "a, b, c",
        "a\nb\nc",
        '["a", "b", "c"]',
        "[ a ,b,  c ]",
    ],
)
def test_list_forms(raw: object) -> None:
    assert items(LS, raw) == ("a", "b", "c")


def test_list_commas_inside_items() -> None:
    assert items(LS, '["a,b", c]') == ("a,b", "c")
    assert items(LS, "[a,b; c]") == ("a,b", "c")  # ';' wins as delimiter
    assert items(LM, "[USD 1,000; EUR 2m]") == (money("1000", "USD"), money("2000000", "EUR"))


def test_list_item_types() -> None:
    assert items(LD, "01.03.2026; 2 March 2026") == (date(2026, 3, 1), date(2026, 3, 2))
    assert items(LE, "property, casualy") == ("Property", "Casualty")


def test_list_set_semantics() -> None:
    assert values_equal(LS, ok(LS, "a; b"), ok(LS, "B; a"))
    assert items(LS, "a; A; b") == ("a", "b")  # duplicates collapse in a set
    assert items(LD, ["2026-03-01", "01.03.2026"]) == (date(2026, 3, 1),)
    assert not values_equal(LS, ok(LS, "a; b"), ok(LS, "a; b; c"))


def test_list_ordered_semantics() -> None:
    assert not values_equal(LS_ORD, ok(LS_ORD, "a; b"), ok(LS_ORD, "b; a"))
    assert values_equal(LS_ORD, ok(LS_ORD, "a; b"), ok(LS_ORD, "A; B"))
    assert items(LS_ORD, "a; a; b") == ("a", "a", "b")  # duplicates kept


@pytest.mark.parametrize("raw", ["", "[]", " ; ", "[a", "a]", 5, None, ["a", 5], [""], "[a; 5x]"])
def test_list_rejects(raw: object) -> None:
    bad(LS if raw != "[a; 5x]" else LM, raw)


def test_list_bad_item_names_item() -> None:
    with pytest.raises(ValueParseError, match="Aviation"):
        parse_value(LE, "Property; Aviation")


# --- format round trip & properties ------------------------------------------------------------

ALL_SPECS = [S, I, DEC, B, DT, M, P, LOB, LS, LS_ORD, LM, LD, LE]
WORDS = st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,8}", fullmatch=True)
CURRENCIES = st.sampled_from(["USD", "EUR", "GBP", "CHF", "JPY", "SEK", "ZAR", "AUD"])
DECIMALS = st.decimals(min_value=-(10**6), max_value=10**6, places=6)
STRATEGIES: dict[int, st.SearchStrategy[Value]] = {
    0: WORDS,
    1: st.integers(-(10**12), 10**12),
    2: DECIMALS,
    3: st.booleans(),
    4: st.dates(date(1900, 1, 1), date(2199, 12, 31)),
    5: st.builds(
        Money,
        st.decimals(min_value=0, max_value=10**12, places=2),
        CURRENCIES,
    ),
    6: st.decimals(min_value=-5, max_value=5, places=4),
    7: st.sampled_from(["Property", "Casualty", "Marine"]),
}


@pytest.mark.parametrize("index", range(8))
@given(data=st.data())
def test_format_round_trip(index: int, data: st.DataObject) -> None:
    spec = ALL_SPECS[index]
    parsed = parse_value(spec, data.draw(STRATEGIES[index]))
    assert parse_value(spec, format_value(spec, parsed)) == parsed


@given(st.lists(WORDS, min_size=1, max_size=5))
def test_list_format_round_trip(words: list[str]) -> None:
    parsed = parse_value(LS_ORD, words)
    assert parse_value(LS_ORD, format_value(LS_ORD, parsed)) == parsed
    parsed_set = parse_value(LS, words)
    assert values_equal(LS, parse_value(LS, format_value(LS, parsed_set)), parsed_set)


NUMBERISH = st.text(alphabet="0123456789.,% -+'USDEURmkbnMio$€£ ", max_size=14)
FREE = st.one_of(NUMBERISH, st.text(max_size=20))


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: f"{s.type}-{s.item_type}")
@given(raw=FREE)
def test_parse_is_total_and_idempotent(spec: ValueSpec, raw: str) -> None:
    try:
        value = parse_value(spec, raw)
    except ValueParseError:
        return
    assert parse_value(spec, value) == value  # normalise is idempotent
    assert values_equal(spec, value, value)


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: f"{s.type}-{s.item_type}")
@given(a=FREE, b=FREE)
def test_equality_is_symmetric(spec: ValueSpec, a: str, b: str) -> None:
    try:
        va, vb = parse_value(spec, a), parse_value(spec, b)
    except ValueParseError:
        return
    assert values_equal(spec, va, vb) == values_equal(spec, vb, va)
