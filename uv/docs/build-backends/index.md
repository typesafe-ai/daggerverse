---
icon: lucide/boxes
title: Supported Build Backends
description: Build backends whose settings the module reads when building workspace members.
---

# Supported Build Backends

The module reads the native settings of these build backends:

- [`uv_build`](uv-build.md)
- [Hatchling](hatchling.md)
- [pdm-backend](pdm-backend.md)
- [flit_core](flit.md)
- [poetry-core](poetry.md)

!!! tip "Your build backend is not supported?"
    No worries! Learn how to [implement your own](custom.md)

!!! note "Backend-agnostic settings"
    Files defined in `[project.readme]`, `[project.license-files]`, and
    `license = { file = ... }` are included for all backends.

!!! note "Installing pure applications (no build system)"
    Projects without a build system, or with `tool.uv.package = false` do not get installed, only their dependencies are.

!!! warning "Limitations"
    - Namespace packages are not supported.
    - Type-stub (`-stubs`) packages are not supported.
    - Versions read from git fail, because the build has no `.git` directory.

    Each backend page lists its own limitations. Supply missing inputs before
    `with_local_dependencies()` when using such settings; a full project copy after
    installation is too late for files the backend needs while building.

## Unknown build backends

Packages using an unknown backend can only be built if their module is named after the distribution (lowercased, with dashes replaced by underscores) and is either using the standard `src/` or the flat layout.
