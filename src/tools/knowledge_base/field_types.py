"""
field_types.py
==============
Type system for claim field values.

Each :class:`FieldType` subclass defines two operations:

normalize(value)
    Canonicalise a raw extracted string so that semantically identical values
    produced by different LLM runs end up in the same bucket.
    Applied immediately after extraction before any storage or comparison.

compare(a, b)
    Return ``True`` if two *normalised* values represent the same fact.
    Used by the deterministic merger to group observations across documents.

Available types
---------------
StringType      — plain text; case-insensitive equality.
NarrativeType   — long free-form text; loose prefix-based equality.
DateType        — calendar dates; parsed to ISO-8601 (YYYY-MM-DD).
CurrencyType    — monetary amounts; canonical form ``"ISO_CODE AMOUNT"``
                  (e.g. ``"EUR 12650"``), integer, no decimals/separators.
EnumType        — fixed vocabulary; aliases are collapsed to canonical values.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────


class FieldType(ABC):
    """Abstract base for all claim-field types."""

    #: Human-readable name shown in logs and reports.
    name: str = "abstract"

    @abstractmethod
    def normalize(self, value: str | None) -> str | None:
        """Return the canonical form of *value*, or ``None`` if the value is absent."""
        ...

    @abstractmethod
    def compare(self, a: str | None, b: str | None) -> bool:
        """Return ``True`` if *a* and *b* represent the same fact."""
        ...

    def __repr__(self) -> str:
        return f"<FieldType:{self.name}>"


# ─────────────────────────────────────────────────────────────────────────────
# Concrete types
# ─────────────────────────────────────────────────────────────────────────────


class StringType(FieldType):
    """
    Plain text.

    * **normalize** — strip leading/trailing whitespace.
    * **compare**   — case-insensitive equality after stripping.
    """

    name = "string"

    def normalize(self, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    def compare(self, a: str | None, b: str | None) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return (self.normalize(a) or "").lower() == (self.normalize(b) or "").lower()


class NarrativeType(FieldType):
    """
    Long free-form text (incident descriptions, damage summaries, …).

    * **normalize** — collapse internal whitespace runs to a single space.
    * **compare**   — compare the first ``prefix_length`` characters
      (lowercased) to decide if two narratives are "the same".
      Full equality would almost never match across documents; this
      heuristic catches copy-paste reuse while still flagging real updates.
    """

    name = "narrative"

    def __init__(self, prefix_length: int = 120) -> None:
        self.prefix_length = prefix_length

    def normalize(self, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(str(value).split())

    def compare(self, a: str | None, b: str | None) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        na = (self.normalize(a) or "")[: self.prefix_length].lower()
        nb = (self.normalize(b) or "")[: self.prefix_length].lower()
        return na == nb


class DateType(FieldType):
    """
    Calendar date.

    * **normalize** — parse common date formats and emit ``"YYYY-MM-DD"``.
      If parsing fails the raw string is returned unchanged.
    * **compare**   — equality of normalised strings.
    """

    name = "date"

    _FORMATS: list[str] = [
        "%Y-%m-%d",      # ISO-8601 — try first
        "%d.%m.%Y",      # European dot-separated
        "%d/%m/%Y",      # European slash-separated
        "%m/%d/%Y",      # US slash-separated
        "%d-%m-%Y",      # European dash-separated
        "%B %d, %Y",     # "March 28, 2026"
        "%d %B %Y",      # "28 March 2026"
        "%b %d, %Y",     # "Mar 28, 2026"
        "%d %b %Y",      # "28 Mar 2026"
        "%Y%m%d",        # Compact
    ]

    def normalize(self, value: str | None) -> str | None:
        if value is None:
            return None
        s = str(value).strip()
        for fmt in self._FORMATS:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return s  # Return raw if no format matched

    def compare(self, a: str | None, b: str | None) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return self.normalize(a) == self.normalize(b)


class CurrencyType(FieldType):
    """
    Monetary amount with currency code.

    * **normalize** — produce ``"ISO_CODE AMOUNT"`` (e.g. ``"EUR 12650"``).
      Strips decimals, thousands separators, and currency symbols.
      Accepts amounts written before or after the code/symbol.
    * **compare**   — equality of normalised strings.

    Examples of accepted inputs::

        "EUR 12,650"   → "EUR 12650"
        "€12.650,00"   → "EUR 12650"   (European decimal notation)
        "12650 EUR"    → "EUR 12650"
        "USD 1,000,000"→ "USD 1000000"
    """

    name = "currency"

    # Two patterns tried in order:
    # 1. code/symbol BEFORE amount  (e.g. "EUR 12,650", "€12650")
    _RE_CODE_FIRST = re.compile(
        r"(?:(?P<code>[A-Z]{3})|(?P<sym>[€\$£¥₹]))\s*(?P<amount>[\d][\d,.'\s]*)",
    )
    # 2. amount BEFORE code         (e.g. "12,650 EUR", "1000000USD")
    _RE_AMOUNT_FIRST = re.compile(
        r"(?P<amount>[\d][\d,.'\s]*)\s*(?P<code>[A-Z]{3})",
    )
    _SYMBOL_TO_ISO: dict[str, str] = {
        "€": "EUR",
        "$": "USD",
        "£": "GBP",
        "¥": "JPY",
        "₹": "INR",
    }

    def _parse(self, value: str) -> tuple[str, int] | None:
        s = value.strip()

        # Try code/symbol first, then amount-first
        m = self._RE_CODE_FIRST.search(s)
        if m:
            code = m.group("code")
            sym = m.group("sym")
            if not code and sym:
                code = self._SYMBOL_TO_ISO.get(sym, "UNK")
            raw_amount = m.group("amount")
        else:
            m = self._RE_AMOUNT_FIRST.search(s)
            if not m:
                return None
            code = m.group("code")
            raw_amount = m.group("amount")

        if not code:
            code = "UNK"

        # Strip all non-digit characters (separators, spaces)
        # First, handle decimal part: if it ends with .XX or ,XX, strip it
        # (heuristic: if there's a separator followed by 1 or 2 digits at the end, it's cents)
        stripped_amount = re.sub(r"[.,]\d{1,2}$", "", raw_amount)
        
        digits_only = re.sub(r"[^\d]", "", stripped_amount)
        if not digits_only:
            return None
        try:
            amount = int(digits_only)
        except ValueError:
            return None
        return (code.upper(), amount)

    def normalize(self, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = self._parse(str(value))
        if parsed is None:
            return str(value).strip()
        code, amount = parsed
        return f"{code} {amount}"

    def compare(self, a: str | None, b: str | None) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return self.normalize(a) == self.normalize(b)


class EnumType(FieldType):
    """
    Fixed-vocabulary field.

    * **normalize** — lower-case, strip, then map through *aliases* to the
      canonical value.  Values not in the vocabulary are kept as-is
      (lowercased) so the system degrades gracefully.
    * **compare**   — equality of normalised strings.

    Parameters
    ----------
    allowed_values:
        Canonical vocabulary entries (case-insensitive).
    aliases:
        Optional mapping of alternative spellings to canonical values
        (e.g. ``{"res": "residential", "comm": "commercial"}``).
    """

    name = "enum"

    def __init__(
        self,
        allowed_values: list[str],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.allowed_values = [v.lower().strip() for v in allowed_values]
        self.aliases: dict[str, str] = (
            {k.lower().strip(): v.lower().strip() for k, v in aliases.items()}
            if aliases
            else {}
        )

    def normalize(self, value: str | None) -> str | None:
        if value is None:
            return None
        s = str(value).strip().lower()
        return self.aliases.get(s, s)

    def compare(self, a: str | None, b: str | None) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return self.normalize(a) == self.normalize(b)
