"""A `UvBuildBackend` plug-in fixture for the uv module's checks.

Supports setuptools packages under `[tool.setuptools] package-dir`, a layout the
uv module's generic one can't build. Plug-ins must live in their own module:
the Python SDK can't pass a module's own objects to its dependencies.
"""

import tomllib

from dagger import function, object_type


@object_type
class SetuptoolsPackageDir:
    """Lays out setuptools packages under `[tool.setuptools] package-dir`."""

    @function
    async def build_backend(self) -> str:
        return "setuptools.build_meta"

    @function
    async def module_name(self, pyproject: str, name: str) -> str | None:
        return None

    @function
    async def module_paths(self, pyproject: str, name: str) -> list[str] | None:
        setuptools = tomllib.loads(pyproject)["tool"]["setuptools"]
        return [f"{setuptools['package-dir']['']}/{package}" for package in setuptools["packages"]]

    @function
    async def source_paths(self, pyproject: str, name: str) -> list[str] | None:
        return [tomllib.loads(pyproject)["tool"]["setuptools"]["package-dir"][""]]

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        dynamic = tomllib.loads(pyproject)["tool"]["setuptools"].get("dynamic", {})
        return [entry["file"] for entry in dynamic.values() if "file" in entry]
