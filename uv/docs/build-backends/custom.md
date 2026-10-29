---
icon: lucide/puzzle
title: Custom backends
description: Add support for another build backend, or replace a built-in one, from your own module.
---

# Custom backends

To support another build backend, or to replace the built-in support for one,
implement the `UvBuildBackend` interface in a Dagger module and pass its objects as
`build_backends` to `build()`, `install()`, or `venv()`. A custom backend takes
precedence over the built-in one for the same `build-backend` value.

`build_backend()` returns the `[build-system].build-backend` value the
implementation handles. The other functions receive the package's
`pyproject.toml` contents and distribution name, and describe the package's layout:

| Function              | Description                                                          |
| --------------------- | -------------------------------------------------------------------- |
| `module_name`         | Names the import package; dotted inside namespace packages           |
| `module_paths`        | Lists the package-relative import directories (or `.py` modules) to stub |
| `source_paths`        | Lists the package-relative directories (or files) to copy            |
| `build_inputs`        | Lists the files (or globs) the build reads; `/` starts at the workspace root |
| `setup(container)`    | Prepares the container before the build, e.g. mounts caches          |
| `teardown(container)` | Cleans the container up after the build                              |

Returning None from `module_name`, `module_paths`, or `source_paths` falls back to
the conventional layout: the module named after the distribution, under `src/` or
at the package root.

## Example

This module supports setuptools packages that set `[tool.setuptools] package-dir`:

```python
import tomllib

from dagger import function, object_type
from dagger_clients.core import Container


@object_type
class SetuptoolsPackageDir:
    @function
    async def build_backend(self) -> str:
        return "setuptools.build_meta"

    @function
    async def module_name(self, pyproject: str, name: str) -> str | None:
        return None

    @function
    async def module_paths(self, pyproject: str, name: str) -> list[str] | None:
        setuptools = tomllib.loads(pyproject)["tool"]["setuptools"]
        return [f"{setuptools['package-dir']['']}/{p}" for p in setuptools["packages"]]

    @function
    async def source_paths(self, pyproject: str, name: str) -> list[str] | None:
        return [tomllib.loads(pyproject)["tool"]["setuptools"]["package-dir"][""]]

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        dynamic = tomllib.loads(pyproject)["tool"]["setuptools"].get("dynamic", {})
        return [entry["file"] for entry in dynamic.values() if "file" in entry]

    @function
    def setup(self, container: Container) -> Container:
        return container

    @function
    def teardown(self, container: Container) -> Container:
        return container
```

!!! warning "Separate module"
    The Python SDK can't pass a module's own objects to its dependencies, so the
    backend must live in its own module. Depend on it, and pass the object from its
    client:

    ```python
    from dagger_clients.setuptools_package_dir import setuptools_package_dir
    from dagger_clients.uv import uv

    uv(workspace).workspace(".").install(build_backends=[setuptools_package_dir()])
    ```
