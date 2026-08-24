---
icon: lucide/rocket
title: Overview
description: A Dagger module for linting Python dependency issues with deptry.
---

# deptry

A [Dagger](https://dagger.io) module for linting Python dependency issues with
[deptry](https://deptry.com) — it finds unused, missing, misplaced, and
transitive dependencies.

This check runs `deptry` for each `pyproject.toml` discovered in the source tree, in parallel.

## Installation

```console
$ dagger toolchain install github.com/typesafe-ai/daggerverse/deptry
```

## Usage

Run the check over the current source tree:

```console
$ dagger check deptry
```

the check runs `deptry` over projects that either:

- have a `src/` layout matching `[project].name`
- have a directory matching `[project].name`
- declare a `[tool.deptry]` section in their `pyproject.toml`

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/deptry)
