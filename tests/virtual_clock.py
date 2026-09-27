"""Explicitly advanced task-local clock for provider wait tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TypeVar

from campusctl.wait_clock import use_clock

T = TypeVar("T")


class VirtualClock:
    def __init__(self) -> None:
        self.timestamp = 0.0
        self.events: list[tuple[float, Callable[[], None]]] = []
        self.sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self.timestamp

    def monotonic(self) -> float:
        return self.timestamp

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (self.timestamp + seconds, future)
        self.sleepers.append(entry)
        try:
            await future
        finally:
            self.sleepers.remove(entry)

    def sleep_sync(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.timestamp += seconds
        for deadline, future in tuple(self.sleepers):
            if deadline <= self.timestamp + 1e-9 and not future.done():
                future.set_result(None)
        for deadline, callback in tuple(self.events):
            if deadline <= self.timestamp + 1e-9:
                self.events.remove((deadline, callback))
                callback()

    def call_at(self, deadline: float, callback: Callable[[], None]) -> None:
        self.events.append((deadline, callback))

    async def wait_for(self, awaitable: Awaitable[T], seconds: float) -> T:
        task = asyncio.ensure_future(awaitable)
        if seconds <= 0 and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise TimeoutError
        if task.done():
            return await task
        timer = asyncio.create_task(self.sleep(seconds))
        try:
            done, _ = await asyncio.wait((task, timer), return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return await task
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise TimeoutError
        finally:
            timer.cancel()
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def settle() -> None:
    for _ in range(64):
        await asyncio.sleep(0)


async def drive(awaitable: Awaitable[T], clock: VirtualClock) -> T:
    """Advance to the next scheduled wait until the operation finishes."""
    with use_clock(clock):
        task = asyncio.create_task(awaitable)
        for _ in range(10000):
            await settle()
            if task.done():
                return await task
            deadlines = [deadline for deadline, future in clock.sleepers if not future.done()]
            deadlines.extend(deadline for deadline, _ in clock.events)
            if not deadlines:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise AssertionError("Provider did not schedule a clock wait")
            clock.advance(max(0, min(deadlines) - clock.now()))
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise AssertionError("Provider exceeded virtual wait budget")
