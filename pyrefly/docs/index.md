---
icon: lucide/rocket
title: Overview
description: A Dagger module that type checks Python with Pyrefly, one generator per uv workspace.
---

# pyrefly

A [Dagger](https://dagger.io) module that type checks Python with
[Pyrefly](https://pyrefly.org).

The module checks each uv workspace (a directory with a `uv.lock`) that locks
`pyrefly`, and skips the ones that don't. Each workspace is checked in its own
environment, using the pyrefly version it locks. The
[`uv`](https://daggerverse.docs.typesafe.ai/uv/) module builds that environment
and installs every workspace member into it, so pyrefly can resolve imports of
the members and of their dependencies.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/pyrefly
```

Then add `pyrefly` to each workspace you want checked. It has to be in a
dependency group the environment installs, and `dev` is installed by default:

```console
$ uv add --dev pyrefly
```

## Usage

Every workspace gets its own [generator](https://docs.dagger.io/reference/sdks/python/#generators),
called `check`. The generators run in parallel:

```console
$ dagger generate --pyrefly              # apply pyrefly's fixes
$ dagger check --generated --pyrefly     # fail on pending fixes and on type errors
```

`check` runs `pyrefly check` from the workspace root, so pyrefly picks up the
workspace's own configuration (`pyrefly.toml` or `[tool.pyrefly]`). If type
errors are left once the fixes are applied, it fails.

## Fixes

`check` can apply three fixes, each controlled by a setting:

| Setting | Default | Fix |
|---------|---------|-----|
| `removeUnusedIgnores` | `true` | Remove the ignore comments that suppress nothing (`--remove-unused-ignores`). |
| `suppressErrors` | `false` | Add an ignore comment for each type error (`--suppress-errors`) instead of failing on it. |
| `pruneBaseline` | `true` | Drop the baseline entries that match no error anymore (`--prune-baseline`), in workspaces that set a `baseline`. |

## Adopting pyrefly in an existing codebase

To turn the check on before fixing the errors a codebase already has, use a
[baseline](https://pyrefly.org/en/docs/error-suppressions/#baseline-files-experimental).
It records those errors, and `check` then fails only on new ones. Name the
baseline file in the workspace's pyrefly configuration:

```toml title="pyproject.toml"
[tool.pyrefly]
baseline = "pyrefly-baseline.json"
```

Then record the current errors. This command runs pyrefly in the same
environment `check` uses and writes the baseline file into your working tree:

```console
$ dagger -c 'pyrefly | workspaces | get . | update-baseline | export --path .'
```

As you fix errors, `check` removes their entries from the baseline.

## Configuration

The module's settings go in `dagger.toml`:

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

`baseContainer` sets the container the environment is built on. Use it when the
build needs something the default image lacks, such as credentials for a
private index.

### Per-workspace settings

A workspace can override these settings in its own `pyproject.toml`. The keys
are the same ones in kebab-case, and any key you leave out keeps the module's
value:

```toml title="pyproject.toml"
[tool.typesafe_daggerverse.pyrefly]
# This workspace's `cpu` and `gpu` extras conflict, so it can't install every extra.
all-extras = false
extras = ["cpu"]
no-install-packages = ["my-gpu-kernels"]
```

Every setting can be overridden this way except `exclude`, `baseContainer` and
`uvCache`. Those three apply to the whole repository.

## Caching

Pyrefly has no on-disk cache, so every run checks the workspace from scratch.
The environment is cached, though. The `uv` module installs packages from its
`uv-cache` volume, and Dagger reuses the environment's layers until a
dependency manifest changes. Editing source code therefore only reruns
`pyrefly check`. To use a different cache volume, for example one shared with
another module, set `uvCache`.
