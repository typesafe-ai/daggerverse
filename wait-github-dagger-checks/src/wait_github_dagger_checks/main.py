"""Dagger function: wait for Dagger Cloud checks to land as successful GitHub
commit statuses.

Glue layer only — the watcher owns state, deadlines, and rendering.
"""

from typing import Annotated

import dagger
import httpx
from dagger import Doc, dag, function, object_type
from rich.console import Console

from wait_github_dagger_checks import render
from .github import auth_headers, poll_snapshots, statuses_url
from .types import Step
from .watcher import Watcher


@object_type
class WaitGithubDaggerChecks:
    @function(cache="never")
    async def wait(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[dagger.Secret, Doc("GitHub token with read access")],
        checks: Annotated[
            list[str] | None,
            Doc("An optional explicit list of check names to wait for."),
        ] = None,
        poll_interval: Annotated[int, Doc("Seconds between GitHub polls")] = 3,
        progress_interval: Annotated[
            int,
            Doc(
                "Seconds between routine progress lines (terminal transitions are "
                "still printed live)."
            ),
        ] = 30,
        timeout: Annotated[int, Doc("Total wall-clock budget, seconds")] = 1800,
        discovery_timeout: Annotated[
            int, Doc("How long expected statuses may take to first appear, seconds")
        ] = 300,
        fail_fast: Annotated[
            bool,
            Doc(
                "If True, raise as soon as any check fails. "
                "If False (default), wait for every check to reach a terminal "
                "state and then raise at the end if any failed."
            ),
        ] = False,
    ) -> str:
        """Wait until every expected check has a successful GitHub commit status
        on `ref`.

        When `checks` is None, the expected set is enumerated via
        `dag.current_workspace().checks()`. When `checks` is provided, those
        names are used verbatim and auto-discovery is skipped. An empty
        `checks` list returns immediately.
        """
        console = Console(force_terminal=True)

        if checks is None:
            discovered = await dag.current_workspace().checks().list_()
            expected = sorted({await c.name() for c in discovered})
        else:
            expected = sorted(set(checks))
        if not expected:
            render.empty(console)
            return "no checks to wait for"

        watcher = Watcher(
            expected=expected,
            repo=repo,
            ref=ref,
            fail_fast=fail_fast,
            timeout=timeout,
            discovery_timeout=discovery_timeout,
            poll_interval=poll_interval,
            progress_interval=progress_interval,
            console=console,
        )

        plaintext = await token.plaintext()
        url = statuses_url(repo=repo, ref=ref)

        async with httpx.AsyncClient(
            headers=auth_headers(plaintext), timeout=15
        ) as client:
            async for snapshot in poll_snapshots(client, url, poll_interval):
                match watcher.step(snapshot):
                    case Step.SUCCEEDED:
                        return f"all {len(watcher.succeeded)} checks succeeded"
                    case Step.FAILED:
                        raise RuntimeError(f"checks failed: {watcher.failed}")
                    case Step.DISCOVERY_TIMEOUT:
                        raise TimeoutError(
                            f"checks never appeared on {ref}: {watcher.missing}"
                        )
                    case Step.WALLCLOCK_TIMEOUT:
                        raise TimeoutError(
                            f"timed out: pending={watcher.pending} "
                            f"missing={watcher.missing}"
                        )
                    case Step.CONTINUE:
                        continue

        raise RuntimeError("unreachable")
