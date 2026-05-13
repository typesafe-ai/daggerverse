"""GitHub commit-status and check-run fetching."""

import asyncio
from collections.abc import AsyncIterator, Mapping

import httpx

from .types import Status


def statuses_url(*, github_api: str, repo: str, ref: str) -> str:
    return f"{github_api}/repos/{repo}/commits/{ref}/statuses"


def check_runs_url(*, github_api: str, repo: str, ref: str) -> str:
    return f"{github_api}/repos/{repo}/commits/{ref}/check-runs"


def auth_headers(token_plaintext: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token_plaintext}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


_CHECK_RUN_STATUS_MAP: dict[str, str] = {
    "queued": "pending",
    "in_progress": "pending",
    "waiting": "pending",
    "requested": "pending",
    "pending": "pending",
}


def _check_run_state(run: dict) -> str:
    status = run["status"]
    if status == "completed":
        conclusion = run.get("conclusion", "failure")
        if conclusion == "success":
            return "success"
        if conclusion in ("failure", "timed_out", "cancelled", "action_required"):
            return "failure"
        if conclusion == "neutral" or conclusion == "skipped":
            return "success"
        return "error"
    return _CHECK_RUN_STATUS_MAP.get(status, "pending")


async def fetch_statuses_snapshot(
    client: httpx.AsyncClient, url: str
) -> dict[str, Status]:
    """Page through ``/statuses`` and return ``{context: Status}`` keeping the
    newest entry per context. The API returns reverse-chronological."""
    latest: dict[str, Status] = {}
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
                latest[ctx] = Status(state=s["state"])
        if len(items) < 100:
            break
        page += 1
    return latest


async def fetch_check_runs_snapshot(
    client: httpx.AsyncClient, url: str
) -> dict[str, Status]:
    """Page through ``/check-runs`` and return ``{name: Status}``."""
    latest: dict[str, Status] = {}
    page = 1
    while True:
        r = await client.get(url, params={"per_page": 100, "page": page})
        r.raise_for_status()
        data = r.json()
        runs = data.get("check_runs", [])
        if not runs:
            break
        for run in runs:
            name = run["name"]
            if name not in latest:
                latest[name] = Status(state=_check_run_state(run))
        if len(runs) < 100:
            break
        page += 1
    return latest


async def poll_snapshots(
    client: httpx.AsyncClient,
    *,
    statuses_url: str | None,
    check_runs_url: str | None,
    interval: int,
) -> AsyncIterator[Mapping[str, Status]]:
    """Yield successive merged snapshots from both commit statuses and check runs.

    The first snapshot is yielded immediately; the sleep happens after each
    yield, so a consumer that breaks out of the loop never sleeps unnecessarily.
    """
    while True:
        merged: dict[str, Status] = {}
        if statuses_url:
            merged.update(await fetch_statuses_snapshot(client, statuses_url))
        if check_runs_url:
            merged.update(await fetch_check_runs_snapshot(client, check_runs_url))
        yield merged
        await asyncio.sleep(interval)
