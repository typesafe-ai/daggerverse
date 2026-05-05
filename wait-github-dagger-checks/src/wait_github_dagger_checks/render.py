"""Rich-based console output. Each function takes a Console explicitly so the
caller (typically a Watcher) controls where output goes — including a
recording Console in tests."""

from rich.console import Console
from rich.table import Table

from .types import MISSING


STATE_STYLE: dict[str, str] = {
    "success": "bold green",
    "pending": "yellow",
    "failure": "bold red",
    "error": "bold red",
    MISSING: "dim",
}


def header(console: Console, *, repo: str, ref: str, expected: list[str]) -> None:
    console.rule(f"[bold]waiting on {len(expected)} Dagger check(s)[/]")
    console.print(f"repo: [cyan]{repo}[/]  ref: [cyan]{ref}[/]")
    console.print(f"expected: {', '.join(expected)}")


def transition(console: Console, *, name: str, state: str, trace_url: str) -> None:
    style = STATE_STYLE.get(state, "")
    icon = "✓" if state == "success" else "✗"
    link = f" [link={trace_url}]trace ↗[/link]" if trace_url else ""
    console.print(f"[{style}]{icon} {state:<7}[/] {name}{link}")


def progress(
    console: Console,
    *,
    succeeded: int,
    pending: int,
    missing: int,
    total: int,
) -> None:
    console.print(
        f"[dim]success={succeeded}/{total} pending={pending} missing={missing}[/]"
    )


def final_table(
    console: Console,
    *,
    expected: list[str],
    states: dict[str, str],
    traces: dict[str, str],
) -> None:
    table = Table(show_header=True, header_style="bold", title="final status")
    table.add_column("check")
    table.add_column("state")
    table.add_column("trace")
    for ctx in expected:
        state = states[ctx]
        url = traces.get(ctx, "")
        trace_cell = f"[link={url}]{url}[/link]" if url else ""
        table.add_row(ctx, f"[{STATE_STYLE.get(state, '')}]{state}[/]", trace_cell)
    console.print(table)


def success(console: Console, *, count: int) -> None:
    console.print(f"[bold green]✓ all {count} checks succeeded[/]")


def failure(console: Console, *, failed: list[str]) -> None:
    console.print(f"[bold red]✗ checks failed: {failed}[/]")


def discovery_timeout(console: Console, *, ref: str, missing: list[str]) -> None:
    console.print(f"[bold red]✗ checks never appeared on {ref}: {missing}[/]")


def wallclock_timeout(
    console: Console, *, pending: list[str], missing: list[str]
) -> None:
    console.print(f"[bold red]✗ timed out: pending={pending} missing={missing}[/]")


def empty(console: Console) -> None:
    console.print("[yellow]no checks to wait for[/]")
