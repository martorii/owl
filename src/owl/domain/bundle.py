"""The document bundle submitted in one request (CONTRACT §5.1, §5.2)."""

import re
from dataclasses import dataclass, field

from owl.domain.taxonomy import DocumentType

_SHORT_ID = re.compile(r"D([1-9]\d*)")


class BundleError(ValueError):
    """An invalid bundle. ``problems`` lists every issue found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid document bundle:\n- " + "\n- ".join(self.problems))


@dataclass(frozen=True)
class Document:
    document_id: str
    filename: str
    document_type: DocumentType
    text: str


@dataclass(frozen=True)
class DocumentInput:
    """A document as submitted; ``document_id`` is optional and defaults to ``doc-<index>``."""

    text: str
    filename: str = ""
    document_type: DocumentType = DocumentType.OTHER
    document_id: str | None = field(default=None)


@dataclass(frozen=True)
class LabelledDocument:
    """A document with its per-request short ID (``D1``, ``D2``, ...); never leaves the pipeline."""

    short_id: str
    document: Document


@dataclass(frozen=True)
class DocumentBundle:
    documents: tuple[Document, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not self.documents:
            problems.append("a bundle needs at least one document")
        seen: set[str] = set()
        duplicated: set[str] = set()
        for index, document in enumerate(self.documents):
            if not document.document_id.strip():
                problems.append(f"documents[{index}]: document_id must not be blank")
            if not document.text.strip():
                problems.append(f"documents[{index}]: text must not be blank")
            if document.document_id in seen and document.document_id not in duplicated:
                duplicated.add(document.document_id)
                problems.append(f"duplicate document_id {document.document_id!r}")
            seen.add(document.document_id)
        if problems:
            raise BundleError(problems)

    @classmethod
    def from_inputs(cls, inputs: list[DocumentInput]) -> "DocumentBundle":
        """Build a bundle, assigning ``doc-<index>`` (0-based input order) where no ID was given."""
        return cls(
            tuple(
                Document(
                    document_id=item.document_id if item.document_id is not None else f"doc-{i}",
                    filename=item.filename,
                    document_type=item.document_type,
                    text=item.text,
                )
                for i, item in enumerate(inputs)
            )
        )

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

    def short_id_of(self, document_id: str) -> str | None:
        return next(
            (x.short_id for x in self.labelled if x.document.document_id == document_id), None
        )

    def get(self, document_id: str) -> Document | None:
        return next((d for d in self.documents if d.document_id == document_id), None)
