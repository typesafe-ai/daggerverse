"""State-machine tests for `Watcher`. Snapshots are synthesized — no GitHub
calls. Output is captured via a recording `rich.console.Console`."""

from collections.abc import Iterable

import pytest
from rich.console import Console

from github_status_monitor.types import MISSING, Status, Step, Verdict
from github_status_monitor.watcher import Watcher


def make_console() -> Console:
    """A Console suitable for test inspection: no ANSI, captures all output."""
    return Console(record=True, width=200, force_terminal=False, force_interactive=False)


def make_watcher(expected: Iterable[str], **kwargs) -> tuple[Watcher, Console]:
    c = make_console()
    return Watcher(expected, console=c, **kwargs), c


# ---- initial state ----


def test_initial_verdict_is_waiting():
    w, _ = make_watcher(["a", "b"])
    assert w.verdict is Verdict.WAITING
    assert w.missing == ["a", "b"]
    assert w.pending == []
    assert w.succeeded == []
    assert w.failed == []
    assert w.states == {"a": MISSING, "b": MISSING}


def test_expected_is_deduplicated_and_sorted():
    w, _ = make_watcher(["b", "a", "b"])
    assert w.expected == ["a", "b"]


# ---- observe / verdict basic transitions ----


def test_all_success_yields_succeeded_verdict():
    w, _ = make_watcher(["a", "b"])
    transitions = w.observe({"a": Status("success"), "b": Status("success")})
    assert sorted(t.name for t in transitions) == ["a", "b"]
    assert w.verdict is Verdict.SUCCEEDED
    assert w.succeeded == ["a", "b"]


def test_pending_keeps_waiting():
    w, _ = make_watcher(["a"])
    transitions = w.observe({"a": Status("pending")})
    assert transitions == []
    assert w.verdict is Verdict.WAITING
    assert w.pending == ["a"]


def test_partial_completion_keeps_waiting():
    w, _ = make_watcher(["a", "b"])
    transitions = w.observe({"a": Status("success"), "b": Status("pending")})
    assert [t.name for t in transitions] == ["a"]
    assert w.verdict is Verdict.WAITING


def test_error_state_treated_as_failure():
    w, _ = make_watcher(["a"])
    w.observe({"a": Status("error")})
    assert w.failed == ["a"]
    assert w.verdict is Verdict.FAILED


def test_extra_contexts_in_snapshot_are_ignored():
    w, _ = make_watcher(["a"])
    w.observe({"a": Status("success"), "unrelated": Status("failure")})
    assert w.verdict is Verdict.SUCCEEDED
    assert "unrelated" not in w.states


# ---- fail_fast vs default ----


def test_default_does_not_fail_until_all_terminal():
    w, _ = make_watcher(["a", "b"])
    w.observe({"a": Status("failure"), "b": Status("pending")})
    assert w.verdict is Verdict.WAITING
    w.observe({"a": Status("failure"), "b": Status("success")})
    assert w.verdict is Verdict.FAILED
    assert w.failed == ["a"]
    assert w.succeeded == ["b"]


def test_fail_fast_returns_failed_immediately():
    w, _ = make_watcher(["a", "b"], fail_fast=True)
    w.observe({"a": Status("failure"), "b": Status("pending")})
    assert w.verdict is Verdict.FAILED


# ---- transition emission ----


def test_each_terminal_transition_emitted_exactly_once():
    w, _ = make_watcher(["a"])
    first = w.observe({"a": Status("success")})
    second = w.observe({"a": Status("success")})
    third = w.observe({"a": Status("success")})
    assert [t.name for t in first] == ["a"]
    assert second == []
    assert third == []


def test_non_terminal_changes_do_not_emit():
    w, _ = make_watcher(["a"])
    transitions = w.observe({"a": Status("pending")})
    assert transitions == []


def test_pending_then_terminal_emits_once():
    w, _ = make_watcher(["a"])
    w.observe({"a": Status("pending")})
    transitions = w.observe({"a": Status("success")})
    assert [t.state for t in transitions] == ["success"]


def test_disappearing_status_reverts_to_missing():
    w, _ = make_watcher(["a"])
    w.observe({"a": Status("pending")})
    w.observe({})
    assert w.states["a"] == MISSING
    assert w.missing == ["a"]


# ---- realistic sequences ----


