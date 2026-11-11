"""Build backends: each one's import layout and the files it reads while building.

The module scaffolds local packages as stubs before their real source arrives,
so it must know where each backend expects the import directories and which
extra files (readme, license files, version source, hook scripts) the build reads.

Each supported backend lives in its own module, with its handler type, and is
registered in `BACKENDS` under its `[build-system].build-backend` value. Its
settings are plain Python types derived from a package's `pyproject.toml` on
demand. Handlers (see `handler`) configure a built-in backend for some packages,
or support a backend this module doesn't know, through the `BuildBackendHandler`
interface.
"""

from dataclasses import dataclass, field
from typing import Any, Self

from dagger import function, object_type

from uv.workspace.build_backends.base import BackendSettings
from uv.workspace.build_backends.flit import FlitCoreHandler
from uv.workspace.build_backends.handler import BuiltinHandler
from uv.workspace.build_backends.hatchling import HatchlingHandler
from uv.workspace.build_backends.maturin import MaturinHandler
from uv.workspace.build_backends.pdm import PdmBackendHandler
from uv.workspace.build_backends.poetry import PoetryCoreHandler
from uv.workspace.build_backends.uv_build import UvBuildHandler

BACKENDS: dict[str, type[BuiltinHandler]] = {
    handler.backend: handler
    for handler in (
        UvBuildHandler,
        HatchlingHandler,
        PdmBackendHandler,
        FlitCoreHandler,
        PoetryCoreHandler,
        MaturinHandler,
    )
}


@object_type
class BuildBackendHandlers:
    """Handlers for the module's built-in backends, to pass to `Uv.with_build_backend_handlers`.

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


@dataclass(frozen=True)
class PackageBackend:
    """A package's build backend together with the settings that shape its staging."""

    # `[build-system].build-backend`, if declared.
    name: str | None = None
    # Settings of a supported backend, or a handler's answers; None for any other backend.
    settings: BackendSettings | None = None
    # `[project]` readme and license files, which every backend reads.
    project_inputs: list[str] = field(default_factory=list)

    @classmethod
    def from_pyproject(cls, metadata: dict[str, Any], name: str, settings: BackendSettings | None = None) -> Self:
        """The package's backend; `settings` (resolved from a handler) replaces the built-in ones."""
        backend = metadata.get("build-system", {}).get("build-backend")
        if settings is None and (builtin := BACKENDS.get(backend) if backend else None):
            settings = builtin.parse(metadata, name)
        return cls(
            name=backend,
            settings=settings,
            project_inputs=_project_inputs(metadata.get("project", {})),
        )

    @property
    def module_name(self) -> str | None:
        """The backend-declared import name for the generic layout, if any."""
        return self.settings.module_name if self.settings else None

    @property
    def module_paths(self) -> list[str] | None:
        """Declared import directories (or `.py` modules), or None to fall back to the generic layout."""
        return self.settings.module_paths if self.settings else None

    @property
    def source_paths(self) -> list[str] | None:
        """Declared directories (or files) to copy, or None to fall back to the generic layout."""
        return self.settings.source_paths if self.settings else None

    @property
    def editable_writes_source(self) -> bool:
        """Whether the backend's editable build writes into the source tree, which a cached build would skip."""
        return self.settings.editable_writes_source if self.settings else False

    @property
    def build_inputs(self) -> list[str]:
        """Package-relative files (or globs) the backend reads while building, staged into the scaffold."""
        backend_inputs = self.settings.build_inputs if self.settings else []
        return list(dict.fromkeys([*self.project_inputs, *backend_inputs]))


def _project_inputs(project: dict[str, Any]) -> list[str]:
    """Files named by PEP 621 metadata: `license-files`, a readme file, and a legacy `license.file`."""
    paths = list(project.get("license-files", []))
    readme = project.get("readme")
    if isinstance(readme, dict):
        readme = readme.get("file")
    if isinstance(readme, str):
        paths.append(readme)
    license_ = project.get("license")
    if isinstance(license_, dict) and "file" in license_:
        paths.append(license_["file"])
    return paths
