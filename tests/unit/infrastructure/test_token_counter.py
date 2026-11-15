import pytest
from hypothesis import given
from hypothesis import strategies as st

from owl.application.ports import TokenCounter
from owl.infrastructure.context.token_counter import CharTokenCounter


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (0, 0),
        (1, 1),  # ceil(1 / 3.5 * 1.15) = ceil(0.33)
        (7, 3),  # ceil(2 * 1.15) = ceil(2.3)
        (35, 12),  # ceil(10 * 1.15) = ceil(11.5)
        (3500, 1150),
    ],
)
def test_chars_divided_by_3_5_plus_15_percent(length: int, expected: int) -> None:
    assert CharTokenCounter().count("x" * length) == expected


def test_satisfies_the_port() -> None:
    counter: TokenCounter = CharTokenCounter()
    assert counter.count("") == 0


@given(st.text(), st.text())
def test_monotonic_in_length(a: str, b: str) -> None:
    counter = CharTokenCounter()
    assert counter.count(a) <= counter.count(a + b)
