"""poetry-core: `[tool.poetry]` packages, included files, the legacy readme, and the build script.

See https://python-poetry.org/docs/pyproject/.
"""

import posixpath
from dataclasses import dataclass, field
from typing import Self

from uv.workspace.build_backends.paths import is_file, is_module, tree

_GLOB_CHARS = "*?["


def _in_wheel(entry: str | dict) -> bool:
    """Whether a `packages`/`include` entry ships in wheels (no `format` means every format)."""
    formats = entry.get("format") if isinstance(entry, dict) else None
    if formats is None:
        return True
    return "wheel" in ([formats] if isinstance(formats, str) else formats)


def _literal_prefix(path: str) -> str:
    """The leading path components before the first glob component."""
    parts = []
    for part in path.split("/"):
        if any(c in part for c in _GLOB_CHARS):
            break
        parts.append(part)
    return "/".join(parts)


@dataclass(frozen=True)
class PoetrySettings:
    """`[tool.poetry]` settings that determine the import layout and build inputs."""

    # Wheel `packages` (`from` joined with `include`); glob entries are cut to their
    # literal leading directory. None leaves poetry's discovery to the generic layout.
    packages: list[str] | None = None
    # Wheel `include` paths and globs (entries without a `format` count, too).
    includes: list[str] = field(default_factory=list)
    # The legacy `[tool.poetry].readme` file(s), used when there is no `[project]` table.
    readme: list[str] = field(default_factory=list)
    # The `[tool.poetry.build]` script (`build = "..."` in older projects).
    build_script: str | None = None

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        poetry = metadata.get("tool", {}).get("poetry", {})
        packages = None
        if "packages" in poetry:
            packages = []
            for entry in poetry["packages"]:
                if not _in_wheel(entry):
                    continue
                path = _literal_prefix(posixpath.join(entry.get("from", ""), entry["include"]))
                if path:
                    packages.append(posixpath.normpath(path))
        includes = [
            entry["path"] if isinstance(entry, dict) else entry
            for entry in poetry.get("include", [])
            if _in_wheel(entry)
        ]
        readme = poetry.get("readme", [])
        build = poetry.get("build")
        return cls(
            packages=list(dict.fromkeys(packages)) if packages is not None else None,
            includes=includes,
            readme=[readme] if isinstance(readme, str) else readme,
            build_script=build.get("script") if isinstance(build, dict) else build,
        )

    @property
    def module_name(self) -> None:
        return None

    @property
    def module_paths(self) -> list[str] | None:
        return None if self.packages is None else [path for path in self.packages if is_module(path)]

    @property
    def source_paths(self) -> list[str] | None:
        return self.packages

    @property
    def build_inputs(self) -> list[str]:
        # A literal include may be a file or a directory; a glob is matched as written.
        inputs = [
            path
            for include in self.includes
            for path in ([include] if any(c in include for c in _GLOB_CHARS) else tree(include))
        ]
        inputs += self.readme
        if self.build_script:
            inputs.append(self.build_script)
        inputs += [path for path in self.packages or [] if is_file(path) and not is_module(path)]
        return inputs
