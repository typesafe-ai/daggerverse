"""What every build backend provides: the settings that shape a package's staging, and the hooks around its build."""

from typing import Protocol

from dagger_clients.core import Container


class BackendSettings(Protocol):
    """A backend's settings that shape a package's staging.

    None from `module_name`, `module_paths`, or `source_paths` falls back to the
    generic layout: the module named after the distribution, under `src/` or at
    the package root.
    """

    @property
    def module_name(self) -> str | None:
        """The import name for the generic layout, if the backend declares one."""
        ...

    @property
    def module_paths(self) -> list[str] | None:
        """Declared import directories (or `.py` modules)."""
        ...

    @property
    def source_paths(self) -> list[str] | None:
        """Declared directories (or files) to copy."""
        ...

    @property
    def editable_writes_source(self) -> bool:
        """Whether an editable build writes into the source tree (e.g. a compiled extension).

        uv caches only the editable wheel, so such packages are rebuilt rather
        than installed from a cached build.
        """
        ...

    @property
    def build_inputs(self) -> list[str]:
        """Files (or globs) the backend reads while building.

        Relative to the package, or to the workspace root with a leading `/`.
        """
        ...


class BackendHooks(Protocol):
    """What runs around the local packages' build: a handler, or a built-in backend that needs it."""

    async def setup(self, container: Container) -> Container:
        """Prepare the container before the local packages build, e.g. mount caches or set variables."""
        ...

    async def teardown(self, container: Container) -> Container:
        """Undo what `setup` shouldn't leave in the final container, e.g. variables or secrets."""
        ...
