# zensical

Build [Zensical](https://zensical.org) docs sites in Dagger.

Every directory with a `zensical.toml` is a docs site. Each site builds in the
environment of the uv workspace (`uv.lock`) that holds it, using the
[`uv`](../uv/index.md) module. Docs plugins that import your code, such as an
API reference, therefore find it installed. If the docs directory is a
workspace member, that member's package is installed; otherwise it's the
workspace root's.

## Installation

```sh
dagger install github.com/typesafe-ai/daggerverse/zensical
```

Each site is its own check, and the checks run in parallel:

```sh
dagger check --zensical
```

## Configuration

Set the module's settings in `dagger.toml`:

```toml
[modules.zensical.settings]
# uv dependency groups to install. They must provide `zensical`. Defaults to ["docs"].
groups = ["docs"]
# Extras of the docs' package to install.
extras = ["api"]
# Paths to leave out of discovery.
exclude = ["**/tests/**"]
# Fail on warnings such as broken links (`zensical build --strict`). Defaults to true.
strict = true
```

`base-container` sets the container the environment is built on, for example
one that carries private index credentials.

## Build a site

`site` returns the generated site directory, for example to deploy it:

```sh
dagger call zensical projects get --key=docs site export --path=./site
```

A build reads the `zensical.toml`, its `docs_dir` (`docs` by default) and the
theme's `custom_dir`, if set.
