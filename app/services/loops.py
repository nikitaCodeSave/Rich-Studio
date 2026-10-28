"""Background loops of the studio, run by one process at a time, and their orderly stop.

Only one process may send scheduled posts, poll getUpdates and recover posts left "sending" by a crash:
a second one (another uvicorn worker, a new container started before the old one stopped) would mark the
first one's sends "may have gone" and fight it over getUpdates (409). So the loops run under a lease in
SQLite: the holder renews it every RENEW seconds; a process that cannot take it waits as a standby that
still answers HTTP, and takes over once the holder's lease runs out.

On shutdown the scheduler takes no new posts, sends already under way get up to SHUTDOWN_GRACE seconds,
and only then the loops are cancelled.
"""

import asyncio
import contextlib
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from typing import Any

import store

from . import publisher

log = logging.getLogger("rich-studio.loops")
LEASE_KEY = "loops"
LEASE_TTL = 60  # seconds without renewal before another process may take over
RENEW = 20
SHUTDOWN_GRACE = 30  # seconds the sends under way get before the loops are cancelled
_sleep = asyncio.sleep  # tests replace this, not asyncio.sleep itself


@dataclass
class State:
    role: str = "off"  # off | leader | standby
    owner: str = ""  # this process: host:pid:random
    holder: str | None = None  # who holds the lease when this process is a standby
    stopping: bool = False  # the scheduler takes no new posts

    def as_dict(self) -> dict:
        return asdict(self)


STATE = State(owner=f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}")


class Loops:
    """Starts the loops while this process holds the lease; stops them in order."""

    def __init__(self, factories: list[Callable[[], Coroutine[Any, Any, None]]]):
        self.factories = factories
        self.tasks: list[asyncio.Task] = []
        self.keeper: asyncio.Task | None = None

    def start(self) -> None:
        STATE.stopping = False
        self.keeper = asyncio.create_task(self._keep_lease())

    async def _keep_lease(self) -> None:
        failures = 0
        while True:
            try:
                held = store.take_lease(LEASE_KEY, STATE.owner, LEASE_TTL)
                failures = 0
            except Exception:  # a locked or broken database: keep leading for a round, never past the ttl
                log.exception("lease renewal failed")
                failures += 1
                held = STATE.role == "leader" and failures * RENEW < LEASE_TTL - RENEW
            if held and STATE.role != "leader":
                self._lead()
            elif not held and STATE.role != "standby":
                await self._stand_by()
            await _sleep(RENEW)

    def _lead(self) -> None:
        # nobody else runs the loops now: a "sending" row no process of ours is sending was cut off by a crash
        stale = store.recover_stale_sending(keep=publisher.in_flight)
        if stale:
            log.warning("%d posts were being sent when the service stopped; marked failed", stale)
        STATE.role, STATE.holder = "leader", STATE.owner
        self.tasks = [asyncio.create_task(f()) for f in self.factories]
        log.info("background loops started", extra={"event": "loops.lead", "owner": STATE.owner})

    async def _stand_by(self) -> None:
        if self.tasks:  # lost the lease (this process stalled longer than LEASE_TTL): someone else leads now
            log.warning("lease lost: background loops stopped", extra={"event": "loops.lost"})
            await _cancel(self.tasks)
            self.tasks = []
        STATE.role = "standby"
        STATE.holder = store.lease_holder(LEASE_KEY)
        log.warning(
            "another studio process runs the background loops",
            extra={"event": "loops.standby", "holder": STATE.holder},
        )

    async def stop(self) -> None:
        STATE.stopping = True
        deadline = time.monotonic() + SHUTDOWN_GRACE
        while publisher.in_flight and time.monotonic() < deadline:  # noqa: ASYNC110 - a plain set, filled by requests too
            await asyncio.sleep(0.2)
        if publisher.in_flight:
            log.warning("stopping with %d sends under way", len(publisher.in_flight))
        await _cancel([*self.tasks, *([self.keeper] if self.keeper else [])])
        if STATE.role == "leader":
            with contextlib.suppress(Exception):
                store.drop_lease(LEASE_KEY, STATE.owner)
        STATE.role, self.tasks = "off", []


async def _cancel(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
