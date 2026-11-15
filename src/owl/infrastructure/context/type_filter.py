"""``TypeFilterSelector``: preferred document types first, whole documents until the budget is
spent (CONTRACT §7.1)."""

from owl.application.ports import TokenCounter
from owl.domain.bundle import DocumentBundle, LabelledDocument
from owl.domain.catalog import FieldGroup
from owl.domain.context import ContextDocument, ExtractionContext
from owl.domain.suggestions import PipelineWarning, WarningCode
from owl.domain.taxonomy import DocumentType


class TypeFilterSelector:
    """Inclusion priority: preferred types of any field of the group, then the rest; newest
    first within each tier. Strict order: the first document that does not fit is cut to the
    remaining budget, and every document after it is skipped. Included documents are presented
    in chronological (bundle) order."""

    def __init__(self, counter: TokenCounter) -> None:
        self._counter = counter

    def select(self, group: FieldGroup, bundle: DocumentBundle, budget: int) -> ExtractionContext:
        preferred: set[DocumentType] = {
            kind for item in group.fields for kind in item.definition.preferred_document_types
        }
        labelled = bundle.labelled
        position = {item.short_id: index for index, item in enumerate(labelled)}
        priority = sorted(
            labelled,
            key=lambda x: (x.document.document_type not in preferred, -position[x.short_id]),
        )

        chosen: dict[str, ContextDocument] = {}
        warnings: list[PipelineWarning] = []
        remaining = max(budget, 0)
        exhausted = False
        for item in priority:
            if not exhausted:
                cost = self._counter.count(item.document.text)
                if cost <= remaining:
                    chosen[item.short_id] = ContextDocument(item, item.document.text)
                    remaining -= cost
                    continue
                exhausted = True
                text = self._fitting_start(item.document.text, remaining)
                if text:
                    chosen[item.short_id] = ContextDocument(item, text, truncated=True)
                    warnings.append(_warning(WarningCode.DOCUMENT_TRUNCATED, item, group, text))
                    continue
            warnings.append(_warning(WarningCode.DOCUMENT_SKIPPED, item, group))

        documents = tuple(chosen[x.short_id] for x in labelled if x.short_id in chosen)
        return ExtractionContext(group, documents, tuple(warnings))

    def _fitting_start(self, text: str, budget: int) -> str:
        """The longest start of ``text`` within ``budget``, cut back to a word boundary when the
        cut falls inside a word (unless the start is a single word); trailing space removed."""
        low, high = 0, len(text)
        while low < high:  # largest n with count(text[:n]) <= budget
            middle = (low + high + 1) // 2
            if self._counter.count(text[:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        start = text[:low]
        if 0 < low < len(text) and not text[low].isspace() and not start[-1].isspace():
            boundary = max(start.rfind(c) for c in (" ", "\n", "\t", "\r"))
            if boundary > 0:
                start = start[:boundary]
        return start.rstrip()


def _warning(
    code: WarningCode, item: LabelledDocument, group: FieldGroup, kept: str | None = None
) -> PipelineWarning:
    name = item.document.filename or str(item.document.document_id)
    if kept is None:
        message = f"document {name} left out of group {group.name!r}: context budget exhausted"
    else:
        message = (
            f"document {name} truncated for group {group.name!r}: "
            f"{len(kept)} of {len(item.document.text)} characters kept"
        )
    return PipelineWarning(code, message, group=group.name, document_id=item.document.document_id)
