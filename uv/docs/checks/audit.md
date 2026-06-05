---
icon: lucide/shield-check
title: Auditing dependencies
description: Audit the locked dependencies of every uv workspace in a source tree for known vulnerabilities.
---

# Auditing dependencies

`audit` runs `uv audit --frozen` against your locked dependencies and reports known
vulnerabilities, for all `uv.lock` files found in the source tree, in parallel.

## Auditing a whole source tree

The top-level `audit` is a **check**: it discovers every workspace in your source
(one per `uv.lock`) and audits them all in parallel. It exits non-zero if any
workspace has a finding, and each workspace's report shows up as its own node in the
Dagger trace, so a failure points you straight at the offending lockfile.

=== "CLI"

    Installing the module as a toolchain surfaces the check as `uv:audit`, so you run it
    directly alongside your other Dagger checks:

    ```console
    $ dagger check uv:audit
    ```

=== "Python SDK"

    ```python
    await dag.uv(source=src).audit()
    ```

### Skipping workspaces

Test fixtures and intentionally-vulnerable sample projects shouldn't fail your build.
Pass glob patterns (matched against each workspace's source-relative path) to exclude
them:

=== "CLI"

    ```console
    $ dagger -m uv call --source . audit --exclude '**/tests/_packages/**'
    ```

=== "Python SDK"

    ```python
    await dag.uv(source=src).audit(exclude=["**/tests/_packages/**"])
    ```

## Auditing a single workspace

To audit just one workspace, select it by path first. The workspace figures out which
`uv` version to run from its own `required-version`, so you normally don't pass anything:

=== "CLI"

    ```console
    $ dagger -m uv call --source . workspace --path services/api audit run
    ```

=== "Python SDK"

    ```python
    await dag.uv(source=src).workspace(path="services/api").audit().run()
    ```
