---
icon: lucide/package
title: Building containers
description: Build a minimal container for a package in a uv monorepo, assembling its local workspace dependencies from uv.lock in a cache-friendly order.
---

# Building containers

This is the module's core feature: turning one package in a `uv` monorepo into a small,
ready-to-run container. The hard part of building inside a workspace is **assembling the
context** — a package usually depends on sibling packages by path, and those local
dependencies must be present and installed in the right order. The module reads
`uv.lock`, works out exactly which local members the target needs, makes them available,
and installs everything in a cache-friendly order — so you don't hand-curate the build
context yourself.

!!! note
    Workspace members that declare no build system (also known as applications) are supported as well.


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

## `install` — the one-call path

`install` builds a container with everything a package needs:

=== "CLI"

    ```console
    $ dagger -m uv call --source . workspace install --package my-app
    ```

=== "Python SDK"

    ```python
    ctr = dag.uv(source=src).workspace().install(package=["my-app"])
    ```

What happens under the hood — each step is its own layer, ordered most-stable to
most-volatile so the expensive work is cached across builds:

1. The workspace's `uv.lock` is parsed to find the **local** packages your target
   transitively depends on.
2. **Remote** (third-party) dependencies are installed first. They change rarely, so
   this layer caches well.
3. The needed **local** members are scaffolded as stubs (their `pyproject.toml` plus an
   empty module) and installed with `uv sync`. `uv` installs workspace members as
   *editable* by default, so this only records path links — it depends on the packages'
   metadata, not their code, and stays cached when you only change source.
4. The real source is copied in **last**, on top of the stubs. Because the editable
   installs already point at these paths, the code goes live with no re-sync — so a
   source-only change invalidates just this thin final layer, not the install above it.

If you omit a package, the module mirrors a bare `uv sync` and installs the **current
package** — the one declared at the workspace root. To install every member of the
workspace instead, ask for all packages.

!!! note
    By default there is no `.venv` created and the system environment is used instead.
    Set `venv=True` to create a `.venv` in the workspace root.
    Also, see [virtual environments](./virtual-environments.md) to learn how to produce a virtual environment
    for multi-staged builds with this module.

### Choosing a base image

If you don't provide a base container, the module starts from a Debian-based `uv` image
pinned to the workspace's `uv` version, and `uv` provisions a managed Python on demand.
Provide your own base when you need system packages, private registry auth, or a
specific platform — for example a musl/Alpine base. Whatever you pass determines the
platform and libc of the resulting environment.

## The pipeline — when you need control

`install` is a convenience wrapper. When you need to do something *between* the steps, drive the pipeline yourself. `build` prepares the build without
installing anything and hands back a `UvWorkspaceBuild`; you then call the steps in
order:

```python
b = dag.uv(source=src).workspace().build(package=["my-app"])
b = b.with_remote_dependencies()      # uv sync --no-install-local
# ... run your own step here, e.g. `pulumi install` ...
b = b.with_workspace_files()          # scaffold local package stubs
ctr = b.with_local_dependencies()     # editable-install from stubs, then copy real source last
```

Each step returns a new `UvWorkspaceBuild` (or, at the end, a `Container`), so the
chain reads top to bottom. You can also swap in a different container mid-pipeline
(for instance after installing OS packages) and keep the same resolved plan.

## Dagger modules as dependencies

If a package you're building is itself a Dagger module (it has a `dagger.json`), the
module runs Dagger codegen and overlays the generated SDK before installing, so the
generated `dagger-io` package is present even when the SDK directory is gitignored in
CI. This is on by default and is a no-op for non-Dagger projects.
