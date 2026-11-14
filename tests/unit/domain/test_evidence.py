from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from owl.domain.evidence import (
    MatcherConfig,
    MatchKind,
    QuoteMatcher,
    normalise,
    value_in_quotes,
)
from owl.domain.values import FieldType, ListValue, Money, Value, ValueSpec, parse_value

# --- normaliser ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello   World\n\nfoo", "hello world foo"),
        ("  padded \t text  ", "padded text"),
        ("“Limit” isn\N{RIGHT SINGLE QUOTATION MARK}t", '"limit" isn\'t'),
        ("«quote»", '"quote"'),
        ("A\N{EN DASH}B—C\N{MINUS SIGN}D", "a-b-c-d"),
        ("ﬁnance", "finance"),  # ligature (NFKC)
        (
            "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT TWO}\N{FULLWIDTH DIGIT THREE}",
            "123",
        ),  # full-width digits (NFKC)
        ("insur­ance", "insurance"),  # soft hyphen
        ("zero​width", "zerowidth"),
        ("insur-\nance cover", "insurance cover"),
        ("insur-  \r\n  ance", "insurance"),
        ("well-known", "well-known"),  # a real hyphen is kept
        ("end -\nnext", "end - next"),  # hyphen preceded by a space is not hyphenation
        ("2026-\n03", "2026- 03"),  # only letters are joined
        ("école", "école"),  # decomposed accent composes
        ("STRASSE Straße", "strasse strasse"),
        ("", ""),
        ("   \n ", ""),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    assert normalise(raw).text == expected


def test_normalise_offsets_map_back_to_the_source() -> None:
    raw = "  Limit:\n  USD   5,000 "
    n = normalise(raw)
    assert n.text == "limit: usd 5,000"
    start, end = n.span(n.text.index("usd"), n.text.index("usd") + len("usd 5,000"))
    assert raw[start:end] == "USD   5,000"


def test_normalise_offsets_cover_removed_hyphenation() -> None:
    raw = "the insur-\nance cover"
    n = normalise(raw)
    i = n.text.index("insurance")
    start, end = n.span(i, i + len("insurance"))
    assert raw[start:end] == "insur-\nance"


# --- exact matching --------------------------------------------------------------------------

SOURCE = (
    "SLIP\nThe limit of liability is USD 5,000,000 any one occurrence and in the annual "
    "aggregate.\n"
    "Period: 01/03/2026 to 28/02/2027. “Insured” means "
    "Acme Corp\N{RIGHT SINGLE QUOTATION MARK}s subsidiaries; "
    "the insur-\nance cover is annual."
)


@pytest.mark.parametrize(
    ("quote", "expected_source"),
    [
        ("USD 5,000,000 any one occurrence", "USD 5,000,000 any one occurrence"),
        ("usd 5,000,000 ANY ONE occurrence", "USD 5,000,000 any one occurrence"),  # case
        ("USD   5,000,000\nany one   occurrence", "USD 5,000,000 any one occurrence"),  # spaces
        ("Period: 01/03/2026 to 28/02/2027", "Period: 01/03/2026 to 28/02/2027"),
        ('"Insured" means Acme Corp\'s subsidiaries', None),  # curly quotes in source
        ("the insurance cover is annual", None),  # hyphenation in source
        ("the insur-\nance cover is annual", None),  # hyphenation in both
    ],
)
def test_exact_matches(quote: str, expected_source: str | None) -> None:
    match = QuoteMatcher().match(quote, SOURCE)
    assert match is not None
    assert match.kind is MatchKind.EXACT
    assert SOURCE[match.start : match.end] == match.source_text
    if expected_source is not None:
        assert match.source_text == expected_source


def test_exact_match_returns_the_real_source_for_normalised_differences() -> None:
    match = QuoteMatcher().match('"Insured" means Acme Corp\'s subsidiaries', SOURCE)
    assert match is not None
    assert (
        match.source_text
        == "“Insured” means Acme Corp\N{RIGHT SINGLE QUOTATION MARK}s subsidiaries"
    )
    match = QuoteMatcher().match("the insurance cover is annual", SOURCE)
    assert match is not None
    assert match.source_text == "the insur-\nance cover is annual"


