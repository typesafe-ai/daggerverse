"""GitHub commit-status fetching."""

import asyncio
from collections.abc import AsyncIterator, Mapping

import httpx

from .types import Status


GITHUB_API = "https://api.github.com"


def statuses_url(repo: str, ref: str) -> str:
    return f"{GITHUB_API}/repos/{repo}/commits/{ref}/statuses"


def auth_headers(token_plaintext: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token_plaintext}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def fetch_snapshot(client: httpx.AsyncClient, url: str) -> dict[str, Status]:
    """Page through `/statuses` and return {context: Status} keeping the newest
    entry per context. The API returns reverse-chronological."""
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


async def poll_snapshots(
    client: httpx.AsyncClient,
    url: str,
    interval: int,
) -> AsyncIterator[Mapping[str, Status]]:
    """Yield successive snapshots, sleeping `interval` seconds between fetches.

    The first snapshot is yielded immediately; the sleep happens after each
    yield, so a consumer that breaks out of the loop never sleeps unnecessarily.
    """
    while True:
        yield await fetch_snapshot(client, url)
        await asyncio.sleep(interval)
