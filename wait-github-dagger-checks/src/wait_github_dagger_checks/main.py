"""Wait for Dagger Cloud checks to land as successful GitHub commit statuses."""

import asyncio
import time
from typing import Annotated

import dagger
import httpx
from dagger import Doc, dag, function, object_type
from rich.console import Console
from rich.table import Table


GITHUB_API = "https://api.github.com"

# force_terminal so colors render in the GitHub Actions log.
console = Console(force_terminal=True)

STATE_STYLE = {
    "success": "bold green",
    "pending": "yellow",
    "failure": "bold red",
    "error": "bold red",
    "missing": "dim",
}


@object_type
class WaitGithubDaggerChecks:
    @function(cache="never")
    async def wait(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[dagger.Secret, Doc("GitHub token with read access")],
        poll_interval: Annotated[int, Doc("Seconds between polls")] = 10,
        timeout: Annotated[int, Doc("Total wall-clock budget, seconds")] = 1800,
        discovery_timeout: Annotated[
            int, Doc("How long expected statuses may take to first appear, seconds")
        ] = 300,
    ) -> str:
        """Wait until every Dagger check in the current workspace has a successful
        GitHub commit status on `ref`.

        The expected check set is enumerated via
        `dag.current_workspace().checks()`, so caller-side check lists or regexes
        are not needed.
        """
        checks = await dag.current_workspace().checks().list_()
        expected = sorted({await c.name() for c in checks})
        if not expected:
            console.print("[yellow]no checks to wait for[/]")
            return "no checks to wait for"

        console.rule(f"[bold]waiting on {len(expected)} Dagger check(s)[/]")
        console.print(f"repo: [cyan]{repo}[/]  ref: [cyan]{ref}[/]")
        console.print(f"expected: {', '.join(expected)}")

        plaintext = await token.plaintext()
        headers = {
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{GITHUB_API}/repos/{repo}/commits/{ref}/statuses"

        deadline = time.monotonic() + timeout
        discovery_deadline = time.monotonic() + discovery_timeout

        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            while True:
                latest = await _fetch_latest(client, url)

                states = {c: latest.get(c, "missing") for c in expected}
                _print_status_table(states)

                failed = sorted(
                    c for c, s in states.items() if s in {"failure", "error"}
                )
                if failed:
                    console.print(f"[bold red]✗ checks failed: {failed}[/]")
                    raise RuntimeError(f"checks failed: {failed}")

                pending = [c for c, s in states.items() if s == "pending"]
                missing = [c for c, s in states.items() if s == "missing"]
                success = [c for c, s in states.items() if s == "success"]

                if not pending and not missing and len(success) == len(expected):
                    console.print(
                        f"[bold green]✓ all {len(success)} checks succeeded[/]"
                    )
                    return f"all {len(success)} checks succeeded"

                now = time.monotonic()
                if missing and now > discovery_deadline:
                    console.print(
                        f"[bold red]✗ checks never appeared on {ref}: {missing}[/]"
                    )
                    raise TimeoutError(f"checks never appeared on {ref}: {missing}")
                if now > deadline:
                    console.print(
                        f"[bold red]✗ timed out: pending={pending} missing={missing}[/]"
                    )
                    raise TimeoutError(
                        f"timed out: pending={pending} missing={missing}"
                    )

                console.print(
                    f"[dim]success={len(success)}/{len(expected)} "
                    f"pending={len(pending)} missing={len(missing)}; "
                    f"sleeping {poll_interval}s[/]"
                )
                await asyncio.sleep(poll_interval)


def _print_status_table(states: dict[str, str]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("check")
    table.add_column("state")
    for ctx in sorted(states):
        state = states[ctx]
        table.add_row(ctx, f"[{STATE_STYLE.get(state, '')}]{state}[/]")
    console.print(table)


async def _fetch_latest(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    """Page through `/statuses` and return {context: state} keeping the newest
    state per context (API returns reverse-chronological)."""
    latest: dict[str, str] = {}
    page = 1
    while True:
        r = await client.get(url, params={"per_page": 100, "page": page})
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        for s in items:
            ctx = s["context"]
            if ctx not in latest:
                latest[ctx] = s["state"]
        if len(items) < 100:
            break
        page += 1
    return latest
