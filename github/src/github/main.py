"""Dagger module: GitHub utilities for CI pipelines."""

from typing import Annotated

from dagger import Doc, field, function, object_type

from github.status_monitor.main import GithubStatusMonitor


@object_type
class Github:
    github_api: Annotated[
        str,
        Doc("Base URL of the GitHub REST API."),
    ] = field(default="https://api.github.com")

    @function
    def status_monitor(self) -> GithubStatusMonitor:
        """Poll GitHub commit statuses and check runs until a final verdict is reached."""
        return GithubStatusMonitor(github_api=self.github_api)
