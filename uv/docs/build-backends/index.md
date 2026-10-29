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
- [maturin](maturin.md)

!!! tip "Your build backend is not supported?"
    [Plug it in](plugging-in.md) with a handler from your own module.

!!! note "Backend-agnostic settings"
    Files defined in `[project.readme]`, `[project.license-files]`, and
    `license = { file = ... }` are included for all backends.

!!! note "Installing pure applications (no build system)"
    Projects without a build system, or with `tool.uv.package = false` do not get installed, only their dependencies are.

!!! warning "Limitations"
    - Namespace packages are not supported.
    - Type-stub (`-stubs`) packages are not supported.
    - Versions taken from VCS (`git`) are not supported.

## Unknown build backends

Packages using an unknown backend can only be built if their module is named after the distribution (lowercased, with dashes replaced by underscores) and is either using the standard `src/` or the flat layout.
