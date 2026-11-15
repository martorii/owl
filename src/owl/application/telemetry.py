"""The default ``Telemetry``: records nothing (CONTRACT §11)."""

from collections.abc import Generator
from contextlib import contextmanager

from owl.application.ports import Attributes, Span


class _NoopSpan:
    def set_attributes(self, attributes: Attributes) -> None:
        del attributes


_SPAN = _NoopSpan()


class NoopTelemetry:
    @contextmanager
    def span(self, name: str, attributes: Attributes | None = None) -> Generator[Span]:
        del name, attributes
        yield _SPAN
