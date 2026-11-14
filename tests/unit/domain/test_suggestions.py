from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from owl.domain.catalog import Applicability, Catalog, FieldDefinition
from owl.domain.evidence import MatchKind
from owl.domain.suggestions import (
    Action,
    Candidate,
    Evidence,
    Mode,
    SuggestionResult,
    WarningCode,
    reconcile,
)
from owl.domain.taxonomy import BusinessType, Lob
from owl.domain.values import FieldType, Money, Value

ALL = Applicability(frozenset(Lob), frozenset(BusinessType))
DOC1 = UUID("00000000-0000-4000-8000-000000000001")
DOC2 = UUID("00000000-0000-4000-8000-000000000002")


def defn(name: str, type: FieldType) -> FieldDefinition:
    return FieldDefinition(name, type, "A field.", ALL, "g")


CATALOG = Catalog(
    1,
    (
        defn("inception", FieldType.DATE),
        defn("limit", FieldType.MONEY),
        defn("insured", FieldType.STRING),
    ),
)
ACTIVE = CATALOG.active_fields(Lob.CASUALTY, BusinessType.DIRECT)


D = date(2026, 1, 1)


def ev(
    quote: str = "q",
    doc: UUID = DOC1,
    kind: MatchKind = MatchKind.EXACT,
    on: date = D,
) -> Evidence:
    return Evidence(doc, on, "f.pdf", quote, kind)


def cand(
    field: str,
    value: Value,
    *,
    evidence: tuple[Evidence, ...] | None = None,
    rationale: str = "because",
    confidence: float = 0.5,
    flags: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(field, value, evidence or (ev(),), rationale, confidence, flags)


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


def run(
    candidates: list[Candidate],
    current: dict[str, Value] | None = None,
    mode: Mode = Mode.UPDATE,
) -> SuggestionResult:
    return reconcile(ACTIVE, candidates, current or {}, mode)


# --- table-driven decisions ------------------------------------------------------------------


def test_add_when_no_current_value() -> None:
    result = run([cand("inception", date(2026, 3, 1))])
    [s] = result.suggestions
    assert (s.field, s.action, s.current_value, s.proposed_value) == (
        "inception",
        Action.ADD,
        None,
        date(2026, 3, 1),
    )
    assert result.conflicts == ()


def test_change_when_value_differs() -> None:
    result = run([cand("inception", date(2026, 3, 1))], {"inception": date(2026, 1, 1)})
    [s] = result.suggestions
    assert s.action is Action.CHANGE
    assert s.current_value == date(2026, 1, 1)
    assert s.proposed_value == date(2026, 3, 1)


def test_no_op_when_value_equals_current() -> None:
    result = run([cand("inception", date(2026, 3, 1))], {"inception": date(2026, 3, 1)})
    assert result == SuggestionResult((), (), ())


def test_no_op_when_string_differs_only_in_case() -> None:
    result = run([cand("insured", "ACME RE")], {"insured": "Acme Re"})
    assert result.suggestions == ()
    assert result.conflicts == ()


def test_no_candidates_no_output() -> None:
    assert run([]) == SuggestionResult((), (), ())


def test_conflict_of_two_new_values() -> None:
    result = run([cand("limit", usd("5000000")), cand("limit", usd("7500000"))])
    assert result.suggestions == ()
    [c] = result.conflicts
    assert c.field == "limit"
    assert c.current_value is None
    assert [x.value for x in c.candidates] == [usd("5000000"), usd("7500000")]
    assert [x.is_current for x in c.candidates] == [False, False]


def test_conflict_including_current_value_marks_it() -> None:
    result = run(
        [cand("limit", usd("5000000")), cand("limit", usd("7500000"))],
        {"limit": usd("5000000")},
    )
    assert result.suggestions == ()
    [c] = result.conflicts
    assert c.current_value == usd("5000000")
    assert [x.is_current for x in c.candidates] == [True, False]


def test_conflict_of_two_values_both_differing_from_current() -> None:
    result = run([cand("limit", usd("1")), cand("limit", usd("2"))], {"limit": usd("3")})
    [c] = result.conflicts
    assert [x.is_current for x in c.candidates] == [False, False]


def test_extraction_mode_is_all_add_with_no_current() -> None:
    result = run(
        [cand("inception", date(2026, 3, 1)), cand("insured", "Acme")], mode=Mode.EXTRACTION
    )
    assert [(s.action, s.current_value) for s in result.suggestions] == [
        (Action.ADD, None),
        (Action.ADD, None),
    ]


def test_extraction_mode_rejects_current_values() -> None:
    with pytest.raises(ValueError, match="extraction"):
        run([], {"insured": "x"}, Mode.EXTRACTION)


def test_candidate_for_inactive_field_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="nope"):
        run([cand("nope", "x")])


