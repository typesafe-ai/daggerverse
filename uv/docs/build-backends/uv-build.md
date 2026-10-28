---
icon: lucide/zap
title: uv_build
description: How the module builds workspace members that use uv's own build backend.
---

# `uv_build`

Applies to packages with `build-backend = "uv_build"`. The module reads these
[`[tool.uv.build-backend]`](https://docs.astral.sh/uv/reference/settings/#build-backend)
settings:

- [`module-name`](https://docs.astral.sh/uv/reference/settings/#build-backend_module-name)
  and [`module-root`](https://docs.astral.sh/uv/reference/settings/#build-backend_module-root)
  locate the import packages. Explicit names can differ from the distribution name, and
  dotted names map to nested directories.
- [`data`](https://docs.astral.sh/uv/reference/settings/#build-backend_data) directories
  are available while the package builds.

For example, this configuration declares two modules at the package root:

```toml
[tool.uv.build-backend]
module-name = ["actual_package", "compat_package"]
module-root = "."
```

Only `actual_package/` and `compat_package/` are copied, so unrelated root-level files
such as `tests/` stay out of the container. With a source root such as `src`, the whole
root is copied.

!!! warning "Not supported"
    [Namespace packages](https://docs.astral.sh/uv/concepts/build-backend/#namespace-packages)
    (`namespace = true`) and
    [stub packages](https://docs.astral.sh/uv/concepts/build-backend/#stub-packages).
