# wait-github-dagger-checks

Block until every expected check has reported a successful GitHub commit status
on a given ref.

By default, the expected check set is auto-discovered from
`dag.current_workspace().checks().list_()`. You can also pass an explicit list
of check names via the `checks` parameter to skip discovery.

## Wiring

`dag.current_workspace().checks()` enumerates the checks of the **primary**
loaded module. To make your repo's checks visible, install this module as a
dependency of your root module and wrap the call in a function on that module —
do not call `wait` directly via `-m wait-github-dagger-checks`, which would
load this module as primary and see zero checks.

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
        return await dag.wait_github_dagger_checks().wait(
            repo=repo, ref=ref, token=token
        )
```

## Example: GitHub Actions

```yaml
- name: Wait for Dagger Cloud checks
  uses: dagger/dagger-for-github@v8.4.1
  with:
    version: ${{ env.DAGGER_VERSION }}
    call: |
      wait-dagger-checks
        --repo=${{ github.repository }}
        --ref=${{ github.event.pull_request.head.sha || github.sha }}
        --token=env:GITHUB_TOKEN
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## Parameters

| name                | default | meaning                                                |
|---------------------|---------|--------------------------------------------------------|
| `repo`              | —       | `owner/name`                                           |
| `ref`               | —       | commit SHA to poll                                     |
| `token`             | —       | GitHub token (passed as secret)                        |
| `checks`            | `null`  | explicit check names; `null` auto-discovers, `[]` returns immediately |
| `poll-interval`     | `3`     | seconds between GitHub polls                           |
| `progress-interval` | `30`    | seconds between routine progress lines                 |
| `timeout`           | `1800`  | total wall-clock budget, in seconds                    |
| `discovery-timeout` | `300`   | how long expected statuses may take to first appear    |
| `fail-fast`         | `false` | whether to exit 1 on first check failure               |

## Waiting for an explicit check list

If you'd rather not rely on workspace auto-discovery, pass `checks` directly:

```python
return await dag.wait_github_dagger_checks().wait(
    repo=repo,
    ref=ref,
    token=token,
    checks=["build", "test", "lint"],
)
```

When `checks` is provided, `dag.current_workspace().checks()` is not consulted,
so this form also works when calling the module directly via
`-m wait-github-dagger-checks`.
