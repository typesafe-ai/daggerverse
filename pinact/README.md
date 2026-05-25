# pinact

Dagger module for [pinact](https://github.com/suzuki-shunsuke/pinact) — pin GitHub Actions to full-length commit SHAs.

## Installation

Install as a [toolchain](https://docs.dagger.io/core-concepts/checks#checks-from-toolchains) to add `pinact:lint` to `dagger check`:

```sh
dagger toolchain install github.com/typesafe-ai/daggerverse/pinact
```

## Usage

Lint workflows (defaults to `.github` in the current directory):

```sh
dagger check pinact
```

Fix unpinned actions and apply changes to the host:

```sh
dagger check pinact fix --github-token=env:GITHUB_TOKEN
```
