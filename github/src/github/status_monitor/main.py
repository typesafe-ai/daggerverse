"""Wait for GitHub commit statuses and/or check runs to succeed.

Two entry points:

* :meth:`GithubStatusMonitor.wait_for_statuses` — explicit list of status
  contexts and/or check-run names; general-purpose for any GitHub check.
* :meth:`GithubStatusMonitor.wait_for_dagger_checks` — auto-discovers expected
  contexts from ``dag.current_workspace().checks()``; specific to Dagger
  Cloud checks that publish their state as GitHub Statuses.
"""

from typing import Annotated

import dagger
import httpx
from dagger import Doc, dag, field, function, object_type
from rich.console import Console

from github.status_monitor import render
from github.status_monitor.api import (
    auth_headers,
    check_runs_url,
    poll_snapshots,
    statuses_url,
)
from github.status_monitor.params import (
    DiscoveryTimeout,
    FailFast,
    PollInterval,
    ProgressInterval,
    Ref,
    Repo,
    Timeout,
    Token,
)
from github.status_monitor.types import Step
from github.status_monitor.watcher import Watcher


@object_type
class GithubStatusMonitor:
    github_api: Annotated[
        str,
        Doc("Base URL of the GitHub REST API."),
    ] = field(default="https://api.github.com")

    @function(cache="never")
    async def wait_for_statuses(
        self,
        repo: Repo,
        ref: Ref,
        token: Token,
        checks: Annotated[
            list[str] | None,
            Doc("GitHub commit-status context names to wait for."),
        ] = None,
        check_runs: Annotated[
            list[str] | None,
            Doc("GitHub Actions check-run names to wait for (emitted by jobs)."),
        ] = None,
        poll_interval: PollInterval = 3,
        progress_interval: ProgressInterval = 30,
        timeout: Timeout = 1800,
        discovery_timeout: DiscoveryTimeout = 300,
        fail_fast: FailFast = False,
    ) -> str:
        """Wait until every status context in `checks` and every check-run in
        `check_runs` succeeds on `ref`."""
        return await self._wait(
            repo=repo,
            ref=ref,
            token=token,
            status_names=sorted(set(checks or [])),
            check_run_names=sorted(set(check_runs or [])),
            poll_interval=poll_interval,
            progress_interval=progress_interval,
            timeout=timeout,
            discovery_timeout=discovery_timeout,
            fail_fast=fail_fast,
        )

    @function(cache="never")
    async def wait_for_dagger_checks(
        self,
        repo: Repo,
        ref: Ref,
        token: Token,
        poll_interval: PollInterval = 3,
        progress_interval: ProgressInterval = 30,
        timeout: Timeout = 1800,
        discovery_timeout: DiscoveryTimeout = 300,
        fail_fast: FailFast = False,
    ) -> str:
        """Wait for every Dagger check in the current workspace to succeed.

        The expected status set is enumerated via
        ``dag.current_workspace().checks()`` — Dagger check names are assumed
        to match the GitHub status `context` strings published by Dagger Cloud.
        """
        discovered = await dag.current_workspace().checks().list_()
        expected = sorted({await c.name() for c in discovered})
        return await self._wait(
            repo=repo,
            ref=ref,
            token=token,
            status_names=expected,
            check_run_names=[],
            poll_interval=poll_interval,
            progress_interval=progress_interval,
            timeout=timeout,
            discovery_timeout=discovery_timeout,
            fail_fast=fail_fast,
        )

    async def _wait(
        self,
        *,
        repo: str,
        ref: str,
        token: dagger.Secret,
        status_names: list[str],
        check_run_names: list[str],
        poll_interval: int,
        progress_interval: int,
        timeout: int,
        discovery_timeout: int,
        fail_fast: bool,
    ) -> str:
        console = Console(force_terminal=True)

        expected = sorted(set(status_names + check_run_names))
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
        s_url = statuses_url(github_api=self.github_api, repo=repo, ref=ref) if status_names else None
        cr_url = check_runs_url(github_api=self.github_api, repo=repo, ref=ref) if check_run_names else None

        async with httpx.AsyncClient(headers=auth_headers(plaintext), timeout=15) as client:
            async for snapshot in poll_snapshots(
                client,
                statuses_url=s_url,
                check_runs_url=cr_url,
                interval=poll_interval,
            ):
                match watcher.step(snapshot):
                    case Step.SUCCEEDED:
                        return f"all {len(watcher.succeeded)} checks succeeded"
                    case Step.FAILED:
                        raise RuntimeError(f"checks failed: {watcher.failed}")
                    case Step.DISCOVERY_TIMEOUT:
                        raise TimeoutError(f"checks never appeared on {ref}: {watcher.missing}")
                    case Step.WALLCLOCK_TIMEOUT:
                        raise TimeoutError(f"timed out: pending={watcher.pending} missing={watcher.missing}")
                    case Step.CONTINUE:
                        continue

        raise RuntimeError("unreachable")
