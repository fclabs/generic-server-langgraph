"""Runnable cooperative cancellation (BR-108).

Uses ``httpx.AsyncClient`` + ``ASGITransport`` and ``asyncio.wait_for`` to cancel the client
wait mid-request while the stub ``ainvoke`` sleeps. If this proves flaky in CI, prefer raising
the timeout or ``wait_for`` deadline slightly; a minimal alternative is cancelling the task that
runs the runnable coroutine directly (same cancellation semantics, no HTTP stack).
"""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest
from langchain_core.runnables import Runnable

from langgraph_runnable_server import create_runnable_app


class _SlowRunnable:
    """Sleeps in ``ainvoke`` until cancelled; records whether ``CancelledError`` was observed."""

    def __init__(self) -> None:
        self.cancelled_seen = False

    async def ainvoke(self, input, config=None):
        try:
            await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            self.cancelled_seen = True
            raise

    async def abatch(self, inputs, config=None):
        return [{"echo": i} for i in inputs]


def test_invoke_cancelled_error_propagates_from_stub() -> None:
    """Given sleeping ainvoke, when client wait is cancelled, then stub observes CancelledError."""

    async def _run() -> None:
        # Given
        stub = _SlowRunnable()
        app = create_runnable_app(
            prefix="/agents",
            runnables=cast(dict[str, Runnable], {"agent1": stub}),
        )
        transport = httpx.ASGITransport(app=app)
        # When
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    client.post("/agents/agent1/invoke", json={"input": {}}),
                    timeout=0.25,
                )
        # Then
        assert stub.cancelled_seen is True

    asyncio.run(_run())
