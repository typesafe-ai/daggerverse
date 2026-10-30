---
icon: lucide/rocket
title: Overview
description: A Dagger module for linting and formatting Python code with Ruff.
---

# ruff

A [Dagger](https://dagger.io) module for linting and formatting Python code with
[Ruff](https://docs.astral.sh/ruff/).

It exposes two generators: `check` (`ruff check --fix`) and `format` (`ruff format`).
`dagger generate` applies their changes, and `dagger check --generated` fails while
either would change a file. `check` also fails on violations ruff can't fix.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/ruff
```

## Usage

### Checks

Fail when the workspace isn't linted or formatted:

```console
$ dagger check --ruff --generated
```

### Auto-fix

Apply the fixes and formatting to the workspace:

```console
$ dagger generate --ruff
```

### Excluding paths

Gitignored files are never uploaded to the Dagger engine. `exclude` sets the glob
patterns of other workspace paths to leave out, replacing the default `["**/.git"]`.
Set it in `dagger.toml`:

```toml
[modules.ruff.settings]
exclude = ["**/.git", "**/tests/_fixtures/**"]
```

## `ruff` version detection

The module reads the ruff version from the first of these files in the workspace root that sets one:

1. `uv.lock`: the pinned package version
2. `ruff.toml` / `.ruff.toml`: `required-version`
3. `pyproject.toml`: `[tool.ruff].required-version`

You can override this with `--version` or supply your own `--ctr`.

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/ruff)
