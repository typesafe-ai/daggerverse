# pyrefly

Dagger module that type checks Python with [Pyrefly](https://pyrefly.org), one generator per uv workspace.

## Installation

```sh
dagger install github.com/typesafe-ai/daggerverse/pyrefly
```

## Usage

Apply the fixes pyrefly can make, then fail on the type errors that remain:

```sh
dagger generate --pyrefly
dagger check --generated --pyrefly
```

Learn more in [docs](https://daggerverse.docs.typesafe.ai/pyrefly).
