from uuid import UUID, uuid4

import pytest

from owl.domain.bundle import BundleError, Document, DocumentBundle
from owl.domain.taxonomy import DocumentType


def doc(text: str = "text", document_id: UUID | None = None) -> Document:
    return Document(document_id=document_id or uuid4(), text=text, filename="f.pdf")


def test_document_id_is_the_uuid_it_was_given() -> None:
    uid = uuid4()
    bundle = DocumentBundle((doc(document_id=uid),))
    assert bundle.documents[0].document_id == uid
    assert bundle.get(uid) is bundle.documents[0]
    assert bundle.get(uuid4()) is None


def test_document_type_defaults_to_other() -> None:
    assert doc().document_type is DocumentType.OTHER
    typed = Document(uuid4(), "a", "a.pdf", DocumentType.CONTRACT)
    assert typed.document_type is DocumentType.CONTRACT


def test_short_ids_are_prompt_labels_in_input_order() -> None:
    a, b = uuid4(), uuid4()
    bundle = DocumentBundle((doc(document_id=a), doc(document_id=b)))
    assert [(x.short_id, x.document.document_id) for x in bundle.labelled] == [("D1", a), ("D2", b)]
    assert bundle.short_id_of(b) == "D2"
    assert bundle.short_id_of(uuid4()) is None


def test_short_ids_are_not_identifiers() -> None:
    bundle = DocumentBundle((doc(),))
    assert bundle.get("D1") is None  # type: ignore[arg-type]
    assert str(bundle.documents[0].document_id) != bundle.labelled[0].short_id


def test_resolve_short_id() -> None:
    bundle = DocumentBundle((doc(), doc()))
    assert bundle.resolve("D2") is bundle.documents[1]
    for bad in ("D0", "D3", "d1", "F1", "D", "D01", "", "D1 "):
        assert bundle.resolve(bad) is None


def test_rejects_empty_bundle() -> None:
    with pytest.raises(BundleError, match="at least one"):
        DocumentBundle(())


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_rejects_blank_text(text: str) -> None:
    with pytest.raises(BundleError, match=r"documents\[1\]"):
        DocumentBundle((doc(), doc(text)))


def test_rejects_duplicate_uuids() -> None:
    uid = uuid4()
    with pytest.raises(BundleError, match="duplicate"):
        DocumentBundle((doc(document_id=uid), doc(document_id=uid)))


def test_reports_every_problem() -> None:
    uid = uuid4()
    with pytest.raises(BundleError) as exc:
        DocumentBundle((doc("", uid), doc(" ", uid)))
    assert len(exc.value.problems) == 3
