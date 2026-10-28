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

For every backend, the files named in `[project]` (`readme`, `license-files`, and
`license = { file = ... }`) are included as well. License patterns support globs such
as `LICENSE*` and `licenses/**/*.txt`.

Members with no build system, or with `tool.uv.package = false`, are dependency-only:
their dependencies are installed, but the member itself is not.

## Other backends

Packages using any other backend (`setuptools`, `flit_core`, `poetry-core`, ...) build
when their module is named after the distribution (lowercased, with dashes replaced by
underscores) and sits under `src/` or at the package root.

!!! warning "Limitations"
    - PEP 420 namespace packages and type-stub (`-stubs`) packages are not supported.
    - Versions read from git fail, because the build has no `.git` directory.

    Each backend page lists its own limitations. Supply missing inputs before
    `with_local_dependencies()` when using such settings; a full project copy after
    installation is too late for files the backend needs while building.
