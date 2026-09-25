"""Shared value types and state constants."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, NamedTuple

TERMINAL_STATES: frozenset[str] = frozenset({"success", "failure", "error"})
FAILURE_STATES: frozenset[str] = frozenset({"failure", "error"})
MISSING: str = "missing"


class Check(NamedTuple):
    """A check's API channel and display name."""

    channel: Literal["status", "check run"]
    name: str


def format_check(check: Check | str) -> str:
    """Render a channel-qualified check, tolerating legacy string keys."""
    if isinstance(check, Check):
        return f"{check.channel}: {check.name}"
    return check


@dataclass(frozen=True)
class Status:
    """A single GitHub commit status snapshot for one context."""

    state: str


@dataclass(frozen=True)
class Transition:
    """A check that just reached a terminal state."""

    name: Check | str
    state: str


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
