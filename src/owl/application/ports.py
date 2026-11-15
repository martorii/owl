"""Ports the pipeline depends on; adapters live in infrastructure (CONTRACT §7, §10, §11).

The job ports (``JobStore``, ``JobLauncher``, ``CallbackSender``) are added with the job core.
"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from owl.domain.bundle import DocumentBundle
from owl.domain.catalog import FieldGroup
from owl.domain.context import ExtractionContext
from owl.domain.suggestions import Candidate, PipelineWarning

type AttributeValue = str | int | float | bool
type Attributes = Mapping[str, AttributeValue]


class TokenCounter(Protocol):
    """Estimates how many tokens a text costs the model."""

    def count(self, text: str) -> int: ...


class ContextSelector(Protocol):
    """Chooses which documents (and how much of each) one group's extraction sees."""

    def select(self, group: FieldGroup, bundle: DocumentBundle, budget: int) -> ExtractionContext:
        """``budget`` is the number of tokens available for document text."""
        ...


@dataclass(frozen=True)
class ExtractionOutcome:
    """The validated candidates of one group, and the warnings raised while extracting."""

    candidates: tuple[Candidate, ...]
    warnings: tuple[PipelineWarning, ...] = ()


class GroupExtractionError(Exception):
    """Nothing valid remained for a group; the pipeline reports it as ``group_failed``."""

    def __init__(self, group: str, reason: str) -> None:
        self.group = group
        self.reason = reason
        super().__init__(f"extraction failed for group {group!r}: {reason}")


class FieldExtractor(Protocol):
    """Extracts candidates for one field group from its selected context.

    The full ``bundle`` is passed so that quotes are validated against whole documents.
    Raises ``GroupExtractionError`` when nothing valid remains.
    """

    async def extract(
        self, group: FieldGroup, context: ExtractionContext, bundle: DocumentBundle
    ) -> ExtractionOutcome: ...


class Span(Protocol):
    def set_attributes(self, attributes: Attributes) -> None: ...


class Telemetry(Protocol):
    """Tracing seam: the application emits spans without knowing the backend."""

    def span(
        self, name: str, attributes: Attributes | None = None
    ) -> AbstractContextManager[Span]: ...
