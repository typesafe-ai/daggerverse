# zizmor

Dagger module for [zizmor](https://github.com/zizmorcore/zizmor) — static analysis for GitHub Actions security.

## Installation

Install as a [toolchain](https://docs.dagger.io/core-concepts/checks#checks-from-toolchains) to add `zizmor:lint` to `dagger check`:

```sh
dagger toolchain install github.com/typesafe-ai/daggerverse/zizmor
```

## Usage

Lint workflows (defaults to `.github` in the current directory):

```sh
dagger check zizmor
```

With a GitHub token for online audits:

```sh
dagger check zizmor lint --github-token=env:GITHUB_TOKEN
```

Auto-fix security issues and apply changes to the host:

```sh
dagger call zizmor fix
```
