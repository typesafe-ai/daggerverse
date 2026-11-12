---
icon: lucide/shield-check
title: Auditing Dependencies
description: Audit the locked dependencies of every uv workspace in a source tree for known vulnerabilities, and upgrade the vulnerable ones.
---

# Auditing Dependencies

The `uv` module provides an `audit` check that runs `uv audit` to find vulnerabilities in your Python dependencies. It's also a Dagger [generator](https://docs.dagger.io/using/generating/) which makes it able to automatically suggest upgrades for vulnerable dependencies, with respect to configured [upgrade strategy](#configuration-options).

## Running the `audit` generator

Install the module into your workspace, then check every workspace, or apply the fixes:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
$ dagger check --generated --uv
$ dagger generate --uv
```

!!! note "Working with Workspaces"
    The workspaces form a [collection](https://docs.dagger.io/reference/sdks/python/#collections),
    `workspaces`, keyed by each workspace's path, so each one runs as its own generator.
    List them, or [select one](https://docs.dagger.io/using/checking/#select-collection-items)
    by its path:

    ```console
    $ dagger generate -l -a --uv
    $ dagger generate --uv --uv-workspace-source=services/api
    ```

## What `audit` does

For each workspace, `audit` runs [`uv audit --frozen`](https://docs.astral.sh/uv/reference/cli/#uv-audit) against the committed `uv.lock`,
with vulnerability data from the public OSV service. That data changes under an unchanged
`uv.lock`, so a result is cached for an hour at most.

For every vulnerable package that has a fixed release, `audit` attempts to re-lock it with
[`uv lock --upgrade-package`](https://docs.astral.sh/uv/concepts/projects/sync/#upgrading-locked-package-versions). This may result in one of the following outcomes:

- **Fixed:** `audit` changes `uv.lock`, so `dagger check --generated` reports the
  workspace as stale until `dagger generate` applies the upgrade.
- **Partially fixed:** `audit` returns the fixes it could make and logs the rest. Once you
  apply them, the next run fails on what's left.
- **Not fixable:** there is no compatible release available (for example, a strict constraint such as `urllib3==2.6.3` rules
  it out, or the cooldown setting forbids it). In this case `audit` fails with uv's error message.

### Cooldown exemptions

A cooldown ([`exclude-newer`](https://docs.astral.sh/uv/reference/settings/#exclude-newer), such as `"7 days"`) keeps freshly published releases
out of the lock, so it can hold back a fix. With `auditCooldown` set to `EXEMPT`, `audit`
admits just the fixing version by writing a marked
[`exclude-newer-package`](https://docs.astral.sh/uv/reference/settings/#exclude-newer-package) entry, to `uv.toml` if the workspace has
one, otherwise to `pyproject.toml`:

```toml title="pyproject.toml"
[tool.uv]
exclude-newer = "7 days"

[tool.uv.exclude-newer-package]
urllib3 = "2026-09-15T19:29:37Z" # UvAudit[dagger]: security fix exempt from cooldown
```

The check will automatically cleanup expired `exclude-newer-package` entries at first convenience.

## Configuration options

| Option | Set on | Default | What it does |
| --- | --- | --- | --- |
| `exclude` | module setting | none | Glob patterns of workspace paths to leave out of discovery: no `audit`, and `workspace` can't load them. |
| `upgradeStrategy` | module setting | `LATEST` | How far a vulnerable package moves: `LATEST` (as far as constraints and cooldown allow), `MINIMAL` (the lowest version fixing every advisory), or `SKIP` (no upgrade, report only). |
| `auditCooldown` | module setting | `RESPECT` | When the cooldown holds back a fix: `RESPECT` it, or `EXEMPT` the fixing version. |
| `auditConcurrency` | module setting | `10` | How many `uv lock` runs `audit` has in flight at once in each workspace. |
| `auditExclude` | module setting | none | Glob patterns of workspace paths to leave unaudited. They stay buildable. |
| `auditBaseContainer` | module setting | Debian uv image | Container with uv on `PATH` to audit and relock in, e.g. one carrying private index [credentials](https://docs.astral.sh/uv/concepts/indexes/#authentication). |
| `uv_version` | `audit` argument | the workspace's | uv version of the default image. |

Set them in `dagger.toml`:

```toml title="dagger.toml"
[modules.uv.settings]
exclude = ["**/tests/_packages/**"]
upgradeStrategy = '"MINIMAL"'
auditCooldown = '"EXEMPT"'
```

!!! warning "Setting values are JSON"

    String and enum values are parsed as JSON, so they're quoted twice: `'"MINIMAL"'`,
    not `"MINIMAL"`. A `dag://` reference to another module's function is not.

!!! tip "Private indexes"

    When working with a private index, [wire](https://docs.dagger.io/config/module-wiring)
    a container carrying its
    [credentials](https://docs.astral.sh/uv/concepts/indexes/#providing-credentials-directly)
    into `auditBaseContainer`, here from a function `base` on your own module `ci`:

    ```toml title="dagger.toml"
    [modules.uv.settings]
    auditBaseContainer = "dag://ci/base"
    ```

    Without credentials, set `upgradeStrategy` to `SKIP`: `audit` then never relocks, and
    only fails on vulnerabilities:

    ```toml title="dagger.toml"
    [modules.uv.settings]
    upgradeStrategy = '"SKIP"'
    ```
