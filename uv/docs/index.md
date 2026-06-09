---
icon: lucide/rocket
title: Overview
description: A Dagger module for uv-managed Python monorepos — build a package with its local workspace dependencies, package it as a relocatable venv, and audit locked dependencies.
---

# uv

A [Dagger](https://dagger.io) module for [`uv`](https://github.com/astral-sh/uv)-managed
Python projects and workspaces.

Its main job is **building a package out of a `uv` monorepo**. In a workspace, building
one package means assembling the right context first: its local sibling dependencies
have to be present and installed in the right order. This module reads your `uv.lock`,
works out exactly which local workspace members the target needs, and prepares that
build context for you — so a single call turns a package buried in a monorepo into a
minimal, ready-to-run container.

On top of that it can:

- **Package** a project as a relocatable virtual environment that runs in a fresh
  container with no Python of its own.
- **Audit** the locked dependencies of every workspace in a source tree.

!!! info "Each workspace knows its own `uv` version"

    Knowing the required `uv` version is a property of how this module represents a
    workspace: it reads `required-version` (from `uv.toml`, or the `[tool.uv]` table of
    `pyproject.toml`) and resolves it to a concrete image tag. That version is then used
    wherever an image is needed — the `audit` image and the default build base — so the
    tooling matches what the project declares. You can always override the version or
    the full image.

## Installation

Add it as a dependency of your own Dagger module:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
```

Or install it as a toolchain, which makes its checks (such as `uv:audit`) available
directly:

```console
$ dagger toolchain install github.com/typesafe-ai/daggerverse/uv
```

## Quickstart

=== "CLI"

    Build a minimal container with a package and its local dependencies installed:

    ```console
    $ dagger call uv workspace install --package my-app
    ```

    Audit every workspace in the current directory:

    ```console
    $ dagger check uv:audit
    ```

=== "Python SDK"

    ```python
    from dagger import dag

    # Build a minimal container with a package and its local dependencies installed.
    ctr = dag.uv(source=src).workspace().install(package=["my-app"])

    # Audit every workspace's locked dependencies.
    await dag.uv(source=src).audit()
    ```

??? abstract "The mental model"

    ```mermaid
    graph LR
      Uv["Uv<br/><i>(your source tree)</i>"] -->|workspace / get_workspaces| WS["UvWorkspaceSource<br/><i>(one per uv.lock)</i>"]
      WS -->|audit| A["Audit"]
      WS -->|build| B["UvWorkspaceBuild<br/><i>(container + sync plan)</i>"]
      WS -->|install| C1["Container"]
      B -->|with_local_dependencies / copy_venv| C2["Container"]
      B -->|venv| V["UvVenv<br/><i>(venv + its Python)</i>"]
    ```

    - **`Uv`** holds only your source directory. Ask it for a single workspace by path
      (`workspace`) or for every workspace it can find (`get_workspaces`), and run the
      aggregate `audit` check across all of them.
    - **`UvWorkspaceSource`** is one `uv` workspace (the files rooted at a `uv.lock`). From it
      you can read the required `uv` version, `audit` it, `build` a container step by step, or
      `install` everything in one call.
    - **`UvWorkspaceBuild`** is an in-progress build: a container plus the resolved sync
      plan. It exposes the individual pipeline steps so you can splice your own work in
      between them, and can export the result as a container or a [`UvVenv`](virtual-environments.md).

    You don't have to learn every type up front — the convenience methods (`audit`,
    `install`) cover the common cases, and you reach for the pipeline only when you need
    fine-grained control.

## Where to go next

- [Building containers](building.md) — assemble a monorepo package's build context and install it.
- [Virtual environments](virtual-environments.md) — export a portable, relocatable venv.
- [Auditing dependencies](checks/audit.md) — scan locked dependencies for known vulnerabilities.

!!! note "API reference"

    This site is a tutorial. The exact functions, arguments, and types are published
    as generated reference documentation on the Daggerverse.
