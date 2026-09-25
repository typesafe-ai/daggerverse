"""Resolve uv_build's import layout for dependency scaffolding and source copies."""

import posixpath
from typing import Annotated, Self

from dagger import Doc, field, object_type


@object_type
class UvBuildLayout:
    """Normalized uv_build settings carried between the Dagger build steps."""

    module_root: Annotated[str, Doc("Source root; an empty string means a flat layout")] = field()
    module_names: Annotated[list[str], Doc("Declared Python import names")] = field()

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self | None:
        """Return a layout only for projects explicitly using uv_build."""
        if metadata.get("build-system", {}).get("build-backend") != "uv_build":
            return None
        backend = metadata.get("tool", {}).get("uv", {}).get("build-backend", {})
        names = backend.get("module-name", name.lower().replace("-", "_").replace(".", "_"))
        if isinstance(names, str):
            names = [names]
        root = posixpath.normpath(backend.get("module-root", "src"))
        return cls(module_root="" if root == "." else root, module_names=names)

    @property
    def module_paths(self) -> list[str]:
        """Import directories to scaffold, relative to the package's pyproject.toml."""
        return [posixpath.join(self.module_root, name.replace(".", "/")) for name in self.module_names]

    @property
    def source_paths(self) -> list[str]:
        """Copy the source root, or only declared modules for a flat layout."""
        return [self.module_root] if self.module_root else self.module_paths
