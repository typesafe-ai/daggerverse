# deptry

Dagger module for [deptry](https://github.com/fpgmaas/deptry) — a linter for Python dependency issues.

## Installation

Install as a [toolchain](https://docs.dagger.io/core-concepts/checks#checks-from-toolchains) to add `deptry:check` to `dagger check`:

```sh
dagger toolchain install github.com/typesafe-ai/daggerverse/deptry
```

## Usage

Check the current project:

```sh
dagger check deptry
```

Learn more in [docs](https://daggerverse.docs.typesafe.ai/deptry).