def test_realistic_success_sequence():
    w, _ = make_watcher(["a", "b", "c"])
    sequence = [
        {},
        {"a": Status("pending")},
        {"a": Status("pending"), "b": Status("pending")},
        {"a": Status("success"), "b": Status("pending"), "c": Status("pending")},
        {"a": Status("success"), "b": Status("success"), "c": Status("pending")},
        {"a": Status("success"), "b": Status("success"), "c": Status("success")},
    ]
    completed: list[str] = []
    for snapshot in sequence:
        completed.extend(t.name for t in w.observe(snapshot))
    assert completed == ["a", "b", "c"]
    assert w.verdict is Verdict.SUCCEEDED


def test_realistic_mixed_outcome_sequence_without_fail_fast():
    w, _ = make_watcher(["a", "b", "c"])
    sequence = [
        {"a": Status("pending"), "b": Status("pending"), "c": Status("pending")},
        {"a": Status("failure"), "b": Status("pending"), "c": Status("pending")},
        {"a": Status("failure"), "b": Status("success"), "c": Status("pending")},
        {"a": Status("failure"), "b": Status("success"), "c": Status("error")},
    ]
    verdicts: list[Verdict] = []
    for snapshot in sequence:
        w.observe(snapshot)
        verdicts.append(w.verdict)
    assert verdicts == [
        Verdict.WAITING,
        Verdict.WAITING,
        Verdict.WAITING,
        Verdict.FAILED,
    ]
    assert w.failed == ["a", "c"]


# ---- rendering output ----


def test_partial_completion_prints_per_transition():
    w, c = make_watcher(["a", "b"])
    w.observe({"a": Status("success"), "b": Status("pending")})
    out = c.export_text()
    assert "a" in out
    assert "success" in out


def test_observe_does_not_reprint_same_state():
    w, c = make_watcher(["a", "b"])
    w.observe({"a": Status("success"), "b": Status("pending")})
    first_lines = c.export_text(clear=False).count("\n")
    w.observe({"a": Status("success"), "b": Status("pending")})
    w.observe({"a": Status("success"), "b": Status("pending")})
    second_lines = c.export_text(clear=False).count("\n")
    assert first_lines == second_lines


def test_pending_does_not_print():
    w, c = make_watcher(["a"])
    w.observe({"a": Status("pending")})
    assert c.export_text().strip() == ""


def test_short_circuits_when_verdict_becomes_terminal():
    """If a single observe() makes the verdict terminal, don't print live
    transitions — the caller's final table will summarize."""
    w, c = make_watcher(["a", "b"])
    w.observe({"a": Status("success"), "b": Status("success")})
    assert c.export_text().strip() == ""
    assert w.verdict is Verdict.SUCCEEDED


def test_short_circuits_on_closing_transitions_only():
    """Live transitions are rendered while still WAITING; the final batch that
    flips the verdict to terminal is suppressed (final table covers it)."""
    w, c = make_watcher(["a", "b", "c"])
    # First batch: 'a' completes, 'b'/'c' still pending → live print.
    w.observe({"a": Status("success"), "b": Status("pending"), "c": Status("pending")})
    after_first = c.export_text(clear=False)
    assert "a" in after_first

    # Second batch: 'b' and 'c' both complete in one shot → verdict terminal,
    # no further live prints.
    w.observe({"a": Status("success"), "b": Status("success"), "c": Status("success")})
    after_second = c.export_text(clear=False)
    assert after_second == after_first
    assert w.verdict is Verdict.SUCCEEDED


def test_short_circuits_when_failed_with_fail_fast():
    w, c = make_watcher(["a", "b"], fail_fast=True)
    w.observe({"a": Status("failure"), "b": Status("pending")})
    assert c.export_text().strip() == ""
    assert w.verdict is Verdict.FAILED


def test_print_final_emits_failure_summary_with_failed_checks():
    w, c = make_watcher(["a", "b"])
    w.observe({"a": Status("success"), "b": Status("failure")})
    w.print_final()
    out = c.export_text()
    assert "checks failed" in out
    assert "b" in out


def test_print_header_mentions_repo_and_ref():
    c = make_console()
    w = Watcher(["a"], repo="org/proj", ref="deadbeef", console=c)
    w.print_header()
    out = c.export_text()
    assert "org/proj" in out
    assert "deadbeef" in out


