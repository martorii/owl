from decimal import Decimal

import pytest

from owl.domain._numbers import LoneSeparator, parse_number

GROUP = LoneSeparator.GROUP_IF_THREE_DIGITS
DECIMAL = LoneSeparator.DECIMAL


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1000", "1000"),
        ("1,000,000", "1000000"),
        ("1.000.000", "1000000"),
        ("1,000,000.50", "1000000.50"),
        ("1.000.000,50", "1000000.50"),
        ("1 000 000", "1000000"),
        ("1\N{NO-BREAK SPACE}000\N{NO-BREAK SPACE}000", "1000000"),
        ("1'000'000", "1000000"),
        ("1\N{RIGHT SINGLE QUOTATION MARK}000", "1000"),
        ("1.000", "1000"),  # lone separator + exactly 3 digits: grouping
        ("12,345", "12345"),
        ("1,5", "1.5"),
        ("12.34", "12.34"),
        ("0.500", "0.5"),  # leading zero: never grouping
        ("-5", "-5"),
        ("+7.25", "7.25"),
    ],
)
def test_group_if_three_digits(text: str, expected: str) -> None:
    assert parse_number(text, GROUP) == Decimal(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.250", "1.25"),  # lone separator is always a decimal point
        ("1,234", "1.234"),
        ("1.234,5", "1234.5"),
        ("1,234.5", "1234.5"),
        ("1.000.000", "1000000"),  # repeated separator is still grouping
    ],
)
def test_lone_separator_is_decimal(text: str, expected: str) -> None:
    assert parse_number(text, DECIMAL) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "abc",
        "1.2.3",
        "1,2,3",
        "1e3",
        "1 2",
        ".5",
        "5.",
        "1,000.000,5",
        "--1",
        "1,,000",
        "٣",
    ],
)
def test_rejects(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_number(text, GROUP)
