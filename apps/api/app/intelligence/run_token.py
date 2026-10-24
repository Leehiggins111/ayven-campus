"""A run token cancels late writes after a stage timeout.

Python cannot stop a thread that is already running. The worker keeps the
token that was current when it started. Timing the stage out cancels that
token, and later inserts, updates, and deletes from that worker are refused.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token


class StaleWrite(RuntimeError):
    """A timed-out worker tried to persist a result."""


class RunToken:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def alive(self) -> bool:
        with self._lock:
            return not self._cancelled


_current: ContextVar[RunToken | None] = ContextVar("ayven_run_token", default=None)


def bind(token: RunToken) -> Token:
    return _current.set(token)


def reset(token: Token) -> None:
    _current.reset(token)


def write_allowed() -> bool:
    token = _current.get()
    return token is None or token.alive


def reject_if_stale() -> None:
    if not write_allowed():
        raise StaleWrite("late write rejected")
