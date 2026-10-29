"""Build backends: each one's import layout and the files it reads while building.

The module scaffolds local packages as stubs before their real source arrives,
so it must know where each backend expects the import directories and which
extra files (readme, license files, version source, hook scripts) the build reads.

Each supported backend lives in its own module and is registered in
`BACKENDS` under its `[build-system].build-backend` value. These are plain
Python types derived from a package's `pyproject.toml` on demand, not Dagger
API types. Other modules can add or replace backends through the
`BuildBackend` interface (see `plugin`).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, Self, runtime_checkable

from dagger_clients.core import Container

from uv.workspace.build_backends.flit import FlitSettings
from uv.workspace.build_backends.hatchling import HatchlingSettings
from uv.workspace.build_backends.pdm import PdmSettings
from uv.workspace.build_backends.poetry import PoetrySettings
from uv.workspace.build_backends.uv_build import UvBuildSettings


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
    def build_inputs(self) -> list[str]:
        """Files (or globs) the backend reads while building.

        Relative to the package, or to the workspace root with a leading `/`.
        """
        ...


@runtime_checkable
class BackendHooks(Protocol):
    """What runs around the local packages' build: a plug-in, or settings of a backend that needs it."""

    def setup(self, container: Container) -> Container:
        """Prepare the container before the local packages build, e.g. mount caches or set variables."""
        ...

    def teardown(self, container: Container) -> Container:
        """Undo what `setup` shouldn't leave in the final container, e.g. variables or secrets."""
        ...


# Each backend's settings, parsed from the package's metadata and distribution name.
BACKENDS: dict[str, Callable[[dict, str], BackendSettings]] = {
    "uv_build": UvBuildSettings.from_pyproject,
    "hatchling.build": HatchlingSettings.from_pyproject,
    "pdm.backend": PdmSettings.from_pyproject,
    "flit_core.buildapi": FlitSettings.from_pyproject,
    "poetry.core.masonry.api": PoetrySettings.from_pyproject,
}


@dataclass(frozen=True)
class PackageBackend:
    """A package's build backend together with the settings that shape its staging."""

    # `[build-system].build-backend`, if declared.
    name: str | None = None
    # Settings of a supported or plugged-in backend; None for any other backend.
    settings: BackendSettings | None = None
    # `[project]` readme and license files, which every backend reads.
    project_inputs: list[str] = field(default_factory=list)

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str, settings: BackendSettings | None = None) -> Self:
        """The package's backend; `settings` (resolved from a plug-in) replaces the built-in ones."""
        backend = metadata.get("build-system", {}).get("build-backend")
        if settings is None and (parse := BACKENDS.get(backend) if backend else None):
            settings = parse(metadata, name)
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
    def build_inputs(self) -> list[str]:
        """Package-relative files (or globs) the backend reads while building, staged into the scaffold."""
        backend_inputs = self.settings.build_inputs if self.settings else []
        return list(dict.fromkeys([*self.project_inputs, *backend_inputs]))


def _project_inputs(project: dict) -> list[str]:
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
