"""The document bundle submitted in one request (CONTRACT §5.1, §5.2)."""

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from owl.domain.taxonomy import DocumentType

_SHORT_ID = re.compile(r"D([1-9]\d*)")


class BundleError(ValueError):
    """An invalid bundle. ``problems`` lists every issue found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid document bundle:\n- " + "\n- ".join(self.problems))


@dataclass(frozen=True)
class Document:
    """One OCR'd text. ``document_id`` is the caller-supplied UUID, the only document identifier.

    ``document_date`` is the date of the document itself (letter or email date, effective date),
    not an upload date; later documents win when documents disagree (CONTRACT §5.2, §7.3).
    """

    document_id: UUID
    document_date: date
    text: str
    filename: str = ""
    document_type: DocumentType = DocumentType.OTHER


@dataclass(frozen=True)
class LabelledDocument:
    """A document with its per-request prompt label (``D1``, ``D2``, ...); never an identifier."""

    short_id: str
    document: Document


@dataclass(frozen=True)
class DocumentBundle:
    """The documents of one request, always in chronological order: oldest first, latest last.

    The sort is stable, so documents with the same date keep the caller's order. Short IDs
    (``D1``, ``D2``, ...) follow this order: a higher number is never an earlier document.
    """

    documents: tuple[Document, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not self.documents:
            problems.append("a bundle needs at least one document")
        seen: set[UUID] = set()
        duplicated: set[UUID] = set()
        for index, document in enumerate(self.documents):
            if not document.text.strip():
                problems.append(f"documents[{index}]: text must not be blank")
            if document.document_id in seen and document.document_id not in duplicated:
                duplicated.add(document.document_id)
                problems.append(f"duplicate document_id {document.document_id}")
            seen.add(document.document_id)
        if problems:
            raise BundleError(problems)
        ordered = tuple(sorted(self.documents, key=lambda d: d.document_date))
        object.__setattr__(self, "documents", ordered)

    @property
    def labelled(self) -> tuple[LabelledDocument, ...]:
        return tuple(LabelledDocument(f"D{i}", d) for i, d in enumerate(self.documents, start=1))

    def resolve(self, short_id: str) -> Document | None:
        """The document for a short ID such as ``D2``; ``None`` if malformed or out of range."""
        match = _SHORT_ID.fullmatch(short_id)
        if match is None:
            return None
        index = int(match[1]) - 1
        return self.documents[index] if index < len(self.documents) else None

    def short_id_of(self, document_id: UUID) -> str | None:
        return next(
            (x.short_id for x in self.labelled if x.document.document_id == document_id), None
        )

    def get(self, document_id: UUID) -> Document | None:
        return next((d for d in self.documents if d.document_id == document_id), None)
