"""Task-local clock for bounded waits and retry deadlines."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...

    async def wait_for(self, awaitable: Awaitable[T], seconds: float) -> T: ...

    def monotonic(self) -> float: ...

    def sleep_sync(self, seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class _RealClock:
    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_for(self, awaitable: Awaitable[T], seconds: float) -> T:
        return await asyncio.wait_for(awaitable, timeout=seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep_sync(self, seconds: float) -> None:
        time.sleep(seconds)


_REAL_CLOCK = _RealClock()
_clock: ContextVar[Clock] = ContextVar("campusctl_wait_clock", default=_REAL_CLOCK)


def current_clock() -> Clock:
    return _clock.get()


@contextmanager
def use_clock(clock: Clock) -> Iterator[None]:
    token = _clock.set(clock)
    try:
        yield
    finally:
        _clock.reset(token)
