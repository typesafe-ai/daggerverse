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

the check currently only supports `src/` and flat project layouts. For flat layouts it uses `pyproject.toml` to determine the project name.

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/deptry)
