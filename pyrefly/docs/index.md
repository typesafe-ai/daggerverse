---
icon: lucide/rocket
title: Overview
description: A Dagger module that type checks Python with Pyrefly, one check per uv workspace.
---

# pyrefly

A [Dagger](https://dagger.io) module that type checks Python with
[Pyrefly](https://pyrefly.org).

It runs over the uv workspaces that install `pyrefly`, each in its own
environment, with the pyrefly version its `uv.lock` pins. This module uses the
[`uv`](https://daggerverse.docs.typesafe.ai/uv/) module to build the Python
environment for Pyrefly.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/pyrefly
```

!!! hint

    The module discovers workspaces on its own. It runs over every uv workspace
    that installs `pyrefly` and skips the rest, so a workspace that already uses
    pyrefly needs no setup.

## Usage

Every workspace gets its own check, called `check`. The checks run in parallel:

```console
$ dagger check --pyrefly
```

`check` runs `pyrefly check` from the workspace root, so pyrefly picks up the
workspace's own configuration (`pyrefly.toml` or `[tool.pyrefly]`). It fails on
any type error.

Check a single workspace by its path (see
[Select collection items](https://docs.dagger.io/using/checking/#select-collection-items)):

```console
$ dagger check --pyrefly --pyrefly-workspace=services/api
```

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

Run it again after fixing errors to drop their entries from the baseline.

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
