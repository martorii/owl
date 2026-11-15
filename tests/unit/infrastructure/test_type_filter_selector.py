from datetime import date
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from owl.application.ports import ContextSelector
from owl.domain.bundle import Document, DocumentBundle
from owl.domain.catalog import ActiveField, Applicability, FieldDefinition, FieldGroup
from owl.domain.context import ExtractionContext
from owl.domain.suggestions import WarningCode
from owl.domain.taxonomy import BusinessType, DocumentType, Lob
from owl.domain.values import FieldType
from owl.infrastructure.context.type_filter import TypeFilterSelector

ALL = Applicability(frozenset(Lob), frozenset(BusinessType))


class CharCounter:
    """One token per character, so budgets in tests are exact."""

    def count(self, text: str) -> int:
        return len(text)


def uid(n: int) -> UUID:
    return UUID(int=n)


def document(n: int, day: int, kind: DocumentType, text: str) -> Document:
    return Document(uid(n), date(2026, 1, day), text, f"doc{n}.txt", kind)


def group(*preferred: tuple[DocumentType, ...]) -> FieldGroup:
    fields = tuple(
        ActiveField(
            f"F{i}",
            FieldDefinition(f"field_{i}", FieldType.STRING, "A field.", ALL, "g", types),
        )
        for i, types in enumerate(preferred, start=1)
    )
    return FieldGroup("g", fields)


SELECTOR = TypeFilterSelector(CharCounter())


def ids(context: ExtractionContext) -> list[int]:
    return [d.labelled.document.document_id.int for d in context.documents]


def warnings(context: ExtractionContext, code: WarningCode) -> list[int]:
    return [w.document_id.int for w in context.warnings if w.code is code and w.document_id]


def test_selector_satisfies_the_port() -> None:
    selector: ContextSelector = SELECTOR
    assert selector is SELECTOR


def test_everything_included_while_budget_remains() -> None:
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.EMAIL, "aaaa"),
            document(2, 2, DocumentType.OTHER, "bbbb"),
            document(3, 3, DocumentType.CONTRACT, "cccc"),
        )
    )
    context = SELECTOR.select(group((DocumentType.CONTRACT,)), bundle, budget=12)
    assert ids(context) == [1, 2, 3]
    assert context.warnings == ()
    assert [d.text for d in context.documents] == ["aaaa", "bbbb", "cccc"]
    assert not any(d.truncated for d in context.documents)


def test_preferred_types_first_newest_first_then_the_rest_presented_chronologically() -> None:
    # Preferred (contract, from any field of the group): 2 (day 2), 4 (day 4) -> 4, 2.
    # Rest: 1 (day 1), 3 (day 3) -> 3, 1. Budget fits three whole documents.
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.EMAIL, "aaaa"),
            document(2, 2, DocumentType.CONTRACT, "bbbb"),
            document(3, 3, DocumentType.OTHER, "cccc"),
            document(4, 4, DocumentType.SLIP, "dddd"),
        )
    )
    preferred = group((DocumentType.CONTRACT,), (DocumentType.SLIP,))
    context = SELECTOR.select(preferred, bundle, budget=12)
    assert ids(context) == [2, 3, 4]  # chronological, not inclusion order
    assert warnings(context, WarningCode.DOCUMENT_SKIPPED) == [1]


def test_short_ids_are_the_bundle_labels() -> None:
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.EMAIL, "aaaa"),
            document(2, 2, DocumentType.CONTRACT, "bbbb"),
            document(3, 3, DocumentType.CONTRACT, "cccc"),
        )
    )
    context = SELECTOR.select(group((DocumentType.CONTRACT,)), bundle, budget=8)
    assert [d.labelled.short_id for d in context.documents] == ["D2", "D3"]


def test_truncation_keeps_the_start_at_a_word_boundary_and_warns() -> None:
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.CONTRACT, "one two three four"),
            document(2, 2, DocumentType.CONTRACT, "xxxx"),
        )
    )
    context = SELECTOR.select(group((DocumentType.CONTRACT,)), bundle, budget=14)
    # Newest first: 2 is included whole (4), 10 remain for 1: "one two th" -> cut to "one two".
    first, second = context.documents
    assert first.labelled.document.document_id == uid(1)
    assert first.truncated
    assert first.text == "one two"
    assert not second.truncated
    assert warnings(context, WarningCode.DOCUMENT_TRUNCATED) == [1]
    warning = context.warnings[0]
    assert warning.group == "g"
    assert warning.message


def test_truncation_without_whitespace_cuts_at_the_budget() -> None:
    bundle = DocumentBundle((document(1, 1, DocumentType.OTHER, "abcdefghij"),))
    context = SELECTOR.select(group(()), bundle, budget=4)
    assert context.documents[0].text == "abcd"


def test_after_a_truncation_every_later_document_is_skipped_even_if_small() -> None:
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.OTHER, "z"),
            document(2, 2, DocumentType.OTHER, "long long long"),
            document(3, 3, DocumentType.OTHER, "aaaa"),
        )
    )
    context = SELECTOR.select(group(()), bundle, budget=9)
    assert ids(context) == [2, 3]
    assert warnings(context, WarningCode.DOCUMENT_TRUNCATED) == [2]
    assert warnings(context, WarningCode.DOCUMENT_SKIPPED) == [1]


def test_a_document_with_no_room_left_is_skipped_not_sent_empty() -> None:
    bundle = DocumentBundle(
        (
            document(1, 1, DocumentType.OTHER, "aaaa"),
            document(2, 2, DocumentType.OTHER, "bbbb"),
        )
    )
    context = SELECTOR.select(group(()), bundle, budget=4)
    assert ids(context) == [2]
    assert warnings(context, WarningCode.DOCUMENT_SKIPPED) == [1]
    assert warnings(context, WarningCode.DOCUMENT_TRUNCATED) == []


def test_zero_budget_skips_everything() -> None:
    bundle = DocumentBundle((document(1, 1, DocumentType.OTHER, "aaaa"),))
    context = SELECTOR.select(group(()), bundle, budget=0)
    assert context.documents == ()
    assert warnings(context, WarningCode.DOCUMENT_SKIPPED) == [1]
    assert context.group.name == "g"


texts = st.text(alphabet="ab ", min_size=1, max_size=30).filter(lambda t: t.strip())


@given(
    st.lists(st.tuples(texts, st.sampled_from(list(DocumentType))), min_size=1, max_size=8),
    st.integers(min_value=0, max_value=120),
)
def test_budget_respected_and_every_document_accounted_for_once(
    docs: list[tuple[str, DocumentType]], budget: int
) -> None:
    bundle = DocumentBundle(
        tuple(document(i, i, kind, text) for i, (text, kind) in enumerate(docs, start=1))
    )
    context = SELECTOR.select(group((DocumentType.CONTRACT,)), bundle, budget=budget)
    assert sum(len(d.text) for d in context.documents) <= budget
    included = ids(context)
    skipped = warnings(context, WarningCode.DOCUMENT_SKIPPED)
    assert sorted(included + skipped) == list(range(1, len(docs) + 1))
    assert included == sorted(included)  # chronological presentation
    assert sum(d.truncated for d in context.documents) <= 1
    for item in context.documents:
        assert item.text
        assert item.labelled.document.text.startswith(item.text)
