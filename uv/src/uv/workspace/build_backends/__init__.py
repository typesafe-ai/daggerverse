"""Build backends: each one's import layout and the files it reads while building.

The module scaffolds local packages as stubs before their real source arrives,
so it must know where each backend expects the import directories and which
extra files (readme, license files, version source, hook scripts) the build reads.

These are plain Python types derived from a package's `pyproject.toml` on
demand, not Dagger API types: Dagger has no union types to carry the
backend-specific settings.
"""

import enum
from dataclasses import dataclass, field
from typing import Self

from uv.workspace.build_backends.hatchling import HatchlingSettings
from uv.workspace.build_backends.pdm import PdmSettings
from uv.workspace.build_backends.uv_build import UvBuildSettings

type BackendSettings = UvBuildSettings | HatchlingSettings | PdmSettings


class BuildBackendKind(enum.Enum):
    """A package's `[build-system].build-backend`, as far as this module interprets it."""

    UV_BUILD = "uv_build"
    HATCHLING = "hatchling.build"
    PDM = "pdm.backend"
    OTHER = "other"
    """Any other backend: conventional flat or `src/` layout, no backend-specific settings."""


@dataclass(frozen=True)
class BuildBackend:
    """A package's build backend together with the settings that shape its staging."""

    kind: BuildBackendKind
    # Backend-specific settings, discriminated by `kind`; None for OTHER.
    settings: BackendSettings | None = None
    # `[project]` readme and license files, which every backend reads.
    project_inputs: list[str] = field(default_factory=list)

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        backend = metadata.get("build-system", {}).get("build-backend")
        kind = next((kind for kind in BuildBackendKind if kind.value == backend), BuildBackendKind.OTHER)
        settings: BackendSettings | None
        match kind:
            case BuildBackendKind.UV_BUILD:
                settings = UvBuildSettings.from_pyproject(metadata, name)
            case BuildBackendKind.HATCHLING:
                settings = HatchlingSettings.from_pyproject(metadata)
            case BuildBackendKind.PDM:
                settings = PdmSettings.from_pyproject(metadata, name)
            case BuildBackendKind.OTHER:
                settings = None
        return cls(kind=kind, settings=settings, project_inputs=_project_inputs(metadata.get("project", {})))

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