# --- merging ---------------------------------------------------------------------------------


def test_format_variants_are_merged_into_one_suggestion() -> None:
    result = run(
        [
            cand("insured", "acme re", confidence=0.4, rationale="r1", evidence=(ev("a"),)),
            cand("insured", "Acme Re", confidence=0.9, rationale="r2", evidence=(ev("b", DOC2),)),
        ]
    )
    assert result.conflicts == ()
    [s] = result.suggestions
    assert s.proposed_value == "Acme Re"  # spelling of the highest-confidence candidate
    assert s.confidence == 0.9
    assert s.rationales == ("r1", "r2")  # all rationales kept, input order
    assert [e.quote for e in s.evidence] == ["a", "b"]


def test_confidence_tie_takes_first_spelling() -> None:
    result = run([cand("insured", "ACME", confidence=0.5), cand("insured", "acme", confidence=0.5)])
    assert result.suggestions[0].proposed_value == "ACME"


def test_identical_evidence_is_deduplicated_keeping_first_order() -> None:
    shared = ev("same quote")
    result = run(
        [
            cand("insured", "Acme", evidence=(shared, ev("other"))),
            cand("insured", "ACME", evidence=(ev("same quote"), ev("third"))),
        ]
    )
    [s] = result.suggestions
    assert [e.quote for e in s.evidence] == ["same quote", "other", "third"]


def test_same_quote_in_different_documents_is_not_a_duplicate() -> None:
    result = run([cand("insured", "Acme", evidence=(ev("q", DOC1), ev("q", DOC2)))])
    assert len(result.suggestions[0].evidence) == 2


def test_duplicate_evidence_with_different_match_kind_keeps_first() -> None:
    result = run(
        [
            cand("insured", "Acme", evidence=(ev("q", kind=MatchKind.FUZZY),)),
            cand("insured", "Acme", evidence=(ev("q", kind=MatchKind.EXACT),)),
        ]
    )
    [e] = result.suggestions[0].evidence
    assert e.match is MatchKind.FUZZY


def test_identical_rationales_are_deduplicated() -> None:
    result = run(
        [
            cand("insured", "Acme", rationale="Stated on page 1."),
            cand("insured", "Acme", rationale=" Stated on page 1. "),
            cand("insured", "Acme", rationale=""),
        ]
    )
    assert result.suggestions[0].rationales == ("Stated on page 1.",)


def test_flags_are_united() -> None:
    result = run(
        [
            cand("insured", "Acme", flags=("value_not_in_quote",)),
            cand("insured", "ACME"),
        ]
    )
    assert result.suggestions[0].flags == ("value_not_in_quote",)


def test_merged_conflict_candidates() -> None:
    result = run(
        [
            cand("limit", usd("1"), confidence=0.2, rationale="a"),
            cand("limit", usd("2"), confidence=0.3, rationale="b"),
            cand("limit", usd("1.0"), confidence=0.7, rationale="c"),
        ]
    )
    [c] = result.conflicts
    assert [(x.value, x.confidence, x.rationales) for x in c.candidates] == [
        (usd("1"), 0.7, ("a", "c")),
        (usd("2"), 0.3, ("b",)),
    ]


def test_output_follows_active_field_order() -> None:
    result = run(
        [cand("insured", "Acme"), cand("inception", date(2026, 3, 1))],
    )
    assert [s.field for s in result.suggestions] == ["inception", "insured"]


# --- supersession by later documents (CONTRACT §7.3) ----------------------------------------

JAN, FEB, MAR = date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10)


def dated(field: str, value: Value, on: date, *more: date, rationale: str = "r") -> Candidate:
    evidence = tuple(ev(f"q{d}", on=d) for d in (on, *more))
    return cand(field, value, evidence=evidence, rationale=rationale)


def test_value_from_a_later_document_supersedes_the_older_one() -> None:
    result = run([dated("limit", usd("5"), JAN), dated("limit", usd("7"), MAR)])
    assert result.conflicts == ()
    [s] = result.suggestions
    assert (s.action, s.proposed_value) == (Action.ADD, usd("7"))
    [w] = result.warnings
    assert w.code is WarningCode.CANDIDATE_SUPERSEDED
    assert (w.field, w.group) == ("limit", "g")
    assert "5" in w.message


