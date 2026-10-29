"""uv_build: `[tool.uv.build-backend]` module names, source root, and wheel data directories.

See https://docs.astral.sh/uv/concepts/build-backend/.
"""

import posixpath
from dataclasses import dataclass, field
from typing import Self

from uv.utils import import_name
from uv.workspace.build_backends.paths import tree


@dataclass(frozen=True)
class UvBuildSettings:
    """`[tool.uv.build-backend]` settings that determine the import layout and build inputs."""

    # Source root; an empty string means a flat layout.
    module_root: str
    # Declared Python import names (dotted names map to nested directories).
    module_names: list[str]
    # `[tool.uv.build-backend.data]` directories (scripts, headers, data, ...) shipped in the wheel.
    data_dirs: list[str] = field(default_factory=list)

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        backend = metadata.get("tool", {}).get("uv", {}).get("build-backend", {})
        names = backend.get("module-name", import_name(name))
        if isinstance(names, str):
            names = [names]
        root = posixpath.normpath(backend.get("module-root", "src"))
        return cls(
            module_root="" if root == "." else root,
            module_names=names,
            data_dirs=[posixpath.normpath(path) for path in backend.get("data", {}).values()],
        )

    @property
    def module_name(self) -> None:
        return None

    @property
    def module_paths(self) -> list[str]:
        return [posixpath.join(self.module_root, name.replace(".", "/")) for name in self.module_names]

    @property
    def source_paths(self) -> list[str]:
        """Copy the source root, or only declared modules for a flat layout."""
        return [self.module_root] if self.module_root else self.module_paths

    @property
    def build_inputs(self) -> list[str]:
        return [path for data_dir in self.data_dirs for path in tree(data_dir)]
