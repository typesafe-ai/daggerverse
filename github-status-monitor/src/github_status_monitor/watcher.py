"""State machine for tracking the status of a known set of GitHub checks.

The Watcher owns three concerns:

- the per-check state derived from successive snapshots
- the deadlines (per-poll wall clock + a separate "discovery" deadline for
  statuses that haven't appeared yet)
- all user-visible output (delegated to :mod:`.render`)

The caller's job is reduced to: fetch a snapshot, feed it to :meth:`step`,
react to the returned :class:`Step` value.
"""

import time
from collections.abc import Callable, Iterable, Mapping

from rich.console import Console

from github_status_monitor import render
from .types import (
    FAILURE_STATES,
    MISSING,
    TERMINAL_STATES,
    Status,
    Step,
    Transition,
    Verdict,
)


def _default_console() -> Console:
    return Console(force_terminal=True)


class Watcher:
    """Tracks check states, deadlines, and rendering across snapshots.

    Tests typically use :meth:`observe` directly and inspect verdict-shape
    properties. End-to-end tests of timeout behavior pass a fake ``clock``.
    """

    def __init__(
        self,
        expected: Iterable[str],
        *,
        repo: str = "",
        ref: str = "",
        fail_fast: bool = False,
        timeout: float = 1800,
        discovery_timeout: float = 300,
        poll_interval: int = 3,
        progress_interval: float = 30,
        console: Console | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.expected: list[str] = sorted(set(expected))
        self.repo: str = repo
        self.ref: str = ref
        self.fail_fast: bool = fail_fast
        self.timeout: float = timeout
        self.discovery_timeout: float = discovery_timeout
        self.poll_interval: int = poll_interval
        self.progress_interval: float = progress_interval
        self.console: Console = console if console is not None else _default_console()
        self.states: dict[str, str] = {name: MISSING for name in self.expected}

        self._clock: Callable[[], float] = clock
        self._start: float | None = None
        self._last_progress: float | None = None

    # ---- pure state ----

    def observe(self, snapshot: Mapping[str, Status]) -> list[Transition]:
        """Apply a snapshot, render any newly-terminal checks, and return them.

        Each terminal transition is reported exactly once. Per-transition
        rendering is *suppressed* when the resulting verdict is terminal —
        the final table about to be rendered will summarize.
        """
        new_completions: list[Transition] = []
        for name in self.expected:
            previous = self.states[name]
            status = snapshot.get(name)
            next_state = MISSING if status is None else status.state
            if next_state != previous:
                self.states[name] = next_state
                if next_state in TERMINAL_STATES and previous not in TERMINAL_STATES:
                    new_completions.append(Transition(name=name, state=next_state))

        if self.verdict is Verdict.WAITING:
            for t in new_completions:
                render.transition(self.console, name=t.name, state=t.state)
        return new_completions

    @property
    def verdict(self) -> Verdict:
        if self.fail_fast and self.failed:
            return Verdict.FAILED
        if self.pending or self.missing:
            return Verdict.WAITING
        if self.failed:
            return Verdict.FAILED
        return Verdict.SUCCEEDED

    @property
    def missing(self) -> list[str]:
        return [c for c in self.expected if self.states[c] == MISSING]

    @property
    def pending(self) -> list[str]:
        return [c for c in self.expected if self.states[c] == "pending"]

    @property
    def failed(self) -> list[str]:
        return [c for c in self.expected if self.states[c] in FAILURE_STATES]

    @property
    def succeeded(self) -> list[str]:
        return [c for c in self.expected if self.states[c] == "success"]

    # ---- orchestration ----

    def step(self, snapshot: Mapping[str, Status]) -> Step:
        """Apply ``snapshot``, advance deadlines, and emit progress output.

        Returns the :class:`Step` outcome — :data:`Step.CONTINUE` means the
        caller should keep polling.
        """
        if self._start is None:
            self._start = self._clock()
            self.print_header()

        self.observe(snapshot)

        verdict = self.verdict
        if verdict is Verdict.SUCCEEDED:
            self.print_final()
            return Step.SUCCEEDED
        if verdict is Verdict.FAILED:
            self.print_final()
            return Step.FAILED

        elapsed = self._clock() - self._start
        if self.missing and elapsed > self.discovery_timeout:
            self.print_discovery_timeout()
            return Step.DISCOVERY_TIMEOUT
        if elapsed > self.timeout:
            self.print_wallclock_timeout()
            return Step.WALLCLOCK_TIMEOUT

        now = self._clock()
        if (
            self._last_progress is None
            or now - self._last_progress >= self.progress_interval
        ):
            self.print_progress()
            self._last_progress = now
        return Step.CONTINUE

    # ---- rendering hooks ----

    def print_header(self) -> None:
        render.header(
            self.console, repo=self.repo, ref=self.ref, expected=self.expected
        )

    def print_progress(self) -> None:
        render.progress(
            self.console,
            succeeded=len(self.succeeded),
            pending=len(self.pending),
            missing=len(self.missing),
            total=len(self.expected),
        )

    def print_final(self) -> None:
        if self.verdict is Verdict.SUCCEEDED:
            render.success(self.console, count=len(self.succeeded))
        elif self.failed:
            render.failure(self.console, failed=self.failed)

    def print_discovery_timeout(self) -> None:
        self._print_table()
        render.discovery_timeout(self.console, ref=self.ref, missing=self.missing)

    def print_wallclock_timeout(self) -> None:
        self._print_table()
        render.wallclock_timeout(
            self.console, pending=self.pending, missing=self.missing
        )

    def _print_table(self) -> None:
        render.final_table(
            self.console,
            expected=self.expected,
            states=self.states,
        )
