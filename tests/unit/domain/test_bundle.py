import pytest

from owl.domain.bundle import BundleError, DocumentBundle, DocumentInput
from owl.domain.taxonomy import DocumentType


def make(*texts: str, ids: tuple[str | None, ...] = ()) -> DocumentBundle:
    inputs = [
        DocumentInput(text=t, filename=f"f{i}.pdf", document_id=ids[i] if i < len(ids) else None)
        for i, t in enumerate(texts)
    ]
    return DocumentBundle.from_inputs(inputs)


def test_default_ids_are_doc_index() -> None:
    bundle = make("a", "b", "c")
    assert [d.document_id for d in bundle.documents] == ["doc-0", "doc-1", "doc-2"]


def test_explicit_ids_are_kept_and_mixed_with_defaults() -> None:
    bundle = make("a", "b", "c", ids=("client-123", None, "x"))
    assert [d.document_id for d in bundle.documents] == ["client-123", "doc-1", "x"]


def test_document_type_defaults_to_other() -> None:
    bundle = make("a")
    assert bundle.documents[0].document_type is DocumentType.OTHER
    typed = DocumentBundle.from_inputs(
        [DocumentInput(text="a", filename="a", document_type=DocumentType.CONTRACT)]
    )
    assert typed.documents[0].document_type is DocumentType.CONTRACT


def test_short_ids_follow_input_order() -> None:
    bundle = make("a", "b", ids=("client-123", None))
    assert [(x.short_id, x.document.document_id) for x in bundle.labelled] == [
        ("D1", "client-123"),
        ("D2", "doc-1"),
    ]
    assert bundle.short_id_of("doc-1") == "D2"
    assert bundle.short_id_of("nope") is None


def test_resolve_short_id() -> None:
    bundle = make("a", "b")
    assert bundle.resolve("D2") is bundle.documents[1]
    for bad in ("D0", "D3", "d1", "F1", "D", "D01", "", "D1 "):
        assert bundle.resolve(bad) is None


def test_get_by_document_id() -> None:
    bundle = make("a", ids=("client-123",))
    assert bundle.get("client-123") is bundle.documents[0]
    assert bundle.get("zzz") is None


def test_rejects_empty_bundle() -> None:
    with pytest.raises(BundleError, match="at least one"):
        DocumentBundle.from_inputs([])


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_rejects_blank_text(text: str) -> None:
    with pytest.raises(BundleError, match=r"documents\[1\]"):
        make("ok", text)


def test_rejects_duplicate_ids_including_collisions_with_defaults() -> None:
    with pytest.raises(BundleError, match="duplicate"):
        make("a", "b", ids=("x", "x"))
    with pytest.raises(BundleError, match="duplicate"):
        make("a", "b", ids=("doc-1", None))  # doc 1 would also get "doc-1"


def test_rejects_blank_explicit_id() -> None:
    with pytest.raises(BundleError, match="document_id"):
        make("a", ids=("  ",))


def test_reports_every_problem() -> None:
    with pytest.raises(BundleError) as exc:
        make("", " ", ids=("x", "x"))
    assert len(exc.value.problems) >= 3
