# github

GitHub utilities for Dagger pipelines.

## status-monitor

Block until every expected GitHub commit-status `context` on a given ref
reaches a successful terminal state.

Two entry points:

- **`wait-for-statuses`**: general-purpose; takes an explicit list of status
  contexts. Use this for any GitHub Status checks (CI providers, custom
  pipelines, branch-protection contexts, etc).
- **`wait-for-dagger-checks`**: specific to Dagger Cloud checks. Discovers
  the expected set from the workspace's check paths
  (`Workspace.artifacts()`); takes no `checks` argument.

The `github_api` field is configurable for GitHub Enterprise Server
(default: `https://api.github.com`).

### Wiring

Dagger Cloud names each status after the check's path, which leaves out the
module name for checks of the workspace's main module (`docs`, not
`my-module/docs`). Discovery names them the same way only while your main module
is the main module, so add this module as a client of your main module and wrap
the call in a function there. Calling it directly with `-m github` makes this
module the main one: your checks then carry their module name and never match
the statuses.

`wait-for-statuses` doesn't rely on workspace discovery, so you can call it
directly.

In your root module (Python example):

```python
from typing import Annotated

from dagger import Doc, function, object_type
from dagger_clients.core import Secret
from dagger_clients.github import github


@object_type
class MyModule:
    @function
    async def wait_dagger_checks(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[Secret, Doc("GitHub token")],
    ) -> str:
        return await github().status_monitor().wait_for_dagger_checks(repo=repo, ref=ref, token=token)
```

### Example: GitHub Actions

```yaml
- name: Wait for Dagger Cloud checks
  uses: dagger/dagger-for-github@v8.4.1
  with:
    version: ${{ env.DAGGER_VERSION }}
    cloud-token: ${{ secrets.DAGGER_CLOUD_TOKEN }}
    dagger-flags: --engine=cloud
    # Calls the wrapper on your main module (see above).
    call: |
      wait-dagger-checks
        --repo=${{ github.repository }}
        --ref=${{ github.event.pull_request.head.sha || github.sha }}
        --token=env:GITHUB_TOKEN
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Waiting for an explicit list of GitHub statuses

`checks` takes the GitHub commit-status `context` strings exactly as they
appear on the commit page (e.g. `build`, `test`, `lint`), without an `owner/repo`
prefix.

```bash
dagger call -m github.com/typesafe-ai/daggerverse/github \
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
| `fail-fast`         | `false`                    | whether to exit 1 on first check failure               |

`wait-for-statuses` additionally requires:

| name     | default | meaning                                                  |
|----------|---------|----------------------------------------------------------|
| `checks` | --      | GitHub status `context` names to wait for                |

Module-level field:

| name         | default                     | meaning                              |
|--------------|-----------------------------|--------------------------------------|
| `github-api` | `https://api.github.com`    | base URL of the GitHub REST API      |
