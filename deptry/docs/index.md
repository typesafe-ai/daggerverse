---
icon: lucide/rocket
title: Overview
description: A Dagger module for linting Python dependency issues with deptry.
---

# deptry

A [Dagger](https://dagger.io) module for linting Python dependency issues with
[deptry](https://deptry.com). It finds unused, missing, misplaced, and
transitive dependencies.

`projects` is a [collection](https://docs.dagger.io/reference/sdks/python/#collections)
of the Python projects in the source tree, keyed by path. `check` runs `deptry` on
each one, so every project is its own check.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/deptry
```

## Usage

Run the checks over the current source tree:

```console
$ dagger check --deptry
```

A project is checked when its `pyproject.toml` declares dependencies and it
either:

- has a `src/` layout matching `[project].name`
- has a directory matching `[project].name`
- declares a `[tool.deptry]` section in its `pyproject.toml`

Check a single project by its path (see
[Select collection items](https://docs.dagger.io/using/checking/#select-collection-items)):

```console
$ dagger check --deptry --deptry-project=services/api
```

### Skipping projects

The `exclude` setting takes glob patterns, matched against each project's
source-relative path, and leaves matching projects out of `projects`:

```toml title="dagger.toml"
[modules.deptry.settings]
exclude = ["**/tests/_packages/**"]
```

By default, the module picks up `deptry` version per `uv` workspace, as pinned in `uv.lock`.
It reads it from the `dev` group of the workspace's root project (`uv add --dev deptry`),
or from the group the `group` setting names. A project whose lock has no deptry in that
group fails its check, as does a project without a `uv.lock`.

### Configuring deptry

`dagger check` runs checks with their default arguments, so deptry's options are
constructor settings too: `group` picks the dependency group deptry's version comes
from, `args` adds arguments before the project root, and `base_container` is the
container deptry is installed into, which only needs `uv`. String settings are JSON,
so they need inner quotes:

```toml title="dagger.toml"
[modules.deptry.settings]
group = '"lint"'
args = ["--no-ansi"]
```

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/deptry)
