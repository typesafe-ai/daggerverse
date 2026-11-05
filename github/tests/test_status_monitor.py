"""Public status-monitor behavior across GitHub status channels."""

import asyncio

import httpx
import pytest
from github.status_monitor.main import GithubStatusMonitor


class FakeToken:
    async def plaintext(self) -> str:
        return "token"


def install_github_responses(monkeypatch, *, status_name: str, status_state: str, run_name: str, run_conclusion: str):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/statuses"):
            return httpx.Response(200, json=[{"context": status_name, "state": status_state}])
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": run_name,
                            "status": "completed",
                            "conclusion": run_conclusion,
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(respond)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        return real_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr("github.status_monitor.main.httpx.AsyncClient", make_client)


def wait_for_both(name_status: str, name_run: str) -> str:
    return asyncio.run(
        GithubStatusMonitor().wait_for_statuses(
            repo="owner/repo",
            ref="deadbeef",
            token=FakeToken(),
            checks=[name_status],
            check_runs=[name_run],
            poll_interval=0,
        )
    )


@pytest.mark.parametrize(
    ("status_state", "run_conclusion", "failed_label"),
    [
        ("failure", "success", "status: build"),
        ("success", "failure", "check run: build"),
    ],
)
def test_same_named_failure_in_either_channel_prevents_success(monkeypatch, status_state, run_conclusion, failed_label):
    install_github_responses(
        monkeypatch,
        status_name="build",
        status_state=status_state,
        run_name="build",
        run_conclusion=run_conclusion,
    )

    with pytest.raises(RuntimeError, match=rf"checks failed: \['{failed_label}'\]"):
        wait_for_both("build", "build")


@pytest.mark.parametrize(
    ("status_name", "run_name"),
    [
        ("build", "build"),
        ("legacy-build", "actions-build"),
    ],
)
def test_every_successful_requested_channel_is_counted(monkeypatch, status_name, run_name):
    install_github_responses(
        monkeypatch,
        status_name=status_name,
        status_state="success",
        run_name=run_name,
        run_conclusion="success",
    )

    assert wait_for_both(status_name, run_name) == "all 2 checks succeeded"
