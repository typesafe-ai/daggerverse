"""Tests for GitHub API helpers — check-run state mapping and snapshot parsing."""

import asyncio

import pytest

from github.status_monitor.api import _check_run_state, fetch_check_runs_snapshot
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
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class FakeClient:
    def __init__(self, pages: list[dict]):
        self._pages = pages
        self._call = 0

    async def get(self, url, params=None):
        idx = self._call
        self._call += 1
        return FakeResponse(self._pages[idx] if idx < len(self._pages) else {"check_runs": []})


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
