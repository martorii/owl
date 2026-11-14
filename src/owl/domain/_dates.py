"""Date parsing for international documents (internal to the domain).

Rules (documented and tested):
- ISO ``YYYY-MM-DD`` (also ``/`` ``.`` separators, compact ``YYYYMMDD``, optional time part).
- Dotted ``D.M.YYYY`` is always day-first (German and most of continental Europe).
- ``D/M/YYYY`` and ``D-M-YYYY``: if one part is > 12 it is the day; if both are <= 12 the order is
  configurable (default day-first).
- Spelled-out months in en, de, fr, es, it, nl, pt, in any token order (``1. März 2026``,
  ``March 1st, 2026``, ``1 de marzo de 2026``).
- Four-digit years only (1900-2199); two-digit years are never guessed.
"""

import re
import unicodedata
from datetime import date

_MIN_YEAR, _MAX_YEAR = 1900, 2199

_MONTH_NAMES: dict[int, str] = {
    1: "january jan januar janner jan janvier janv enero ene gennaio gen januari janeiro",
    2: "february feb februar feb fevrier fevr febrero febbraio febbraio februari fevereiro fev",
    3: "march mar marz maerz mrz mars marzo maart mrt marco",
    4: "april apr avril avr abril abr aprile",
    5: "may mai mayo maggio mag mei maio",
    6: "june jun juni juin junio giugno giu junho",
    7: "july jul juli juillet juil julio luglio lug julho",
    8: "august aug augustus aout agosto ago",
    9: "september sep sept septembre septiembre setiembre settembre sett set setembro",
    10: "october oct oktober okt octobre octubre ottobre ott outubro out",
    11: "november nov novembre noviembre novembro",
    12: "december dec dezember dez decembre diciembre dic dicembre decembro dezembro",
}

MONTHS: dict[str, int] = {}
for _month, _names in _MONTH_NAMES.items():
    for _name in _names.split():
        if MONTHS.setdefault(_name, _month) != _month:  # pragma: no cover - guards the table
            raise RuntimeError(f"month name {_name!r} maps to two months")

_IGNORED_WORDS = frozenset({"of", "de", "del", "d", "the", "von", "di", "du", "le", "el"})
_ORDINAL_SUFFIXES = frozenset({"st", "nd", "rd", "th", "er", "ere", "e", "eme", "o", "a"})

_TIME_SUFFIX = re.compile(r"[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?$")
_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})")
_YMD = re.compile(r"(\d{4})([-/.])(\d{1,2})\2(\d{1,2})")
_DMY = re.compile(r"(\d{1,2})([-/.])(\d{1,2})\2(\d{4})")
_TOKENS = re.compile(r"[^\W\d_]+|\d+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _build(year: int, month: int, day: int) -> date:
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise ValueError(f"year {year} out of range")
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"invalid calendar date: {exc}") from exc


def _numeric(first: int, sep: str, second: int, year: int, *, day_first: bool) -> date:
    if sep == ".":
        return _build(year, second, first)
    if first > 12 >= second:
        return _build(year, second, first)
    if second > 12 >= first:
        return _build(year, first, second)
    if first > 12 and second > 12:
        raise ValueError("neither part is a valid month")
    return _build(year, second, first) if day_first else _build(year, first, second)


def _named_month(text: str) -> date:
    month: int | None = None
    numbers: list[str] = []
    previous_numeric = False
    for token in _TOKENS.findall(_strip_accents(text.lower())):
        if token.isdigit():
            numbers.append(token)
            previous_numeric = True
            continue
        if token in MONTHS:
            if month is not None:
                raise ValueError("more than one month name")
            month = MONTHS[token]
        elif not (token in _IGNORED_WORDS or (previous_numeric and token in _ORDINAL_SUFFIXES)):
            raise ValueError(f"unrecognised word {token!r}")
        previous_numeric = False
    if month is None:
        raise ValueError("no month found")
    years = [n for n in numbers if len(n) == 4]
    days = [n for n in numbers if len(n) <= 2]
    if len(numbers) != 2 or len(years) != 1 or len(days) != 1:
        raise ValueError("expected exactly one day and one four-digit year")
    return _build(int(years[0]), month, int(days[0]))


def parse_date(text: str, *, day_first: bool = True) -> date:
    """Parse a date written in any supported format. Raises ``ValueError``."""
    s = unicodedata.normalize("NFKC", text).strip()
    if not s:
        raise ValueError("empty date")
    s = _TIME_SUFFIX.sub("", s).strip()
    if m := _COMPACT.fullmatch(s):
        return _build(int(m[1]), int(m[2]), int(m[3]))
    if m := _YMD.fullmatch(s):
        return _build(int(m[1]), int(m[3]), int(m[4]))
    if m := _DMY.fullmatch(s):
        return _numeric(int(m[1]), m[2], int(m[3]), int(m[4]), day_first=day_first)
    if any(c.isalpha() for c in s):
        return _named_month(s)
    raise ValueError("unrecognised date format")
