import random

import pytest
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
)

from owl.infrastructure.llm.rate_limit import RateLimitConfig, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.time

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


def limiter(clock: FakeClock, **overrides: object) -> RateLimiter:
    config = RateLimitConfig(**({"max_wait": 600.0} | overrides))  # type: ignore[arg-type]
    return RateLimiter(config, clock=clock, rng=random.Random(0))


class Flaky:
    """Raises the given errors in turn, then returns ``"ok"``."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def rate_limited(retry_after: float | None = None) -> ModelRateLimitError:
    return ModelRateLimitError("m", "429", retry_after=retry_after)


async def test_success_needs_no_wait() -> None:
    clock = FakeClock()
    assert await limiter(clock).run(Flaky()) == "ok"
    assert clock.sleeps == []


async def test_429_is_waited_out_and_retried() -> None:
    clock, call = FakeClock(), Flaky(rate_limited(), rate_limited())
    assert await limiter(clock).run(call) == "ok"
    assert call.calls == 3
    assert len(clock.sleeps) == 2
    assert all(s > 0 for s in clock.sleeps)


async def test_retry_after_is_honoured() -> None:
    clock, call = FakeClock(), Flaky(rate_limited(retry_after=7.0))
    assert await limiter(clock).run(call) == "ok"
    assert clock.sleeps == [7.0]


async def test_backoff_grows_with_jitter_and_is_capped() -> None:
    clock = FakeClock()
    call = Flaky(*[rate_limited() for _ in range(8)])
    await limiter(clock, base_delay=1.0, max_delay=10.0).run(call)
    assert all(0 < s <= 10.0 for s in clock.sleeps)
    assert clock.sleeps[3] > clock.sleeps[0]


@pytest.mark.parametrize(
    "error",
    [
        ModelServerError("m", "500"),
        ModelHTTPError(503, "m"),
        ModelHTTPError(429, "m"),
        ModelConnectionError("m", "dropped"),
        ModelTimeoutError("m", "timeout"),
    ],
)
async def test_transient_errors_are_retried(error: ModelAPIError) -> None:
    call = Flaky(error)
    assert await limiter(FakeClock()).run(call) == "ok"
    assert call.calls == 2


@pytest.mark.parametrize("error", [ModelHTTPError(400, "m"), ModelHTTPError(401, "m")])
async def test_other_4xx_is_not_retried(error: ModelHTTPError) -> None:
    clock, call = FakeClock(), Flaky(error)
    with pytest.raises(ModelHTTPError):
        await limiter(clock).run(call)
    assert call.calls == 1
    assert clock.sleeps == []


async def test_non_model_errors_are_not_retried() -> None:
    call = Flaky(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await limiter(FakeClock()).run(call)
    assert call.calls == 1


async def test_waiting_budget_exhausted_raises_the_last_error() -> None:
    clock = FakeClock()
    call = Flaky(*[rate_limited(retry_after=40.0) for _ in range(10)])
    with pytest.raises(ModelRateLimitError):
        await limiter(clock, max_wait=100.0).run(call)
    assert sum(clock.sleeps) <= 100.0
    assert call.calls == 3  # 40 + 40 waited; a third wait of 40 would exceed 100


async def test_retry_after_beyond_the_budget_fails_without_waiting() -> None:
    clock, call = FakeClock(), Flaky(rate_limited(retry_after=500.0))
    with pytest.raises(ModelRateLimitError):
        await limiter(clock, max_wait=100.0).run(call)
    assert clock.sleeps == []


async def test_budget_is_per_call() -> None:
    clock = FakeClock()
    rl = limiter(clock, max_wait=100.0)
    for _ in range(3):
        await rl.run(Flaky(rate_limited(retry_after=60.0)))
    assert clock.sleeps == [60.0, 60.0, 60.0]


async def test_token_pacing_never_exceeds_the_budget_in_any_minute() -> None:
    clock = FakeClock()
    rl = limiter(clock, tpm_budget=1000)
    sent: list[tuple[float, int]] = []

    async def call() -> str:
        return "ok"

    for _ in range(12):
        await rl.run(call, estimated_tokens=400)
        sent.append((clock.now(), 400))
    for time, _ in sent:
        in_window = sum(t for at, t in sent if time - 60 < at <= time)
        assert in_window <= 1000


async def test_no_pacing_when_unlimited() -> None:
    clock = FakeClock()
    rl = limiter(clock)
    for _ in range(5):
        await rl.run(Flaky(), estimated_tokens=10**9)
    assert clock.sleeps == []


async def test_request_larger_than_the_budget_still_goes_through_alone() -> None:
    clock = FakeClock()
    rl = limiter(clock, tpm_budget=100)
    assert await rl.run(Flaky(), estimated_tokens=500) == "ok"
    assert clock.sleeps == []


def test_config_derives_the_process_share() -> None:
    assert (
        RateLimitConfig.for_process(tpm_limit=90000, max_concurrent_runs=3, max_wait=10).tpm_budget
        == 30000
    )
    assert (
        RateLimitConfig.for_process(tpm_limit=None, max_concurrent_runs=3, max_wait=10).tpm_budget
        is None
    )
