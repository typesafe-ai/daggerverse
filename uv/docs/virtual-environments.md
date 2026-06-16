---
icon: lucide/box
title: Virtual environments
description: Package a uv project as a relocatable virtual environment that runs in a fresh container with no Python of its own.
---

# Virtual environments

Beyond building images, the module can package a project as a **relocatable virtual
environment** and drop it — together with the Python interpreter it needs — into a
*fresh container that has no Python of its own*.
This is useful for composing multiple virtual environments in a single image.

## Creating a relocatable venv

Virtual environments aren't self-contained by default: `.venv/bin/python` points back
at a base interpreter, and the venv's scripts hardcode their location. To make a venv
that survives being moved, create it with `uv venv --relocatable` *before* installing,
so the subsequent `uv sync` populates that relocatable environment:

```python
b = (
    dag.uv(source=src)
    .workspace()
    .builder(package=["my-app"], no_editable=True)
    .with_venv(relocatable=True)  # uv venv --relocatable
    .with_remote_sync()           # sync installs into the relocatable venv
)
```

`no_editable=True` (`uv sync --no-editable`) bakes your local/workspace packages'
source into `site-packages` instead of linking back to the source tree. It's what
makes the venv truly self-contained — see the note below before exporting.

## Exporting and copying it

A venv still needs its interpreter. `copy_venv` bundles the relocatable venv **and the
`uv`-managed Python it links against**, and mounts both into a target container at the
paths the venv expects:

=== "CLI"

    ```console
    $ dagger call uv workspace builder --package my-app --no-editable \
        with-venv --relocatable \
        with-remote-sync \
        copy-venv --container alpine --set-env-vars
    ```

=== "Python SDK"

    ```python
    runner = (
        dag.uv(source=src)
        .workspace()
        .builder(package=["my-app"], no_editable=True)
        .with_venv(relocatable=True)
        .with_remote_sync()
        .copy_venv(dag.container().from_("debian:bookworm-slim"), set_env_vars=True)
    )
    ```

!!! warning "Use `no_editable=True` when the venv carries workspace packages"

    By default `uv` installs your workspace's own packages **editable**: their
    `.pth` entries point back at the source tree, which does not exist at the target container.
    Use `builder(..., no_editable=True)` to bake the local sources into `site-packages` instead.

`copy_venv` mounts the venv at `.venv` (relative to the target's working directory by
default) and, with `set_env_vars`, exports `VIRTUAL_ENV` and prepends the venv's `bin/`
to `PATH` so plain `python` and console scripts resolve without activation. The target
image needs no `uv` and no Python.

If you want the pieces rather than a ready-made container, ask the build for its `venv`:
you get a `UvVenv` holding the environment, its interpreter, and the path the
interpreter must live at — then place it wherever you like.

## A note on portability

The bundled interpreter is a [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
build, and `uv` selects the variant matching the **base image** you built on. The target
container must match that platform and C library:

- Build on a Debian/glibc base → the venv runs on glibc targets (Debian, Ubuntu, …).
- Build on an Alpine/musl base → the venv runs on musl targets.

In other words, you control the target ABI by choosing the base container at build
time. The export only works with `uv`-managed (standalone) Pythons; if you build on an
image whose system Python `uv` reuses, there's nothing relocatable to export, and the
module will tell you so.

!!! tip "Bringing your own Python version"

    On a bare base with no Python, you can install or pin a specific managed Python
    before building with `with_python_install` / `with_python_pin`. `uv` otherwise
    provisions an appropriate Python on the first sync.