def test_first_occurrence_wins() -> None:
    match = QuoteMatcher().match("abc", "xx abc yy abc")
    assert match is not None
    assert match.start == 3


def test_accepts_a_prepared_source() -> None:
    prepared = normalise(SOURCE)
    match = QuoteMatcher().match("annual aggregate", prepared, original=SOURCE)
    assert match is not None
    assert match.source_text == "annual aggregate"


def test_empty_or_oversized_quotes_do_not_match() -> None:
    assert QuoteMatcher().match("", SOURCE) is None
    assert QuoteMatcher().match("  \n ", SOURCE) is None
    assert QuoteMatcher().match("short", "sh") is None
    assert QuoteMatcher().match("x" * 200, SOURCE) is None


# --- fuzzy matching --------------------------------------------------------------------------


def test_fuzzy_accepts_ocr_noise_on_long_quotes() -> None:
    quote = "The Iimit of liability is USD 5,000,000 any one occurrence"  # I for l
    match = QuoteMatcher().match(quote, SOURCE)
    assert match is not None
    assert match.kind is MatchKind.FUZZY
    assert match.source_text == "The limit of liability is USD 5,000,000 any one occurrence"
    assert SOURCE[match.start : match.end] == match.source_text  # replaced quote == real source


def test_fuzzy_window_does_not_cut_words() -> None:
    quote = "limit of liabi1ity is USD 5,000,000 any one occurrenc"
    match = QuoteMatcher().match(quote, SOURCE)
    assert match is not None
    assert match.kind is MatchKind.FUZZY
    assert match.source_text.startswith("limit")
    assert match.source_text.endswith("occurrence")


def test_short_quotes_never_match_fuzzily() -> None:
    assert QuoteMatcher().match("USD 5,000,00O", SOURCE) is None  # 13 chars, one typo
    assert len("limit of liabilty is") < 25
    assert QuoteMatcher().match("limit of liabilty is", SOURCE) is None


def test_no_false_match_on_unrelated_text() -> None:
    quote = "The reinsurer shall pay all sums due within thirty days of notice"
    assert QuoteMatcher().match(quote, SOURCE) is None


def test_too_noisy_quotes_are_rejected() -> None:
    quote = "Tne Iirnit of Iiabi1ity is U5D 5,OOO,OOO any one occurrence"
    assert QuoteMatcher().match(quote, SOURCE) is None


def test_matcher_config_is_respected() -> None:
    quote = "limit of liabilty is USD 5,000,000"
    assert QuoteMatcher().match(quote, SOURCE) is not None  # 34 chars, fuzzy by default
    assert QuoteMatcher(MatcherConfig(fuzzy_threshold=100.0)).match(quote, SOURCE) is None
    assert QuoteMatcher(MatcherConfig(fuzzy_min_length=50)).match(quote, SOURCE) is None
    short = "limit of liabilty is"
    assert QuoteMatcher(MatcherConfig(fuzzy_min_length=10)).match(short, SOURCE) is not None


@given(
    text=st.text(alphabet="abcdefg hij\nklm,.-", min_size=1, max_size=120),
    data=st.data(),
)
def test_any_normalised_substring_matches_exactly(text: str, data: st.DataObject) -> None:
    n = normalise(text)
    if len(n.text) < 2:
        return
    i = data.draw(st.integers(0, len(n.text) - 1))
    j = data.draw(st.integers(i + 1, len(n.text)))
    quote = n.text[i:j]
    if not quote.strip():
        return
    match = QuoteMatcher().match(quote, text)
    assert match is not None
    assert match.kind is MatchKind.EXACT
    assert quote.strip() in normalise(match.source_text).text


# --- value-in-quote (CONTRACT 8.3) -----------------------------------------------------------