def test_input_order_is_irrelevant_to_supersession() -> None:
    result = run([dated("limit", usd("7"), MAR), dated("limit", usd("5"), JAN)])
    assert [s.proposed_value for s in result.suggestions] == [usd("7")]


def test_same_date_is_still_a_conflict() -> None:
    result = run([dated("limit", usd("5"), MAR), dated("limit", usd("7"), MAR)])
    assert result.suggestions == ()
    assert len(result.conflicts) == 1
    assert result.warnings == ()


def test_old_value_equal_to_current_is_superseded_by_a_change() -> None:
    result = run(
        [dated("limit", usd("5"), JAN), dated("limit", usd("7"), MAR)], {"limit": usd("5")}
    )
    [s] = result.suggestions
    assert (s.action, s.current_value, s.proposed_value) == (Action.CHANGE, usd("5"), usd("7"))
    assert result.conflicts == ()


def test_latest_value_equal_to_current_gives_no_suggestion_but_a_warning() -> None:
    result = run(
        [dated("limit", usd("5"), JAN), dated("limit", usd("7"), MAR)], {"limit": usd("7")}
    )
    assert result.suggestions == ()
    assert result.conflicts == ()
    assert [w.code for w in result.warnings] == [WarningCode.CANDIDATE_SUPERSEDED]


def test_a_value_restated_in_a_later_document_is_not_superseded() -> None:
    # 5 is supported by January and March, 7 only by March: the latest support ties
    result = run([dated("limit", usd("5"), JAN, MAR), dated("limit", usd("7"), MAR)])
    assert len(result.conflicts) == 1
    assert result.warnings == ()


def test_three_values_two_tied_latest_conflict_and_the_old_one_is_superseded() -> None:
    result = run(
        [
            dated("limit", usd("1"), JAN),
            dated("limit", usd("2"), MAR),
            dated("limit", usd("3"), MAR),
        ]
    )
    [c] = result.conflicts
    assert [x.value for x in c.candidates] == [usd("2"), usd("3")]
    assert [w.code for w in result.warnings] == [WarningCode.CANDIDATE_SUPERSEDED]


def test_supersession_applies_in_extraction_mode_too() -> None:
    result = run(
        [dated("limit", usd("5"), JAN), dated("limit", usd("7"), FEB)], mode=Mode.EXTRACTION
    )
    assert [(s.action, s.proposed_value) for s in result.suggestions] == [(Action.ADD, usd("7"))]


# --- invariants ------------------------------------------------------------------------------


def test_candidate_needs_evidence() -> None:
    with pytest.raises(ValueError, match="evidence"):
        Candidate("insured", "Acme", (), "r", 0.5)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_candidate_confidence_must_be_a_probability(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        cand("insured", "Acme", confidence=confidence)


def test_candidate_value_cannot_be_none() -> None:
    with pytest.raises(ValueError, match="value"):
        cand("insured", None)  # type: ignore[arg-type]


_values = st.sampled_from(["a", "A", "b", "B", "c"])


@given(
    cands=st.lists(
        st.builds(
            lambda v, c: cand("insured", v, confidence=c),
            _values,
            st.floats(0, 1),
        ),
        max_size=6,
    ),  # all evidence has the same date, so nothing is superseded here
    current=st.one_of(st.none(), _values),
)
def test_field_never_in_both_and_confidence_is_max(
    cands: list[Candidate], current: str | None
) -> None:
    result = run(cands, {"insured": current} if current is not None else {})
    in_suggestions = {s.field for s in result.suggestions}
    in_conflicts = {c.field for c in result.conflicts}
    assert not in_suggestions & in_conflicts

    distinct = {c.value.casefold() for c in cands}  # type: ignore[union-attr]
    for s in result.suggestions:
        group = [c for c in cands if c.value.casefold() == s.proposed_value.casefold()]  # type: ignore[union-attr]
        assert s.confidence == max(c.confidence for c in group)
    if len(distinct) >= 2:
        assert in_conflicts == {"insured"}
    elif len(distinct) == 1 and current is not None and current.casefold() in distinct:
        assert result == SuggestionResult((), (), ())
    elif len(distinct) == 1:
        assert in_suggestions == {"insured"}
    else:
        assert not in_suggestions
        assert not in_conflicts
