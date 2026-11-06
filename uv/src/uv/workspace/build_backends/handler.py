"""Build backend handlers: Dagger objects that tell the module how a backend builds a package.

A handler answers, from a package's `pyproject.toml`, the same questions as a
built-in backend's settings, and can wrap the build in `setup`/`teardown`. Each
built-in backend's module defines a handler type (a `BuiltinHandler`), listed by
`BuildBackendHandlers`, to configure how some packages build; other modules
implement the `BuildBackendHandler` interface to support any other backend. A
handler's answers are resolved once per package while the sync plan is created
and stored as a `BuildBackendLayout`, so the rest of the pipeline reads them
like built-in settings.
"""

import asyncio
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, ClassVar, Protocol, Self

from dagger import Doc, field, function, interface, object_type
from dagger_clients.core import Container

from uv.workspace.build_backends.base import BackendSettings


@interface
class BuildBackendHandler(Protocol):
    """Tells the module how a build backend builds the packages that use it.

    Every function except `build_backend`, `setup` and `teardown` receives the
    package's `pyproject.toml` contents and its distribution name.
    Returning None from `module_name`, `module_paths`, or `source_paths` falls
    back to the generic layout: the module named after the distribution, under
    `src/` or at the package root.
    """

    @function
    async def build_backend(self) -> str:
        """The `[build-system].build-backend` value this handler handles, e.g. `setuptools.build_meta`."""
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
    async def editable_writes_source(self, pyproject: str, name: str) -> bool:
        """Whether an editable build writes into the package's source tree, e.g. a compiled extension.

        uv caches only the editable wheel, so such packages are rebuilt every time
        instead of installed from a cached build that would leave that out.
        """
        ...

    @function
    def setup(self, container: Container) -> Container:
        """Prepare the container before the local packages build, e.g. mount caches, set variables or secrets.

        Runs once per build, before the `uv sync` that builds every local package
        this handler handles. Return `container` unchanged when nothing is needed.
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
    """A package layout resolved from a `BuildBackendHandler`."""

    module_name: Annotated[str | None, Doc("Import name for the generic layout, if declared")] = field(default=None)
    module_paths: Annotated[list[str] | None, Doc("Import directories (or `.py` modules) to scaffold")] = field(
        default=None
    )
    source_paths: Annotated[list[str] | None, Doc("Directories (or files) to copy")] = field(default=None)
    build_inputs: Annotated[list[str], Doc("Files (or globs) the backend reads while building")] = field(default=list)
    editable_writes_source: Annotated[bool, Doc("Whether an editable build writes into the package's source tree")] = (
        field(default=False)
    )

    @classmethod
    async def resolve(cls, handler: BuildBackendHandler, pyproject: str, name: str) -> Self:
        module_name, module_paths, source_paths, build_inputs, editable_writes_source = await asyncio.gather(
            handler.module_name(pyproject, name),
            handler.module_paths(pyproject, name),
            handler.source_paths(pyproject, name),
            handler.build_inputs(pyproject, name),
            handler.editable_writes_source(pyproject, name),
        )
        return cls(
            module_name=module_name,
            module_paths=module_paths,
            source_paths=source_paths,
            build_inputs=build_inputs,
            editable_writes_source=editable_writes_source,
        )


@dataclass(frozen=True)
class HandlerHooks:
    """A handler's `setup`/`teardown` as `BackendHooks`: its calls are lazy, so nothing is awaited."""

    handler: BuildBackendHandler

    async def setup(self, container: Container) -> Container:
        return self.handler.setup(container)

    async def teardown(self, container: Container) -> Container:
        return self.handler.teardown(container)


@dataclass
class BuiltinHandler:
    """The handler interface for a built-in backend, answering like the module's own support for it.

    Each built-in backend's module subclasses it, setting its `backend` and `parse`;
    a backend with options adds them to its handler type, e.g. `MaturinHandler`'s
    `with_package_config`, and one that wraps the build overrides `setup`/`teardown`.
    """

    # The `[build-system].build-backend` value it handles.
    backend: ClassVar[str]
    # Its settings, parsed from a package's metadata and distribution name.
    parse: ClassVar[Callable[[dict, str], BackendSettings]]
    # Whether `setup`/`teardown` do anything, so a build without a handler runs them.
    has_hooks: ClassVar[bool] = False

    def _settings(self, pyproject: str, name: str) -> BackendSettings:
        return self.parse(tomllib.loads(pyproject), name)

    @function
    async def build_backend(self) -> str:
        """The `[build-system].build-backend` value this handler handles."""
        return self.backend

    @function
    async def module_name(self, pyproject: str, name: str) -> str | None:
        return self._settings(pyproject, name).module_name

    @function
    async def module_paths(self, pyproject: str, name: str) -> list[str] | None:
        return self._settings(pyproject, name).module_paths

    @function
    async def source_paths(self, pyproject: str, name: str) -> list[str] | None:
        return self._settings(pyproject, name).source_paths

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        return self._settings(pyproject, name).build_inputs

    @function
    async def editable_writes_source(self, pyproject: str, name: str) -> bool:
        return self._settings(pyproject, name).editable_writes_source

    @function
    async def setup(self, container: Container) -> Container:
        return container

    @function
    async def teardown(self, container: Container) -> Container:
        return container
