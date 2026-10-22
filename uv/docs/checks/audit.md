---
icon: lucide/shield-check
title: Auditing dependencies
description: Audit the locked dependencies of every uv workspace in a source tree for known vulnerabilities.
---

# Auditing dependencies

`audit` runs `uv audit --frozen` against a workspace's `uv.lock` and reports known
vulnerabilities. It reads the lockfile as committed, without re-resolving against
package indexes, so it needs no index credentials.

## Auditing a whole source tree

`workspaces` is a [collection](https://docs.dagger.io/reference/sdks/python/#collections)
with one item per `uv.lock` in the source tree, keyed by the workspace's path.

Install the module into your workspace, then run its checks:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
$ dagger check --uv
```

List the workspaces it found:

```console
$ dagger check -l -a --uv
```

### Skipping workspaces

Test fixtures and intentionally vulnerable sample projects shouldn't fail your build.
The `exclude` setting takes glob patterns, matched against each workspace's
source-relative path, and leaves matching workspaces out of `workspaces`:

```toml title="dagger.toml"
[modules.uv.settings]
exclude = ["**/tests/_packages/**"]
```

## Auditing a single workspace

Select one workspace by its path (see
[Select collection items](https://docs.dagger.io/using/checking/#select-collection-items)):

=== "CLI"

    ```console
    $ dagger check --uv --uv-workspace-source=services/api
    ```

=== "Python SDK"

    ```python
    await uv().workspace("services/api").audit(ws).pass_()
    ```
