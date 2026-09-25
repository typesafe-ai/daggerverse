"""Rich-based console output. Each function takes a Console explicitly so the
caller (typically a Watcher) controls where output goes — including a
recording Console in tests."""

from rich.console import Console

from .types import MISSING, Check, format_check

STATE_STYLE: dict[str, str] = {
    "success": "bold green",
    "pending": "yellow",
    "failure": "bold red",
    "error": "bold red",
    MISSING: "dim",
}


def header(console: Console, *, repo: str, ref: str, expected: list[Check | str]) -> None:
    console.rule(f"[bold]waiting on {len(expected)} Dagger check(s)[/]")
    console.print(f"repo: [cyan]{repo}[/]  ref: [cyan]{ref}[/]")
    console.print(f"expected: {', '.join(format_check(check) for check in expected)}")


def transition(console: Console, *, name: Check | str, state: str) -> None:
    style = STATE_STYLE.get(state, "")
    icon = "✓" if state == "success" else "✗"
    console.print(f"[{style}]{icon} {state:<7}[/] {format_check(name)}")


def progress(
    console: Console,
    *,
    succeeded: int,
    pending: int,
    missing: int,
    total: int,
) -> None:
    console.print(f"[dim]success={succeeded}/{total} pending={pending} missing={missing}[/]")


def final_table(
    console: Console,
    *,
    expected: list[Check | str],
    states: dict[Check | str, str],
) -> None:
    console.rule("[bold]final status[/]")
    for ctx in expected:
        state = states[ctx]
        style = STATE_STYLE.get(state, "")
        glyph = "✓" if state == "success" else "✗"
        console.print(f"[{style}]{glyph} {format_check(ctx)}[/]")
        console.rule(style="dim")


def success(console: Console, *, count: int) -> None:
    console.print(f"[bold green]✓ all {count} checks succeeded[/]")


def failure(console: Console, *, failed: list[Check | str]) -> None:
    console.print(f"[bold red]✗ checks failed: {[format_check(check) for check in failed]}[/]")


def discovery_timeout(console: Console, *, ref: str, missing: list[Check | str]) -> None:
    labels = [format_check(check) for check in missing]
    console.print(f"[bold red]✗ checks never appeared on {ref}: {labels}[/]")


def wallclock_timeout(console: Console, *, pending: list[Check | str], missing: list[Check | str]) -> None:
    pending_labels = [format_check(check) for check in pending]
    missing_labels = [format_check(check) for check in missing]
    console.print(f"[bold red]✗ timed out: pending={pending_labels} missing={missing_labels}[/]")


def empty(console: Console) -> None:
    console.print("[yellow]no checks to wait for[/]")
