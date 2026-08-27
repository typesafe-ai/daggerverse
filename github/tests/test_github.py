"""Tests for GitHub API helpers — check-run state mapping and snapshot parsing."""

import asyncio
from unittest.mock import AsyncMock, call, patch

import httpx
import pytest
from github.status_monitor.api import _check_run_state, fetch_check_runs_snapshot, poll_snapshots
from github.status_monitor.types import Status

# ---- _check_run_state mapping ----


@pytest.mark.parametrize(
    "status, conclusion, expected",
    [
        ("completed", "success", "success"),
        ("completed", "failure", "failure"),
        ("completed", "timed_out", "failure"),
        ("completed", "cancelled", "failure"),
        ("completed", "action_required", "failure"),
        ("completed", "neutral", "success"),
        ("completed", "skipped", "success"),
        ("completed", "stale", "error"),
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("waiting", None, "pending"),
        ("requested", None, "pending"),
        ("pending", None, "pending"),
    ],
)
def test_check_run_state_mapping(status: str, conclusion: str | None, expected: str):
    run = {"status": status}
    if conclusion is not None:
        run["conclusion"] = conclusion
    assert _check_run_state(run) == expected


# ---- fetch_check_runs_snapshot ----


class FakeResponse:
    def __init__(self, json_data, status_code: int = 200, headers: dict[str, str] | None = None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.github.com/test")
            response = httpx.Response(self.status_code, headers=self.headers, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    def json(self):
        return self._json


class FakeClient:
    def __init__(self, outcomes: list[dict | FakeResponse | Exception]):
        self._outcomes = outcomes
        self._call = 0

    async def get(self, url, params=None):
        idx = self._call
        self._call += 1
        outcome = self._outcomes[idx] if idx < len(self._outcomes) else {"check_runs": []}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, FakeResponse) else FakeResponse(outcome)


def test_fetch_check_runs_snapshot_basic():
    page = {
        "check_runs": [
            {
                "name": "pulumi / up (org:core)",
                "status": "completed",
                "conclusion": "success",
            },
            {"name": "pulumi / up (k8s:dev)", "status": "in_progress"},
        ],
    }
    client = FakeClient([page])
    result = asyncio.run(fetch_check_runs_snapshot(client, "https://api.github.com/test"))
    assert result == {
        "pulumi / up (org:core)": Status(state="success"),
        "pulumi / up (k8s:dev)": Status(state="pending"),
    }


def test_fetch_check_runs_snapshot_keeps_first_per_name():
    page = {
        "check_runs": [
            {"name": "a", "status": "completed", "conclusion": "success"},
            {"name": "a", "status": "completed", "conclusion": "failure"},
        ],
    }
    client = FakeClient([page])
    result = asyncio.run(fetch_check_runs_snapshot(client, "https://api.github.com/test"))
    assert result == {"a": Status(state="success")}


@pytest.mark.parametrize(
    "transient_error",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
def test_poll_snapshots_retries_transport_errors(transient_error):
    async def run():
        client = FakeClient([transient_error, [{"context": "test", "state": "success"}]])
        snapshots = poll_snapshots(
            client,
            statuses_url="https://api.github.com/statuses",
            check_runs_url=None,
            interval=3,
        )
        with patch("github.status_monitor.api.asyncio.sleep", new_callable=AsyncMock) as sleep:
            snapshot = await anext(snapshots)
        return snapshot, sleep

    snapshot, sleep = asyncio.run(run())
    assert snapshot == {"test": Status(state="success")}
    sleep.assert_awaited_once_with(1)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse([], status_code=429, headers={"retry-after": "7"}),
        FakeResponse([], status_code=503),
        FakeResponse([], status_code=403, headers={"x-ratelimit-remaining": "0"}),
    ],
)
def test_poll_snapshots_retries_transient_http_statuses(response):
    async def run():
        client = FakeClient([response, [{"context": "test", "state": "pending"}]])
        snapshots = poll_snapshots(
            client,
            statuses_url="https://api.github.com/statuses",
            check_runs_url=None,
            interval=3,
        )
        with patch("github.status_monitor.api.asyncio.sleep", new_callable=AsyncMock) as sleep:
            snapshot = await anext(snapshots)
        return snapshot, sleep

    snapshot, sleep = asyncio.run(run())
    assert snapshot == {"test": Status(state="pending")}
    sleep.assert_awaited_once_with(int(response.headers.get("retry-after", "1")))


def test_poll_snapshots_bounds_retries_and_does_not_retry_permissions_errors():
    async def run(outcomes):
        snapshots = poll_snapshots(
            FakeClient(outcomes),
            statuses_url="https://api.github.com/statuses",
            check_runs_url=None,
            interval=3,
        )
        with (
            patch("github.status_monitor.api.asyncio.sleep", new_callable=AsyncMock) as sleep,
            pytest.raises(httpx.HTTPError),
        ):
            await anext(snapshots)
        return sleep

    sleep = asyncio.run(run([httpx.ConnectTimeout("timed out")] * 5))
    assert sleep.await_args_list == [call(1), call(2), call(4), call(8)]

    sleep = asyncio.run(run([FakeResponse([], status_code=403)]))
    sleep.assert_not_awaited()
