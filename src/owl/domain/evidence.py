"""Evidence grounding: quote normalisation and matching (§8.2), value-in-quote check (§8.3)."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz.fuzz import partial_ratio_alignment

from owl.domain.values import (
    DEFAULT_OPTIONS,
    FieldType,
    ListValue,
    ParseOptions,
    Scalar,
    Value,
    ValueParseError,
    ValueSpec,
    parse_value,
    values_equal,
)

# --- normalisation ---------------------------------------------------------------------------

_CHAR_MAP = str.maketrans(
    {
        **dict.fromkeys(
            "\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
            "\N{SINGLE LOW-9 QUOTATION MARK}\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}"
            "\N{PRIME}\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}"
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}",
            "'",
        ),
        **dict.fromkeys(
            "\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
            "\N{DOUBLE LOW-9 QUOTATION MARK}\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}"
            "\N{DOUBLE PRIME}\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}"
            "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}",
            '"',
        ),
        **dict.fromkeys(
            "\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{FIGURE DASH}\N{EN DASH}\N{EM DASH}"
            "\N{HORIZONTAL BAR}\N{MINUS SIGN}",
            "-",
        ),
        **dict.fromkeys(
            [
                "\N{SOFT HYPHEN}",
                "\N{ZERO WIDTH SPACE}",
                "\N{ZERO WIDTH NON-JOINER}",
                "\N{ZERO WIDTH JOINER}",
                "\N{WORD JOINER}",
                "\N{ZERO WIDTH NO-BREAK SPACE}",
            ],
            None,
        ),
    }
)
# A hyphen between two letters, followed by a line break: "insur-\nance" -> "insurance".
_HYPHENATION = re.compile(r"(?<=[^\W\d_])-[ \t]*(?:\r\n|\n|\r)[ \t]*(?=[^\W\d_])")


@dataclass(frozen=True)
class NormalisedText:
    """Normalised text plus, for every character, the span of the original text it came from."""

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original-text span covering the normalised range ``[start, end)``."""
        return self.starts[start], self.ends[end - 1]


def normalise(text: str) -> NormalisedText:
    """NFKC, unified quotes/dashes, lower case, collapsed whitespace, un-hyphenated line breaks."""
    entries: list[tuple[str, int, int]] = []
    i = 0
    while i < len(text):
        j = i + 1
        while j < len(text) and unicodedata.combining(text[j]):
            j += 1
        cluster = unicodedata.normalize("NFKC", text[i:j]).casefold().translate(_CHAR_MAP)
        entries.extend((char, i, j) for char in cluster)
        i = j

    removed: set[int] = set()
    for match in _HYPHENATION.finditer("".join(char for char, _, _ in entries)):
        removed.update(range(match.start(), match.end()))

    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pending: list[int] | None = None  # original span of a whitespace run
    for index, (char, start, end) in enumerate(entries):
        if index in removed:
            continue
        if char.isspace():
            pending = [start, end] if pending is None else [pending[0], end]
            continue
        if pending is not None:
            if out:
                out.append(" ")
                starts.append(pending[0])
                ends.append(pending[1])
            pending = None
        out.append(char)
        starts.append(start)
        ends.append(end)
    return NormalisedText("".join(out), tuple(starts), tuple(ends))


# --- quote matching --------------------------------------------------------------------------


class MatchKind(StrEnum):
    EXACT = "exact"
    FUZZY = "fuzzy"


@dataclass(frozen=True)
class MatcherConfig:
    fuzzy_min_length: int = 25  # normalised quote length below which fuzzy matching is never tried
    fuzzy_threshold: float = 92.0  # rapidfuzz partial_ratio, 0-100


@dataclass(frozen=True)
class QuoteMatch:
    kind: MatchKind
    source_text: str  # the actual text of the source; replaces the model's quote when fuzzy
    start: int  # offsets into the original source text
    end: int


class QuoteMatcher:
    """Locates a model-provided quote in a document (CONTRACT §8.2)."""

    def __init__(self, config: MatcherConfig = MatcherConfig()) -> None:
        self._config = config

    def match(
        self, quote: str, source: str | NormalisedText, *, original: str | None = None
    ) -> QuoteMatch | None:
        """Exact (normalised) match first, then fuzzy for long quotes, else ``None``.

        ``source`` may be a prepared ``NormalisedText`` (to avoid re-normalising a large document
        for every quote); then ``original`` must be the text it was prepared from.
        """
        if isinstance(source, str):
            original, prepared = source, normalise(source)
        else:
            if original is None:
                raise ValueError("original text is required with a prepared source")
            prepared = source
        needle = normalise(quote).text
        haystack = prepared.text
        if not needle:
            return None

        index = haystack.find(needle)
        if index >= 0:
            return self._result(MatchKind.EXACT, prepared, original, index, index + len(needle))

        if len(needle) < self._config.fuzzy_min_length or len(needle) > len(haystack):
            return None
        alignment = partial_ratio_alignment(
            needle, haystack, score_cutoff=self._config.fuzzy_threshold
        )
        if alignment is None:
            return None
        start, end = alignment.dest_start, alignment.dest_end
        while start > 0 and haystack[start - 1].isalnum() and haystack[start].isalnum():
            start -= 1
        while end < len(haystack) and haystack[end - 1].isalnum() and haystack[end].isalnum():
            end += 1
        return self._result(MatchKind.FUZZY, prepared, original, start, end)

    @staticmethod
    def _result(
        kind: MatchKind, prepared: NormalisedText, original: str, start: int, end: int
    ) -> QuoteMatch:
        source_start, source_end = prepared.span(start, end)
        return QuoteMatch(kind, original[source_start:source_end], source_start, source_end)


# --- value-in-quote (soft check) -------------------------------------------------------------

_MAX_WINDOW = 6  # tokens; enough for "5 000 000 US dollars" or "1 de marzo de 2026"
_EDGE_PUNCTUATION = (
    " \t\r\n.,;:!?()[]{}<>\"'\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
    "\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
)


def value_in_quotes(
    spec: ValueSpec,
    value: Value,
    quotes: Sequence[str],
    options: ParseOptions = DEFAULT_OPTIONS,
) -> bool:
    """Does the type parser find ``value`` in at least one quote? (CONTRACT §8.3)

    For a list, every item must be found in at least one quote (not necessarily the same one).
    """
    if isinstance(value, ListValue):
        item_spec = spec.item_spec
        return all(
            any(_scalar_in_quote(item_spec, item, quote, options) for quote in quotes)
            for item in value.items
        )
    return any(_scalar_in_quote(spec, value, quote, options) for quote in quotes)


def _scalar_in_quote(spec: ValueSpec, value: Scalar, quote: str, options: ParseOptions) -> bool:
    if spec.type is FieldType.STRING:
        needle = normalise(str(value)).text
        return bool(needle) and needle in normalise(quote).text
    tokens = quote.split()
    for start in range(len(tokens)):
        for size in range(1, _MAX_WINDOW + 1):
            if start + size > len(tokens):
                break
            window = " ".join(tokens[start : start + size]).strip(_EDGE_PUNCTUATION)
            if not window:
                continue
            try:
                parsed = parse_value(spec, window, options)
            except ValueParseError:
                continue
            if not isinstance(parsed, ListValue) and values_equal(spec, parsed, value):
                return True
    return False
