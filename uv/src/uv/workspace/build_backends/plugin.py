"""Build backends implemented by other modules, through the `BuildBackend` interface.

A plug-in answers the same questions as a built-in backend's settings, from the
package's `pyproject.toml`. Its answers are resolved once per package while the
sync plan is created and stored as a `BuildBackendLayout`, so the rest of the
pipeline reads them like built-in settings.
"""

import asyncio
from typing import Annotated, Protocol, Self

from dagger import Doc, field, function, interface, object_type
from dagger_clients.core import Container


@interface
class BuildBackend(Protocol):
    """A build backend this module doesn't support natively, or one whose built-in support it replaces.

    Every function except `build_backend` receives the package's `pyproject.toml`
    contents and its distribution name. Returning None from `module_name`,
    `module_paths`, or `source_paths` falls back to the generic layout: the
    module named after the distribution, under `src/` or at the package root.
    """

    @function
    async def build_backend(self) -> str:
        """The `[build-system].build-backend` value this implementation handles, e.g. `setuptools.build_meta`."""
        ...

    @function
    async def module_name(self, pyproject: str, name: str) -> str | None:
        """The import name for the generic layout, dotted for a module inside namespace packages."""
        ...

    @function
    async def module_paths(self, pyproject: str, name: str) -> list[str] | None:
        """Package-relative import directories (or `.py` modules) to scaffold."""
        ...

    @function
    async def source_paths(self, pyproject: str, name: str) -> list[str] | None:
        """Package-relative directories (or files) to copy as the package's source."""
        ...

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        """Files (or globs) the backend reads while building: package-relative, or workspace-root-relative with `/`."""
        ...

    @function
    def setup(self, container: Container) -> Container:
        """Prepare the container before the local packages build, e.g. mount caches, set variables or secrets.

        Runs once per build, before the `uv sync` that builds every local package
        using this backend. Return `container` unchanged when nothing is needed.
        """
        ...

    @function
    def teardown(self, container: Container) -> Container:
        """Remove what `setup` added that shouldn't stay in the final container, e.g. variables or secrets.

        Runs right after that `uv sync`. Return `container` unchanged when nothing is needed.
        """
        ...


@object_type
class BuildBackendLayout:
    """A package layout resolved from a `BuildBackend` plug-in."""

    module_name: Annotated[str | None, Doc("Import name for the generic layout, if declared")] = field(default=None)
    module_paths: Annotated[list[str] | None, Doc("Import directories (or `.py` modules) to scaffold")] = field(
        default=None
    )
    source_paths: Annotated[list[str] | None, Doc("Directories (or files) to copy")] = field(default=None)
    build_inputs: Annotated[list[str], Doc("Files (or globs) the backend reads while building")] = field(default=list)

    @classmethod
    async def resolve(cls, backend: BuildBackend, pyproject: str, name: str) -> Self:
        module_name, module_paths, source_paths, build_inputs = await asyncio.gather(
            backend.module_name(pyproject, name),
            backend.module_paths(pyproject, name),
            backend.source_paths(pyproject, name),
            backend.build_inputs(pyproject, name),
        )
        return cls(
            module_name=module_name,
            module_paths=module_paths,
            source_paths=source_paths,
            build_inputs=build_inputs,
        )
