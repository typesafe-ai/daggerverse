"""Verify retry grace reaches the watcher through the public Dagger functions."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from github.status_monitor import main
from github.status_monitor.main import GithubStatusMonitor
from github.status_monitor.types import Status


@pytest.mark.parametrize("grace", [None, 60])
def test_explicit_statuses_forward_retry_grace(monkeypatch, grace):
    wait = AsyncMock(return_value="all 1 checks succeeded")
    monkeypatch.setattr(GithubStatusMonitor, "_wait", wait)
    kwargs = {} if grace is None else {"retry_grace_period": grace}
    asyncio.run(
        GithubStatusMonitor().wait_for_statuses(repo="org/repo", ref="sha", token=Mock(), checks=["a"], **kwargs)
    )
    assert wait.await_args.kwargs["retry_grace_period"] == (0 if grace is None else grace)


@pytest.mark.parametrize("grace", [None, 0, 120])
def test_dagger_checks_forward_retry_grace(monkeypatch, grace):
    wait = AsyncMock(return_value="all 1 checks succeeded")
    monkeypatch.setattr(GithubStatusMonitor, "_wait", wait)
    check = Mock()
    check.name = AsyncMock(return_value="a")
    checks = Mock(list_=AsyncMock(return_value=[check]))
    monkeypatch.setattr(main, "dag", Mock(current_workspace=Mock(return_value=Mock(checks=Mock(return_value=checks)))))
    kwargs = {} if grace is None else {"retry_grace_period": grace}
    asyncio.run(GithubStatusMonitor().wait_for_dagger_checks(repo="org/repo", ref="sha", token=Mock(), **kwargs))
    assert wait.await_args.kwargs["retry_grace_period"] == (60 if grace is None else grace)
    assert wait.await_args.kwargs["status_names"] == ["a"]


def test_wait_follows_error_pending_success(monkeypatch):
    async def snapshots(*args, **kwargs):
        for state in ("error", "pending", "success"):
            yield {"a": Status(state)}

    monkeypatch.setattr(main, "poll_snapshots", snapshots)
    result = asyncio.run(
        GithubStatusMonitor().wait_for_statuses(
            repo="org/repo",
            ref="sha",
            token=Mock(plaintext=AsyncMock(return_value="test-token")),
            checks=["a"],
            retry_grace_period=60,
        )
    )
    assert result == "all 1 checks succeeded"
