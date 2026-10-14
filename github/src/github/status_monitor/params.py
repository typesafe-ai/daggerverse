"""Reusable annotated type aliases for status-monitor Dagger function parameters."""

from typing import Annotated, TypeAlias

import dagger
from dagger import Doc

Repo: TypeAlias = Annotated[str, Doc("GitHub repo as 'owner/name'")]

Ref: TypeAlias = Annotated[str, Doc("Commit SHA to poll")]

Token: TypeAlias = Annotated[dagger.Secret, Doc("GitHub token with read access")]

PollInterval: TypeAlias = Annotated[int, Doc("Seconds between GitHub polls")]

ProgressInterval: TypeAlias = Annotated[
    int,
    Doc("Seconds between routine progress lines (terminal transitions are still printed live)."),
]

Timeout: TypeAlias = Annotated[int, Doc("Total wall-clock budget, seconds")]

DiscoveryTimeout: TypeAlias = Annotated[int, Doc("How long expected statuses may take to first appear, seconds")]

RetryGracePeriod: TypeAlias = Annotated[
    int,
    Doc("Seconds to allow an error status to return to pending or success before failing; 0 disables retry grace."),
]

FailFast: TypeAlias = Annotated[
    bool,
    Doc(
        "If True, raise as soon as any check fails (error statuses still get their retry grace period). "
        "If False (default), wait for every check to reach a terminal "
        "state and then raise at the end if any failed."
    ),
]
