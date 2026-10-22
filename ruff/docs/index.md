---
icon: lucide/rocket
title: Overview
description: A Dagger module for linting and formatting Python code with Ruff.
---

# ruff

A [Dagger](https://dagger.io) module for linting and formatting Python code with
[Ruff](https://docs.astral.sh/ruff/).

It has two sub-objects, a checker (`ruff check`) and a formatter (`ruff format`).
Each has a `lint` check and a `fix` function that returns a `Changeset`.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/ruff
```

## Usage

### Checks

Run the lint and format checks:

```console
$ dagger check ruff:check
$ dagger check ruff:format
```

### Auto-fix

```console
$ dagger call ruff check fix
$ dagger call ruff format fix
```

## `ruff` version detection

The module reads the ruff version from the first of these files in the workspace root that sets one:

1. `uv.lock`: the pinned package version
2. `ruff.toml` / `.ruff.toml`: `required-version`
3. `pyproject.toml`: `[tool.ruff].required-version`

You can override this with `--version` or supply your own `--ctr`.

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/ruff)
