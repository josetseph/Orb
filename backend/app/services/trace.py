"""Per-request pipeline trace for the benchmark harness.

Off unless a request opts in (``ChatInput.trace``). A ContextVar keeps one
request's events apart from another's; ``asyncio.to_thread`` copies the context,
and every copy points at the same list, so worker threads record into it too.
"""

from __future__ import annotations

from contextvars import ContextVar

_events: ContextVar[list | None] = ContextVar("orb_trace", default=None)


def start() -> list:
    events: list = []
    _events.set(events)
    return events


def record(kind: str, **data) -> None:
    events = _events.get()
    if events is not None:
        events.append({"kind": kind, **data})
