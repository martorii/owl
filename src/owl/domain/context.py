"""The documents chosen for one field group's extraction call (CONTRACT §7.1)."""

from dataclasses import dataclass

from owl.domain.bundle import LabelledDocument
from owl.domain.catalog import FieldGroup
from owl.domain.suggestions import PipelineWarning


@dataclass(frozen=True)
class ContextDocument:
    """A bundle document as shown to the model.

    ``labelled`` keeps the bundle-wide short ID, so a selection may have gaps (``D1, D3``).
    ``text`` is the full text, or its start when ``truncated``. Quotes are always validated
    against the full document text, never against ``text``.
    """

    labelled: LabelledDocument
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class ExtractionContext:
    """The selected documents in chronological order, with selection warnings."""

    group: FieldGroup
    documents: tuple[ContextDocument, ...]
    warnings: tuple[PipelineWarning, ...] = ()
