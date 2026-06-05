# uv

A Dagger module for [uv](https://github.com/astral-sh/uv)-managed Python projects and workspaces.

## Features

- **Audit** — run `uv audit --frozen` across every workspace in the source tree (one per `uv.lock`), in parallel. Usable as a Dagger check; each workspace's vulnerability report shows up as its own trace span.
- **Workspace installation** — build a minimal container with a package's locked dependencies installed. Parses `uv.lock` to resolve local workspace members, installs remote deps first (for better caching), then scaffolds and installs local packages. Takes the same arguments as `uv sync` would take.
- **Venv building & export** — create a (relocatable) virtual environment with `uv venv`, install/pin a managed Python, then export the venv together with the uv-managed Python it links against — so it runs in a *fresh container that has no Python of its own*.

## Install

As a Dagger module:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
```

As a Dagger toolchain:

```console
$ dagger toolchain install github.com/typesafe-ai/daggerverse/uv
```

this automatically makes `uv:audit` and other checks available.

## Examples (Python SDK)

Audit every workspace's locked dependencies:

```python
from dagger import dag

await dag.uv(source=src).audit()
```

Build a minimal container with a package's dependencies installed:

```python
ctr = (
    dag.uv(source=src)
    .workspace(path="my-workspace")       # pick the workspace at ./my-workspace
    .install(package=["my-app"])    # remote deps + transitively-needed local members
)
```

Build a relocatable venv and drop it (plus its uv-managed Python) into a fresh
image that has no Python — `copy_venv` brings the interpreter with it:

```python
ctr = (
    dag.uv(source=src)
    .workspace()
    .build(package=["my-app"])
    .with_venv(relocatable=True)
    .with_remote_dependencies()
    .copy_venv(dag.container().from_("debian:bookworm-slim"), set_env_vars=True)
)
```

For fine-grained control, drive the pipeline step by step instead of `install`:
`build` → `with_remote_dependencies` → `with_workspace_files` → `with_local_dependencies`.
