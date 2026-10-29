"""The real-time engine: feed → processor → subscribers.

The engine runs two small loops on one asyncio event loop:

* the **ingest loop** pulls ticks from the feed and hands each to the processor;
* the **publish loop** takes a snapshot a few times a second and offers it to
  every subscriber (for example, each open dashboard).

Back-pressure is handled the simple, safe way: each subscriber has a queue of
size one, and a newer snapshot replaces an unread older one. A slow browser
therefore always sees the latest state and can never slow down ingestion.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Dict, List, Optional

from ..workspace import Workspace
from .feed import SimulatedFeed
from .processor import AlertConfig, StreamProcessor


class RealTimeEngine:
    def __init__(self, ws: Workspace, feed=None, config: Optional[AlertConfig] = None,
                 snapshot_interval: float = 0.25) -> None:
        self.ws = ws
        self.feed = feed or SimulatedFeed(ws)
        self.processor = StreamProcessor(ws, config)
        self.snapshot_interval = snapshot_interval
        self._subs: List[asyncio.Queue] = []
        self._tasks: List[asyncio.Task] = []
        self.last_snapshot: Optional[Dict] = None
        self.error: Optional[str] = None

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    async def start(self) -> None:
        if self.running:
            return
        self._tasks = [asyncio.create_task(self._ingest()), asyncio.create_task(self._publish())]

    async def stop(self) -> None:
        if hasattr(self.feed, "stop"):
            self.feed.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    async def _ingest(self) -> None:
        process = self.processor.process
        try:
            async for tick in self.feed.stream():
                process(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # e.g. a rejected API key: report it on the dashboard rather than vanish
            self.error = str(exc)
            self.processor._alert(time.time(), "feed", "critical", "Data feed",
                                  f"The price feed stopped: {exc}")

    async def _publish(self) -> None:
        while True:
            await asyncio.sleep(self.snapshot_interval)
            snap = self.processor.snapshot()
            snap["source"] = self.feed.describe() if hasattr(self.feed, "describe") else {"provider": "custom"}
            self.last_snapshot = snap
            for q in list(self._subs):
                if q.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        q.get_nowait()
                q.put_nowait(snap)
