---
icon: lucide/rocket
title: Overview
description: A Dagger module that type checks Python with Pyrefly, one generator per uv workspace.
---

# pyrefly

A [Dagger](https://dagger.io) module that type checks Python with
[Pyrefly](https://pyrefly.org).

Every uv workspace (`uv.lock`) that locks `pyrefly` is type checked in its own
environment, with the pyrefly version the workspace locks. The
[`uv`](https://daggerverse.docs.typesafe.ai/uv/) module builds that environment
with every workspace member installed, so pyrefly resolves their imports and
those of their dependencies. A workspace that doesn't lock `pyrefly` is left alone.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/pyrefly
```

Add `pyrefly` to a dependency group the environment installs, `dev` by default:

```console
$ uv add --dev pyrefly
```

## Usage

Each workspace is its own [generator](https://docs.dagger.io/reference/sdks/python/#generators),
and they run in parallel:

```console
$ dagger generate --pyrefly              # apply pyrefly's fixes
$ dagger check --generated --pyrefly     # fail on pending fixes and on type errors
```

`check` runs `pyrefly check` at the workspace root, so pyrefly reads the
workspace's own configuration (`pyrefly.toml` or `[tool.pyrefly]`). It fails
when type errors remain after the fixes.

## Fixes

| Setting | Default | Fix |
|---------|---------|-----|
| `removeUnusedIgnores` | `true` | Remove the ignore comments that suppress nothing (`--remove-unused-ignores`). |
| `suppressErrors` | `false` | Add an ignore comment for each type error (`--suppress-errors`) instead of failing on it. |
| `pruneBaseline` | `true` | Drop the baseline entries that match no error anymore (`--prune-baseline`), in workspaces that set a `baseline`. |

## Adopting pyrefly in an existing codebase

A [baseline](https://pyrefly.org/en/docs/error-suppressions/#baseline-files-experimental)
records the errors a codebase already has, so `check` fails only on new ones.
Set the file in the workspace's pyrefly configuration:

```toml title="pyproject.toml"
[tool.pyrefly]
baseline = "pyrefly-baseline.json"
```

Then record the errors, in the same environment `check` runs in:

```console
$ dagger -c 'pyrefly | workspaces | get . | update-baseline | export --path .'
```

`check` keeps the baseline pruned as the errors get fixed.

## Configuration

Set the module's settings in `dagger.toml`:

```toml title="dagger.toml"
[modules.pyrefly.settings]
# Workspace paths to leave out of workspaces.
exclude = ["**/tests/_packages/**"]
# uv dependency groups to install on top of the default ones.
groups = ["typing"]
# Every extra is installed by default. Turn that off to install only `extras`.
allExtras = false
extras = ["api"]
# Packages to leave out of the environment. Their imports don't resolve.
noInstallPackages = ["my-gpu-kernels"]
# The fixes `check` applies.
removeUnusedIgnores = true
suppressErrors = false
pruneBaseline = true
# Additional arguments to pass to `pyrefly check`.
args = ["--min-severity", "warn"]
```

The `baseContainer` setting is the container the environment is built on, for
example one that carries private index credentials.

## Caching

Pyrefly keeps no cache on disk: every run checks the workspace from scratch.
What is cached is the environment. The `uv` module installs it from its
`uv-cache` volume, and its layers are reused until a dependency manifest
changes, so a source edit only reruns `pyrefly check`. Set `uvCache` to share
another cache volume.
