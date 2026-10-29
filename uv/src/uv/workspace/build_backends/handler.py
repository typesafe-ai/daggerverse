"""Build backend handlers: Dagger objects that tell the module how a backend builds a package.

A handler answers, from a package's `pyproject.toml`, the same questions as a
built-in backend's settings, and can wrap the build in `setup`/`teardown`. The
module provides a handler type for each of its built-in backends
(`BuildBackendHandlers`), to configure how some packages build; other modules
implement the `BuildBackendHandler` interface to support any other backend. A handler's
answers are resolved once per package while the sync plan is created and stored
as a `BuildBackendLayout`, so the rest of the pipeline reads them like built-in
settings.
"""

import asyncio
import dataclasses
import tomllib
from dataclasses import dataclass
from typing import Annotated, ClassVar, Protocol, Self

from dagger import Doc, field, function, interface, object_type
from dagger_clients.core import Container

from uv.utils import normalize_package_name
from uv.workspace.build_backends import BACKENDS, BackendSettings


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

    @classmethod
    async def resolve(cls, handler: BuildBackendHandler, pyproject: str, name: str) -> Self:
        module_name, module_paths, source_paths, build_inputs = await asyncio.gather(
            handler.module_name(pyproject, name),
            handler.module_paths(pyproject, name),
            handler.source_paths(pyproject, name),
            handler.build_inputs(pyproject, name),
        )
        return cls(
            module_name=module_name,
            module_paths=module_paths,
            source_paths=source_paths,
            build_inputs=build_inputs,
        )


ExtraBuildInputs = Annotated[
    list[str],
    Doc(
        "Files (or globs) the package's build reads on top of the defaults: package-relative, or "
        "workspace-root-relative with `/`."
    ),
]


@dataclass
class _Builtin:
    """The handler interface for a built-in backend, answering like the module's own support for it.

    A backend with options adds them to its own handler type, e.g. `MaturinHandler`'s
    `with_package_config`.
    """

    backend: ClassVar[str]

    def _settings(self, pyproject: str, name: str) -> BackendSettings:
        return BACKENDS[self.backend].parse(tomllib.loads(pyproject), name)

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
    def setup(self, container: Container) -> Container:
        hooks = BACKENDS[self.backend].hooks
        return hooks.setup(container) if hooks else container

    @function
    def teardown(self, container: Container) -> Container:
        hooks = BACKENDS[self.backend].hooks
        return hooks.teardown(container) if hooks else container


@object_type
class UvBuildHandler(_Builtin):
    """Handles packages built with `uv_build`."""

    backend: ClassVar[str] = "uv_build"


@object_type
class HatchlingHandler(_Builtin):
    """Handles packages built with Hatchling."""

    backend: ClassVar[str] = "hatchling.build"


@object_type
class PdmBackendHandler(_Builtin):
    """Handles packages built with pdm-backend."""

    backend: ClassVar[str] = "pdm.backend"


@object_type
class FlitCoreHandler(_Builtin):
    """Handles packages built with flit_core."""

    backend: ClassVar[str] = "flit_core.buildapi"


@object_type
class PoetryCoreHandler(_Builtin):
    """Handles packages built with poetry-core."""

    backend: ClassVar[str] = "poetry.core.masonry.api"


@object_type
class MaturinHandler(_Builtin):
    """Handles packages built with maturin."""

    backend: ClassVar[str] = "maturin"
    # Extra build inputs per normalized distribution name.
    package_inputs: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    @function
    def with_package_config(self, name: str, extra_build_inputs: ExtraBuildInputs) -> "MaturinHandler":
        """Configure how the package `name` builds, e.g. with the crates its Cargo workspace keeps above it."""
        inputs = {**self.package_inputs, normalize_package_name(name): extra_build_inputs}
        return dataclasses.replace(self, package_inputs=inputs)

    @function
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        extra = self.package_inputs.get(normalize_package_name(name), [])
        return [*self._settings(pyproject, name).build_inputs, *extra]


@object_type
class BuildBackendHandlers:
    """Handlers for the module's built-in backends, to pass as `build_backend_handlers` to `build`, `install` or `venv`.

    Use them to configure a backend for some packages, e.g. maturin with the
    crates a package's Cargo workspace keeps above it.
    """

    @function
    def uv_build(self) -> UvBuildHandler:
        return UvBuildHandler()

    @function
    def hatchling(self) -> HatchlingHandler:
        return HatchlingHandler()

    @function
    def pdm_backend(self) -> PdmBackendHandler:
        return PdmBackendHandler()

    @function
    def flit_core(self) -> FlitCoreHandler:
        return FlitCoreHandler()

    @function
    def poetry_core(self) -> PoetryCoreHandler:
        return PoetryCoreHandler()

    @function
    def maturin(self) -> MaturinHandler:
        return MaturinHandler()