S = ValueSpec(FieldType.STRING)
I = ValueSpec(FieldType.INTEGER)  # noqa: E741
DEC = ValueSpec(FieldType.DECIMAL)
B = ValueSpec(FieldType.BOOL)
DT = ValueSpec(FieldType.DATE)
M = ValueSpec(FieldType.MONEY)
P = ValueSpec(FieldType.PERCENTAGE)
CON = ValueSpec(FieldType.ENUM, allowed_values=("masonry", "concrete", "steel"))
LS = ValueSpec(FieldType.LIST, item_type=FieldType.STRING)
LE = ValueSpec(FieldType.LIST, item_type=FieldType.ENUM, allowed_values=("fire", "flood", "theft"))


def has(spec: ValueSpec, value: object, *quotes: str) -> bool:
    return value_in_quotes(spec, parse_value(spec, value), list(quotes))


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("Period: 01/03/2026 to 28/02/2027", True),  # the contract example
        ("Inception 1 March 2026.", True),
        ("Beginn: 1. März 2026", True),
        ("Inception: 2026-03-01 (UTC)", True),
        ("Period: 02/03/2026 to 28/02/2027", False),
        ("No dates mentioned", False),
    ],
)
def test_date_in_quote(quote: str, found: bool) -> None:
    assert has(DT, date(2026, 3, 1), quote) is found


def test_date_in_quote_finds_the_second_date() -> None:
    assert has(DT, date(2027, 2, 28), "Period: 01/03/2026 to 28/02/2027")


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("Limit: USD 5,000,000 any one occurrence", True),
        ("Limit: USD 5m any one occurrence", True),
        ("Limit: 5 000 000 US dollars", True),
        ("(5.000.000,00 USD)", True),
        ("Limit: $5,000,000.", True),
        ("Limit: EUR 5,000,000", False),  # currency mismatch
        ("Limit: USD 7,500,000", False),
        ("Limit: 5,000,000", False),  # no currency
    ],
)
def test_money_in_quote(quote: str, found: bool) -> None:
    assert has(M, Money(Decimal(5000000), "USD"), quote) is found


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("Brokerage of 15% of premium", True),
        ("Brokerage 15 % applies", True),
        ("Brokerage fifteen percent (15 percent)", True),
        ("Brokerage of 25% of premium", False),
        ("Brokerage of 15 of premium", False),
    ],
)
def test_percentage_in_quote(quote: str, found: bool) -> None:
    assert has(P, "15%", quote) is found


def test_other_types_in_quote() -> None:
    assert has(I, 1998, "Built in 1998, renovated 2015.")
    assert not has(I, 1999, "Built in 1998, renovated 2015.")
    assert has(DEC, "1.25", "Rate on line 1.25 for the layer")
    assert has(B, True, "Sprinklered: Yes")
    assert has(B, False, "Sprinklered: Nein")
    assert not has(B, True, "Sprinklered: No")
    assert has(CON, "masonry", "Construction: Masonry, 3 floors")
    assert not has(CON, "steel", "Construction: Masonry, 3 floors")
    assert has(S, "Acme Insurance", "Insured: ACME   Insurance Ltd")
    assert has(S, "Acme Insurance", "Insured: acme\ninsur-\nance Ltd")  # hyphenation is undone
    assert not has(S, "Acme Insurance", "Insured: Zeta Re")


def test_found_in_any_one_of_several_quotes() -> None:
    assert has(DT, date(2026, 3, 1), "irrelevant", "Period: 01/03/2026 to 28/02/2027")
    assert not has(DT, date(2026, 3, 1), "irrelevant", "also irrelevant")
    assert not has(DT, date(2026, 3, 1))  # no quotes at all


def test_every_list_item_must_be_found_in_some_quote() -> None:
    assert has(LS, ["Germany", "France"], "Territory: Germany and France")
    assert has(LS, ["Germany", "France"], "Territory: Germany", "Also covers France")
    assert not has(LS, ["Germany", "France"], "Territory: Germany only")
    assert has(LE, ["fire", "flood"], "Perils: fire; flood", "")
    assert not has(LE, ["fire", "theft"], "Perils: fire; flood")


def test_value_in_quotes_accepts_a_list_value_object() -> None:
    value: Value = parse_value(LS, ["a", "b"])
    assert isinstance(value, ListValue)
    assert value_in_quotes(LS, value, ["a b"])
