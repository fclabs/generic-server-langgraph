"""Minimal LangChain Runnable stand-in for unit tests (no HTTP, no real model)."""

from __future__ import annotations


class StubRunnable:
    async def ainvoke(self, input, config=None):
        return {"echo": input}

    async def abatch(self, inputs, config=None):
        return [{"echo": i} for i in inputs]
