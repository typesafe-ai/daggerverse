---
icon: lucide/package
title: Building containers
description: Build a minimal container for a package in a uv monorepo, assembling its local workspace dependencies from uv.lock in a cache-friendly order.
---

# Building containers

The module builds one package from a `uv` monorepo into a container with minimal required dependencies. Inside a
workspace, a package usually depends on sibling packages by path, and those local
dependencies must be present and installed in the right order. The module reads
`uv.lock`, works out which local members the target needs, stages them, and installs
everything in an order that caches well, so you don't assemble the build context by hand.

!!! note
    Dependency-only workspace members are supported too, including projects with
    no build system and those with `tool.uv.package = false`.


??? abstract "The mental model"

    ```mermaid
    graph LR
      Uv["Uv<br/><i>(your source tree)</i>"] -->|workspace / workspaces| WS["UvWorkspaceSource<br/><i>(one per uv.lock)</i>"]
      WS -->|build| B["UvWorkspaceBuild<br/><i>(container + Python environment + sync plan)</i>"]
      B -->|with_dependencies / with_*| B
      B -->|container / copy_venv| C["Container"]
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
      step returns the build, and `container` or [`venv`](virtual-environments.md)
      finishes it.

## Installing everything: `with_dependencies`

`build` prepares the build, `with_dependencies` installs everything a package needs,
and `container` returns the result:

=== "CLI"

    ```console
    $ dagger call uv workspace --path . build with-dependencies container
    ```

=== "Python SDK"

    ```python
    ctr = uv(ws).workspace(".").build().with_dependencies().container()
    ```

Under the hood this is equivalent to:

```python
ctr = (
    uv(ws)
    .workspace(".")
    .build()
    .with_remote_dependencies()  # uv sync --no-install-local
    .with_workspace_files()  # scaffold local package stubs
    .with_local_dependencies()  # editable-install from stubs, then copy real source
    .container()
)
```

Each step is its own layer. The layers go from most stable to most volatile, so the
expensive work stays cached across builds:

1. The module parses the workspace's `uv.lock` to find the **local** packages your target
   depends on, directly or transitively.
2. The **Python environment** is prepared before `uv.lock` is copied in: a uv-managed
   Python, or a `.venv` (see the warning below). It changes only with the Python version.
3. **Remote** (third-party) dependencies are installed next. They change rarely, so
   this layer caches well. If the cache has grown past a configurable size,
   `uv cache prune --ci` runs afterwards.
4. The needed **local** members are scaffolded as stubs (their `pyproject.toml`, empty
   modules, and the [files their build backend reads](#supported-build-backends), such as
   the readme, license files, and version file) and installed with `uv sync`. `uv`
   installs workspace members as *editable* by default, so this step only records path
   links. It depends on the packages' metadata, not their code, and stays cached when
   only source changes.
5. The real source is copied in **last**, on top of the stubs. The editable installs
   already point at these paths, so the code goes live without a re-sync, and a
   source-only change invalidates only this final layer. Symlinks in the copied
   source that point elsewhere in the source tree, such as modules shared from a
   sibling directory, are replaced with the content they point to.

!!! warning "Set `UV_PROJECT_ENVIRONMENT` to build venv-free images"
    If your base ships a Python, point `UV_PROJECT_ENVIRONMENT` at its prefix:

    ```python
    base = core().container().from_("python:3.13.7-slim").with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
    ctr = uv(ws).workspace(".").build(base_container=base).with_dependencies().container()
    ```

### Choosing a base image

If you don't provide a base container, the module starts from a Debian-based `uv` image
pinned to the workspace's `uv` version, and `uv` provisions a managed Python on demand.
Provide your own base when you need system packages, private registry auth, or a
specific platform, such as a musl/Alpine base. The base you pass determines the
platform and libc of the resulting environment.

!!! tip
    If your custom base doesn't ship `uv`, the module installs it from the official image (`auto_install_uv=True` by default). You can also call `with_uv` yourself in the pipeline.

### Caching

By default, the module mounts a Dagger cache volume at `/mnt/cache/uv` and sets
`UV_CACHE_DIR` to point there. Python downloads and package downloads are both cached
across builds, so a second `uv sync` or `uv python install` for the same versions skips
the network.

If your `base_container` already sets `UV_CACHE_DIR` and doesn't have a directory there, the module mounts the cache volume at that path instead of the default.

The module automatically runs `uv cache prune --ci` when the cache size exceeds a certain threshold (100 GiB by default).

A workspace's cache key depends on where it comes from: the current workspace
gets a new one on every local run (and every commit remotely), while one made
with `directory.as_workspace()` is keyed by the directory's contents. So
`workspace` and `workspaces` load each uv workspace from a workspace made of
just the files its builds read: manifests, members, their path dependencies,
and the build backend handlers' build inputs. Its builds stay cached until one
of those files changes.

A module that reads more of the workspace than `uv` does can merge its own
files into `inputs()` and hand the result on with `as_workspace()`, keeping its
own functions cached the same way.

## Supported build backends

The module supports Python build backends such as `uv_build`, `hatchling`, `pdm-backend`, `flit_core`, or `poetry-core`.
See the full list [here](build-backends/index.md).

The module parses their native configuration files and copies files required to build the package before installing it, ensuring optimal caching.

??? note "Build timestamps"
    Unless your `base_container` sets [`SOURCE_DATE_EPOCH`](https://reproducible-builds.org/docs/source-date-epoch/), the module sets it to 1980-01-01 to avoid timestamps that wheels can't store.

!!! warning "Unsupported backends"
    Packages using other backends are not explicitly supported, and builds for them
    will only work with the standard `src/` layout, with package being named after the distribution.

!!! warning "Non-editable installs"
    With `no_editable=True`, changes to source code trigger a rebuild of the package.
    Third-party dependencies remain cached.

## Running the pipeline step by step

`with_dependencies` runs three steps. To run something *between* them, call them
yourself in order:

```python
b = uv(ws).workspace(".").build(package=["my-app"])
b = b.with_remote_dependencies()  # uv sync --no-install-local
# ... run your own step here, e.g. `pulumi install` ...
b = b.with_workspace_files()  # scaffold local package stubs
ctr = b.with_local_dependencies().container()  # editable-install from stubs, then copy real source last
```

Each step returns a new `UvWorkspaceBuild`, so the chain reads top to bottom. You can also swap in a different container mid-pipeline
(for instance after installing OS packages) and keep the same resolved plan.
