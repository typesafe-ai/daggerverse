"""github-status-monitor: poll a GitHub commit for a set of expected
status contexts and exit when they all reach a terminal state.
"""

from github_status_monitor.main import (
    GithubStatusMonitor as GithubStatusMonitor,
)
