import pytest

from owl.application.ports import Telemetry
from owl.application.telemetry import NoopTelemetry


def test_noop_span_is_usable_and_nests() -> None:
    telemetry: Telemetry = NoopTelemetry()
    with telemetry.span("pipeline.extract", {"group": "g"}) as outer:
        outer.set_attributes({"retries": 1})
        with telemetry.span("pipeline.validate") as inner:
            inner.set_attributes({})


def test_noop_span_does_not_swallow_exceptions() -> None:
    with pytest.raises(RuntimeError), NoopTelemetry().span("x"):
        raise RuntimeError("boom")
