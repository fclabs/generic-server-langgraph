"""Shared stub Runnables for runnable HTTP interface tests."""

from __future__ import annotations


class StubRunnable:
    def __init__(self, response=None, raise_on_invoke=None, raise_on_batch=None):
        self.ainvoke_calls: list[tuple[object, object]] = []
        self.abatch_calls: list[tuple[object, object]] = []
        self._response = response
        self._raise_on_invoke = raise_on_invoke
        self._raise_on_batch = raise_on_batch

    async def ainvoke(self, input, config=None):
        self.ainvoke_calls.append((input, config))
        if self._raise_on_invoke is not None:
            raise self._raise_on_invoke
        return self._response if self._response is not None else {"echo": input}

    async def abatch(self, inputs, config=None):
        self.abatch_calls.append((inputs, config))
        if self._raise_on_batch is not None:
            raise self._raise_on_batch
        return [{"echo": i} for i in inputs]
