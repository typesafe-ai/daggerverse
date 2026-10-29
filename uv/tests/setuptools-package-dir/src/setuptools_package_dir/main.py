"""A `UvBuildBackendHandler` fixture for the uv module's checks.

Handles setuptools packages under `[tool.setuptools] package-dir`, a layout the
uv module's generic one can't build. Handlers must live in their own module: the
Python SDK can't pass a module's own objects to its dependencies.
"""

import tomllib

from dagger import function, object_type
from dagger_clients.core import Container


@object_type
class SetuptoolsPackageDir:
    """Lays out setuptools packages under `[tool.setuptools] package-dir`."""

    @function
    async def build_backend(self) -> str:
        return "setuptools.build_meta"

    @function
    async def module_name(self, pyproject: str, name: str) -> str:
        # The uv module's Go interface can't declare a nullable return, so "" stands for None.
        return ""

    @function
    async def module_paths(self, pyproject: str, name: str) -> list[str]:
        setuptools = tomllib.loads(pyproject)["tool"]["setuptools"]
        return [f"{setuptools['package-dir']['']}/{package}" for package in setuptools["packages"]]

    @function
    async def source_paths(self, pyproject: str, name: str) -> list[str]:
        return [tomllib.loads(pyproject)["tool"]["setuptools"]["package-dir"][""]]

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        dynamic = tomllib.loads(pyproject)["tool"]["setuptools"].get("dynamic", {})
        # Workspace-root-relative: the check's workspace root is the package itself.
        return [f"/{entry['file']}" for entry in dynamic.values() if "file" in entry]

    @function
    def setup(self, container: Container) -> Container:
        """Leave a marker the check looks for, and a variable `teardown` removes."""
        return container.with_new_file("/tmp/setuptools-package-dir-setup", "").with_env_variable(
            "SETUPTOOLS_PACKAGE_DIR", "1"
        )

    @function
    def teardown(self, container: Container) -> Container:
        return container.without_env_variable("SETUPTOOLS_PACKAGE_DIR")
