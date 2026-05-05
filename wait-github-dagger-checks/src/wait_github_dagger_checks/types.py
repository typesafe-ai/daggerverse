"""Shared value types and state constants."""

from dataclasses import dataclass
from enum import Enum


TERMINAL_STATES: frozenset[str] = frozenset({"success", "failure", "error"})
FAILURE_STATES: frozenset[str] = frozenset({"failure", "error"})
MISSING: str = "missing"


@dataclass(frozen=True)
class Status:
    """A single GitHub commit status snapshot for one context."""

    state: str
    trace_url: str = ""


@dataclass(frozen=True)
class Transition:
    """A check that just reached a terminal state."""

    name: str
    state: str
    trace_url: str


class Verdict(Enum):
    """Pure-state verdict, derived only from observed check states."""

    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Step(Enum):
    """Outcome of a single :meth:`Watcher.step` call. The caller maps this to
    return / raise."""

    CONTINUE = "continue"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DISCOVERY_TIMEOUT = "discovery_timeout"
    WALLCLOCK_TIMEOUT = "wallclock_timeout"
