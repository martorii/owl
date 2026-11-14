from datetime import date

import pytest

from owl.domain._dates import MONTHS, parse_date

D = date(2026, 3, 1)


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-01",
        "2026/03/01",
        "2026.03.01",
        "20260301",
        "2026-03-01T10:00:00Z",
        "2026-03-01 10:00",
        "01.03.2026",  # dotted is always day-first
        "1.3.2026",
        "01/03/2026",  # ambiguous slash: default day-first (documented rule)
        "01-03-2026",
        "1 March 2026",
        "1st March 2026",
        "March 1st, 2026",
        "Mar 1 2026",
        "01-Mar-2026",
        "1. März 2026",
        "1 Mrz 2026",
        "1 de marzo de 2026",
        "1er mars 2026",
        "1 maart 2026",
        "1 marzo 2026",
        "1 de março de 2026",
        "  1   MARCH   2026  ",
    ],
)
def test_parses_to_march_first(text: str) -> None:
    assert parse_date(text) == D


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15/03/2026", date(2026, 3, 15)),  # first part > 12 → day
        ("03/15/2026", date(2026, 3, 15)),  # second part > 12 → month-first
        ("15.03.2026", date(2026, 3, 15)),
        ("29.02.2028", date(2028, 2, 29)),
        ("5 Sept 2026", date(2026, 9, 5)),
        ("1 août 2026", date(2026, 8, 1)),
        ("3. Dezember 2026", date(2026, 12, 3)),
        ("1 Jänner 2026", date(2026, 1, 1)),
    ],
)
def test_other_dates(text: str, expected: date) -> None:
    assert parse_date(text) == expected


def test_ambiguous_slash_follows_configured_order() -> None:
    assert parse_date("01/03/2026", day_first=True) == date(2026, 3, 1)
    assert parse_date("01/03/2026", day_first=False) == date(2026, 1, 3)
    # dotted dates stay day-first regardless of the option
    assert parse_date("01.03.2026", day_first=False) == date(2026, 3, 1)
    # unambiguous slash dates ignore the option
    assert parse_date("03/15/2026", day_first=True) == date(2026, 3, 15)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "tomorrow",
        "31/02/2026",
        "29.02.2026",
        "32.01.2026",
        "2026-13-01",
        "13/13/2026",
        "01/03/26",  # two-digit years are not guessed
        "March 2026",
        "March",
        "1 2 2026",
        "5 to 7 March 2026",
        "1 Foo 2026",
        "01.03.1800",
        "1 March 3000",
    ],
)
def test_rejects(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_date(text)


def test_month_table_is_consistent() -> None:
    assert set(MONTHS.values()) == set(range(1, 13))
