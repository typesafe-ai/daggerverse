# github

GitHub utilities for Dagger pipelines.

## status-monitor

Block until every expected GitHub commit-status `context` on a given ref
reaches a successful terminal state.

Two entry points:

- **`wait-for-statuses`** -- general-purpose; takes an explicit list of status
  contexts. Use this for any GitHub Status checks (CI providers, custom
  pipelines, branch-protection contexts, etc).
- **`wait-for-dagger-checks`** -- specific to Dagger Cloud checks. Auto-discovers
  the expected set via `dag.current_workspace().checks()`; takes no `checks`
  argument.

The `github_api` field is configurable for GitHub Enterprise Server
(default: `https://api.github.com`).

### Wiring

`dag.current_workspace().checks()` enumerates the checks of the **primary**
loaded module. For `wait-for-dagger-checks` to see your repo's checks, install
this module as a dependency of your root module and wrap the call in a function
on that module -- do not call it directly via `-m github`, which would load this
module as primary and see zero checks.

`wait-for-statuses` doesn't rely on workspace discovery and works fine when
called directly.

In your root module (Python example):

```python
from typing import Annotated
import dagger
from dagger import Doc, dag, function, object_type


@object_type
class MyModule:
    @function
    async def wait_dagger_checks(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[dagger.Secret, Doc("GitHub token")],
    ) -> str:
        return await dag.github().status_monitor().wait_for_dagger_checks(repo=repo, ref=ref, token=token)
```

### Example: GitHub Actions

```yaml
- name: Wait for Dagger Cloud checks
  uses: dagger/dagger-for-github@v8.4.1
  with:
    version: ${{ env.DAGGER_VERSION }}
    call: |
      status-monitor
        wait-for-dagger-checks
        --repo=${{ github.repository }}
        --ref=${{ github.event.pull_request.head.sha || github.sha }}
        --token=env:GITHUB_TOKEN
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Waiting for an explicit list of GitHub statuses

`checks` takes the GitHub commit-status `context` strings exactly as they
appear on the commit page (e.g. `build`, `test`, `lint`) -- no `owner/repo`
prefix.

```bash
dagger -m github.com/typesafe-ai/daggerverse/github call \
  status-monitor \
  wait-for-statuses \
  --repo=owner/name \
  --ref=$SHA \
  --token=env:GITHUB_TOKEN \
  --checks=build,test,lint
```

### Parameters (shared by both functions)

| name                | default                    | meaning                                                |
|---------------------|----------------------------|--------------------------------------------------------|
| `repo`              | --                         | `owner/name`                                           |
| `ref`               | --                         | commit SHA to poll                                     |
| `token`             | --                         | GitHub token (passed as secret)                        |
| `poll-interval`     | `3`                        | seconds between GitHub polls                           |
| `progress-interval` | `30`                       | seconds between routine progress lines                 |
| `timeout`           | `1800`                     | total wall-clock budget, in seconds                    |
| `discovery-timeout` | `300`                      | how long expected statuses may take to first appear    |
| `retry-grace-period` | `0` (`60` for Dagger checks) | seconds to allow an `error` status to be retried; `0` disables grace |
| `fail-fast`         | `false`                    | whether to exit 1 on first check failure               |

Dagger Cloud may briefly publish `error` when an engine disconnects, then
return the check to `pending` for a retry. The grace period starts when each
check is observed entering `error`. Repeated polls do not extend it; a new
error after returning to `pending` gets a new grace period. `failure` statuses
are never given retry grace, and the total `timeout` still bounds all retries.
`fail-fast` also honors retry grace for `error` statuses.

`wait-for-statuses` additionally requires:

| name     | default | meaning                                                  |
|----------|---------|----------------------------------------------------------|
| `checks` | --      | GitHub status `context` names to wait for                |

Module-level field:

| name         | default                     | meaning                              |
|--------------|-----------------------------|--------------------------------------|
| `github-api` | `https://api.github.com`    | base URL of the GitHub REST API      |
