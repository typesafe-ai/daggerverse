# zensical

Build [Zensical](https://zensical.org) docs sites in Dagger.

Docs sites are discovered by looking for `zensical.toml` in the repository.

Uses the [`uv`](https://daggerverse.docs.typesafe.ai/uv/) module to set up the environment.

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

```toml title="dagger.toml"
[modules.zensical.settings]
# uv dependency groups to install. They must provide `zensical`. Defaults to ["docs"].
groups = ["docs"]
# Extras of the docs' package to install.
extras = ["api"]
# Workspace paths never uploaded to the Dagger engine. Defaults to ["**/.git"].
exclude = ["**/.git", "**/tests/**"]
# Fail on warnings such as broken links (`zensical build --strict`). Defaults to true.
strict = true
```

The `base-container` option sets the container the environment is built on, for example
one that carries private index credentials.

## Build a site

`site` returns the generated site directory, for example to deploy it:

```sh
dagger call zensical projects get --key=docs site export --path=./site
```

## Caching

zensical's build cache (`.cache` next to the config) lives in a cache volume,
one per site, so rebuilds reuse it. Pass `--clean` to `site` to clear the cache
before building.
