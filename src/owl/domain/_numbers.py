"""Locale-tolerant number parsing (internal to the domain).

Documents are international, so ``1,000.50``, ``1.000,50``, ``1 000,50`` and ``1'000.50`` are all
accepted. The one real ambiguity is a *lone* separator such as ``1.000``: see ``LoneSeparator``.
"""

import re
from decimal import Decimal, localcontext
from enum import StrEnum

_MAX_LENGTH = 40
_GROUP_CHARS = " \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}'\N{RIGHT SINGLE QUOTATION MARK}"
_ALLOWED = re.compile(r"[\d.," + _GROUP_CHARS + r"]+")
_GROUPING_SPACE = re.compile(r"(?<=\d)[" + _GROUP_CHARS + r"](?=\d{3}(?!\d))")


class LoneSeparator(StrEnum):
    """How to read a number with a single ``.`` or ``,`` (e.g. ``1.000``)."""

    #: Exactly three digits after it, 1-3 non-zero-leading digits before it → thousands grouping.
    #: Otherwise a decimal point. Used where fractions are unlikely (integers, money amounts).
    GROUP_IF_THREE_DIGITS = "group_if_three_digits"
    #: Always a decimal point (decimals, percentages). Repeated separators are still grouping.
    DECIMAL = "decimal"


def _grouped(int_part: str, sep: str) -> bool:
    return re.fullmatch(rf"\d{{1,3}}(?:{re.escape(sep)}\d{{3}})+", int_part) is not None


def parse_number(text: str, lone: LoneSeparator) -> Decimal:
    """Parse a plain number (optional sign, digits, separators). Raises ``ValueError``."""
    s = text.strip()
    if not s or len(s) > _MAX_LENGTH:
        raise ValueError("empty or too long number")
    sign = ""
    if s[0] in "+-":
        sign, s = s[0], s[1:].strip()
    if not s or not s[0].isdigit() or not s[-1].isdigit() or not _ALLOWED.fullmatch(s):
        raise ValueError("not a number")
    s = _GROUPING_SPACE.sub("", s)
    if any(c in _GROUP_CHARS for c in s):
        raise ValueError("misplaced grouping character")
    if not s.isascii():
        raise ValueError("not a number")

    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        decimal_sep = "." if s.rfind(".") > s.rfind(",") else ","
        group_sep = "," if decimal_sep == "." else "."
        int_part, _, frac = s.rpartition(decimal_sep)
        if not frac.isdigit() or group_sep in frac or not _grouped(int_part, group_sep):
            raise ValueError("inconsistent separators")
        digits = f"{int_part.replace(group_sep, '')}.{frac}"
    elif dots or commas:
        sep = "." if dots else ","
        if s.count(sep) > 1:
            if not _grouped(s, sep):
                raise ValueError("inconsistent separators")
            digits = s.replace(sep, "")
        else:
            int_part, frac = s.split(sep)
            if not int_part or not frac:
                raise ValueError("incomplete number")
            is_grouping = (
                lone is LoneSeparator.GROUP_IF_THREE_DIGITS
                and len(frac) == 3
                and 1 <= len(int_part) <= 3
                and int_part != "0"
            )
            digits = int_part + frac if is_grouping else f"{int_part}.{frac}"
    else:
        digits = s

    with localcontext() as ctx:
        ctx.prec = 60
        return Decimal(sign + digits)
