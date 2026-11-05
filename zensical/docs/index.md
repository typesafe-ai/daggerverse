# zensical

Build [Zensical](https://zensical.org) docs sites in Dagger.

Every directory with a `zensical.toml` is a docs site. A site builds in the
environment of the uv workspace (`uv.lock`) that contains it, which the
[`uv`](https://daggerverse.docs.typesafe.ai/uv/) module sets up. If the docs
directory is a workspace member, that member's package is installed; otherwise
the workspace root's package is. Docs plugins that import your code, such as an
API reference, can then import it.

The build copies in only the `zensical.toml`, its `docs_dir` (`docs` by
default) and the theme's `custom_dir`, if set.

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
