---
icon: lucide/shield-check
title: Auditing dependencies
description: Audit the locked dependencies of every uv workspace in a source tree for known vulnerabilities, and upgrade the vulnerable ones.
---

# Auditing dependencies

`audit` is a [generator](https://docs.dagger.io/using/generating/): it runs
`uv audit --frozen` against a workspace's committed `uv.lock`, relocks every vulnerable
package that has a fix with `uv lock --upgrade-package`, and returns the changes to
`uv.lock` (and, for [cooldown exemptions](#cooldown), the uv settings).

Its staleness check passes only for a clean workspace, so `dagger check --generated`
fails on a vulnerability whether or not `audit` can fix it:

- **Fixable**: `audit` would change `uv.lock`, so the workspace is stale. `dagger generate`
  applies the upgrade.
- **Not fixable** (no fixed release, a constraint like `urllib3==2.6.3` rules it out, or
  the cooldown holds it back): `audit` fails with uv's report.

When it fixes some vulnerabilities but not others, it returns the fixes and logs the rest;
the next run fails on them.

It mounts only the lockfile, `uv.toml` and the members' `pyproject.toml` files, so a
source edit doesn't re-run it. Relocking reaches the package indexes: pass a
`base_container` carrying their credentials when they're private.

## Auditing a whole source tree

`workspaces` is a [collection](https://docs.dagger.io/reference/sdks/python/#collections)
with one item per `uv.lock` in the source tree, keyed by the workspace's path.

Install the module into your workspace, then check every workspace, or apply the fixes:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
$ dagger check --generated --uv
$ dagger generate --uv
```

List the workspaces it found:

```console
$ dagger generate -l -a --uv
```

### Skipping workspaces

Test fixtures and intentionally vulnerable sample projects shouldn't fail your build.
The `exclude` setting takes glob patterns, matched against each workspace's
source-relative path, and leaves matching workspaces out of `workspaces`:

```toml title="dagger.toml"
[modules.uv.settings]
exclude = ["**/tests/_packages/**"]
```

## Update strategy

The `auditStrategy` setting sets how far a vulnerable package moves:

| `auditStrategy` | Upgrades to |
| --- | --- |
| `LATEST` (default) | The newest version the workspace's constraints and cooldown allow. |
| `MINIMAL` | The lowest version that fixes every advisory against the package. |

Only the vulnerable packages move (and whatever their new version needs); the rest
of the lock stays as it is.

## Cooldown

A cooldown (`exclude-newer`, such as `"7 days"`) keeps freshly published releases out of
the lock, so a fix released inside the window is held back. The `auditCooldown` setting
decides what happens then:

| `auditCooldown` | When the cooldown holds back a fix |
| --- | --- |
| `RESPECT` (default) | It stays held back. `audit` keeps any upgrade the cooldown allows and fails on the rest. |
| `EXEMPT` | `audit` admits the minimal fixing version, and nothing newer, past the cooldown. |

An exemption has to live in the uv settings: a lock resolved with an exemption that
only the command line knows about fails `uv lock --locked`. `audit` writes it to
`exclude-newer-package`, in `uv.toml` if the workspace has one, otherwise in
`pyproject.toml`, pinned to the fixing version's upload time and marked:

```toml title="pyproject.toml"
[tool.uv]
exclude-newer = "7 days"

[tool.uv.exclude-newer-package]
urllib3 = "2026-09-15T19:29:37Z" # uv audit: security fix exempt from cooldown
```

Once the cooldown catches up with that time, the entry would hold the package back
instead, so the next `audit` removes it. It only ever removes its own marked entries,
and leaves a package alone when you've set your own `exclude-newer-package` for it.

## Configuration

`dagger generate` and `dagger check --generated` run `audit` with its defaults, so its
policy lives in the module's settings. Each value is JSON, so a string setting is quoted twice:

```toml title="dagger.toml"
[modules.uv.settings]
auditStrategy = '"MINIMAL"'
auditCooldown = '"EXEMPT"'
```

## Auditing a single workspace

Select one workspace by its path (see
[Select collection items](https://docs.dagger.io/using/checking/#select-collection-items)):

=== "CLI"

    ```console
    $ dagger generate --uv --uv-workspace-source=services/api
    ```

=== "Python SDK"

    ```python
    changes = uv(ws, audit_cooldown=UvCooldownPolicy.EXEMPT).workspace("services/api").audit()
    await changes.export(".")
    ```