@pytest.mark.parametrize(
    "states, expected_verdict",
    [
        ({"a": "success", "b": "success"}, Verdict.SUCCEEDED),
        ({"a": "success", "b": "pending"}, Verdict.WAITING),
        ({"a": "failure", "b": "success"}, Verdict.FAILED),
        ({"a": "error", "b": "success"}, Verdict.FAILED),
        ({"a": "pending", "b": "pending"}, Verdict.WAITING),
    ],
)
def test_verdict_matrix(states: dict[str, str], expected_verdict: Verdict):
    w, _ = make_watcher(states.keys())
    w.observe({k: Status(v) for k, v in states.items()})
    assert w.verdict is expected_verdict


# ---- step() orchestration with a fake clock ----


class FakeClock:
    """Manually-advanceable monotonic clock for deterministic timeout tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_step_watcher(expected: Iterable[str], **kwargs) -> tuple[Watcher, Console, FakeClock]:
    c = make_console()
    clock = FakeClock()
    w = Watcher(expected, console=c, clock=clock, **kwargs)
    return w, c, clock


def test_step_returns_continue_while_pending():
    w, _, _ = make_step_watcher(["a"], timeout=100, discovery_timeout=100)
    assert w.step({"a": Status("pending")}) is Step.CONTINUE


def test_step_returns_succeeded_and_renders_final_summary():
    w, c, _ = make_step_watcher(["a"], timeout=100, discovery_timeout=100)
    assert w.step({"a": Status("success")}) is Step.SUCCEEDED
    out = c.export_text(clear=False)
    assert "succeeded" in out


def test_step_returns_failed_when_terminal_with_failure():
    w, _, _ = make_step_watcher(["a", "b"], timeout=100, discovery_timeout=100, fail_fast=True)
    assert w.step({"a": Status("failure"), "b": Status("pending")}) is Step.FAILED


def test_step_discovery_timeout_when_status_never_appears():
    w, _, clock = make_step_watcher(["a"], timeout=1000, discovery_timeout=10)
    assert w.step({}) is Step.CONTINUE
    clock.t = 11
    assert w.step({}) is Step.DISCOVERY_TIMEOUT


def test_step_wallclock_timeout_when_pending_never_lands():
    w, _, clock = make_step_watcher(["a"], timeout=10, discovery_timeout=1000)
    assert w.step({"a": Status("pending")}) is Step.CONTINUE
    clock.t = 11
    assert w.step({"a": Status("pending")}) is Step.WALLCLOCK_TIMEOUT


def test_step_discovery_does_not_fire_once_status_observed():
    """A check that has appeared (even pending) is past discovery; the
    discovery deadline shouldn't apply to it."""
    w, _, clock = make_step_watcher(["a"], timeout=1000, discovery_timeout=10)
    w.step({"a": Status("pending")})
    clock.t = 100
    # No longer missing → not a discovery timeout, just regular wall-clock.
    assert w.step({"a": Status("pending")}) is Step.CONTINUE


def test_step_progress_is_throttled_by_progress_interval():
    """Progress lines fire on the first step then only after `progress_interval`
    elapses, regardless of how often `step()` is called."""
    c = make_console()
    clock = FakeClock()
    w = Watcher(
        ["a"],
        timeout=10_000,
        discovery_timeout=10_000,
        progress_interval=30,
        console=c,
        clock=clock,
    )
    w.step({"a": Status("pending")})  # t=0 → first progress line
    progress_lines = c.export_text(clear=False).count("success=")
    assert progress_lines == 1

    clock.t = 5  # well below 30s
    w.step({"a": Status("pending")})
    clock.t = 15
    w.step({"a": Status("pending")})
    assert c.export_text(clear=False).count("success=") == 1

    clock.t = 30  # crosses the interval
    w.step({"a": Status("pending")})
    assert c.export_text(clear=False).count("success=") == 2


def test_step_prints_header_only_once():
    w, c, _ = make_step_watcher(["a"], timeout=1000, discovery_timeout=1000)
    w.step({"a": Status("pending")})
    after_first = c.export_text(clear=False)
    w.step({"a": Status("pending")})
    after_second = c.export_text(clear=False)
    # Header line ("waiting on") appears exactly once.
    assert after_first.count("waiting on") == 1
    assert after_second.count("waiting on") == 1
