---
icon: lucide/rocket
title: Overview
description: A Dagger module for uv-managed Python monorepos. Build a package with its local workspace dependencies, package it as a relocatable venv, and audit locked dependencies.
---

# uv

A [Dagger](https://dagger.io) module for [`uv`](https://github.com/astral-sh/uv)-managed
Python projects and workspaces.

!!! note "API reference"

    This site is a tutorial. The exact functions, arguments, and types are published
    as generated reference documentation on the
    [Daggerverse](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/uv).

The module's main job is building selected packages from a `uv` workspace.
A workspace package usually depends on sibling packages by path, and those have to be
present and installed in the right order before the package itself can be installed.

The module reads your `uv.lock`, works out which local members the target needs, and
stages them, so one call turns a monorepo package into a minimal container.
It also understands and handles native configuration for the
[supported build backends](build-backends/index.md).

It can also:

- **Package** a project as a relocatable virtual environment that runs in a fresh
  container with no Python of its own.
- **Audit** the locked dependencies of every workspace in a source tree.

!!! info "Each workspace knows its own `uv` version"

    Each workspace reads its `required-version` (from `uv.toml`, or the `[tool.uv]` table
    of `pyproject.toml`) and resolves it to a concrete image tag. The `audit` image and the
    default build base both use that version, so the tooling matches what the project
    declares. You can override the version or pass your own image.

## Installation

Install it into your workspace, which makes its checks (one `audit` per uv workspace)
available to `dagger check`:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
```

Or add it as a client of your own Dagger module:

```console
$ dagger module client add github.com/typesafe-ai/daggerverse/uv
```

## Quickstart

=== "CLI"

    Build a minimal container with a package and its local dependencies installed:

    ```console
    $ dagger call uv workspace --path . build --package my-app with-dependencies container
    ```

    Audit every workspace in the source tree:

    ```console
    $ dagger check --uv
    ```

=== "Python SDK"

    ```python
    from dagger_clients.uv import uv

    # Build a minimal container with a package and its local dependencies installed.
    ctr = uv(ws).workspace(".").build(package=["my-app"]).with_dependencies().container()
    ```

    `ws` is the Dagger workspace the module reads. In your own module, take
    `ws: Workspace` as an argument and Dagger fills it in. To read a directory
    instead, pass `directory.as_workspace()`.

??? abstract "The mental model"

    ```mermaid
    graph LR
      Uv["Uv<br/><i>(your source tree)</i>"] -->|workspace / workspaces| WS["UvWorkspaceSource<br/><i>(one per uv.lock)</i>"]
      WS -->|build| B["UvWorkspaceBuild<br/><i>(container + Python environment + sync plan)</i>"]
      B -->|with_dependencies / with_*| B
      B -->|container / copy_venv| C["Container"]
      B -->|dist| D["Directory<br/><i>(sdists and wheels)</i>"]
      B -->|venv| V["UvVenv<br/><i>(venv + its Python)</i>"]
    ```

    - **`Uv`** holds the source tree it reads from the Dagger workspace (`ws`). Ask it
      for one workspace by path (`workspace`) or for the collection of every workspace
      it finds (`workspaces`).
    - **`UvWorkspaceSource`** is one `uv` workspace (the files rooted at a `uv.lock`). From it
      you can read the required `uv` version, `audit` it (a check), or `build` a container.
    - **`UvWorkspaceBuild`** is an in-progress build: a container with its Python
      environment ready, plus the resolved sync plan. `with_dependencies` installs
      everything; its individual steps let you run your own steps between them. Every
      step returns the build, and `container`, [`venv`](virtual-environments.md) or
      `dist` (sdists and wheels) finishes it.

## Where to go next

- [Building containers](building.md) — assemble a monorepo package's build context and install it.
- [Virtual environments](virtual-environments.md) — export a portable, relocatable venv.
- [Auditing dependencies](checks/audit.md) — scan locked dependencies for known vulnerabilities.
- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/uv)
