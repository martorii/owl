"""Waiting out rate limits and transient errors, and pacing tokens per minute (CONTRACT §9.5).

``RateLimiter.run`` wraps a single model request. Its waits are separate from the ``ModelRetry``
budget of §8.4, so a 429 never costs a validation attempt. ``RateLimitedModel`` plugs it into a
pydantic_ai ``Model`` so that every request of an agent run goes through it.
"""

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelRateLimitError,
    ModelServerError,
    ModelUnavailableError,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings as GenerationSettings

from owl.application.ports import TokenCounter

_WINDOW_SECONDS = 60.0


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class RateLimitConfig:
    max_wait: float  # total seconds one call may spend waiting out errors (RATE_LIMIT_MAX_WAIT)
    tpm_budget: int | None = None  # this process's share of TPM_LIMIT; None = no pacing
    base_delay: float = 1.0  # first backoff, seconds
    max_delay: float = 60.0  # backoff cap, seconds

    @classmethod
    def for_process(
        cls, *, tpm_limit: int | None, max_concurrent_runs: int, max_wait: float
    ) -> "RateLimitConfig":
        """``TPM_LIMIT ÷ OWL_JOBS__MAX_CONCURRENT_RUNS``: runs share the quota statically."""
        budget = None if tpm_limit is None else max(1, tpm_limit // max_concurrent_runs)
        return cls(max_wait=max_wait, tpm_budget=budget)


def _retry_after(error: ModelAPIError) -> float | None:
    value = getattr(error, "retry_after", None)
    return float(value) if value is not None else None


def is_transient(error: Exception) -> bool:
    """429, 5xx, timeouts and dropped connections; every other error is final."""
    if isinstance(
        error,
        ModelRateLimitError | ModelServerError | ModelUnavailableError | ModelConnectionError,
    ):
        return True
    return isinstance(error, ModelHTTPError) and (
        error.status_code == 429 or error.status_code >= 500
    )


class RateLimiter:
    """Retries transient failures within a waiting budget and paces estimated tokens per minute."""

    def __init__(
        self,
        config: RateLimitConfig,
        *,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._sent: deque[tuple[float, int]] = deque()  # (time, tokens) within the last minute

    async def run[T](self, call: Callable[[], Awaitable[T]], *, estimated_tokens: int = 0) -> T:
        waited = 0.0
        attempt = 0
        while True:
            await self._pace(estimated_tokens)
            try:
                return await call()
            except ModelAPIError as error:
                if not is_transient(error):
                    raise
                delay = self._delay(error, attempt)
                if waited + delay > self._config.max_wait:
                    raise
                await self._clock.sleep(delay)
                waited += delay
                attempt += 1

    def _delay(self, error: ModelAPIError, attempt: int) -> float:
        given = _retry_after(error)
        if given is not None:
            return max(0.0, given)
        ceiling = min(self._config.max_delay, self._config.base_delay * 2**attempt)
        return self._rng.uniform(ceiling / 2, ceiling)  # jitter

    async def _pace(self, tokens: int) -> None:
        budget = self._config.tpm_budget
        if budget is None:
            return
        while True:
            now = self._clock.now()
            while self._sent and self._sent[0][0] <= now - _WINDOW_SECONDS:
                self._sent.popleft()
            used = sum(t for _, t in self._sent)
            if not self._sent or used + tokens <= budget:
                self._sent.append((now, tokens))
                return
            await self._clock.sleep(self._sent[0][0] + _WINDOW_SECONDS - now)


class RateLimitedModel(WrapperModel):
    """Sends every request of the wrapped model through a ``RateLimiter``."""

    def __init__(
        self, wrapped: Model, limiter: RateLimiter, *, counter: TokenCounter, max_tokens: int
    ) -> None:
        super().__init__(wrapped)
        self._limiter = limiter
        self._counter = counter
        self._max_tokens = max_tokens

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: GenerationSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        estimate = self._counter.count(_messages_text(messages)) + self._max_tokens
        return await self._limiter.run(
            lambda: self.wrapped.request(messages, model_settings, model_request_parameters),
            estimated_tokens=estimate,
        )


def _messages_text(messages: list[ModelMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                parts.append(content)
        instructions = getattr(message, "instructions", None)
        if isinstance(instructions, str):
            parts.append(instructions)
    return "\n".join(parts)
