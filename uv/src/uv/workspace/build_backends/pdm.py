"""pdm-backend: package directory, explicit includes, the custom hook, and the version source.

See https://backend.pdm-project.org/build_config/ and
https://backend.pdm-project.org/metadata/.
"""

import posixpath
from dataclasses import dataclass
from typing import Self

from uv.utils import import_name
from uv.workspace.build_backends.paths import is_file, is_module

# pdm-backend runs this script as a build hook whenever it exists.
_DEFAULT_CUSTOM_HOOK = "pdm_build.py"


@dataclass(frozen=True)
class PdmSettings:
    """`[tool.pdm]` settings that determine the import layout and build inputs."""

    # Import name derived from the distribution name.
    import_name: str
    # `[tool.pdm.build].package-dir`; None leaves pdm's `src/`-or-root discovery to the generic layout.
    package_dir: str | None = None
    # Literal (non-glob) `[tool.pdm.build].includes` paths; None when unset.
    includes: list[str] | None = None
    # `[tool.pdm.build].custom-hook` script.
    custom_hook: str = _DEFAULT_CUSTOM_HOOK
    # File a `source = "file"` `[tool.pdm.version]` reads the version from.
    version_path: str | None = None

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        pdm = metadata.get("tool", {}).get("pdm", {})
        build = pdm.get("build", {})
        version = pdm.get("version", {})
        includes = build.get("includes")
        if includes is not None:
            # Glob includes select data inside the packages; only literal paths locate them.
            includes = [posixpath.normpath(path) for path in includes if not any(c in path for c in "*?[")]
        package_dir = build.get("package-dir")
        return cls(
            import_name=import_name(name),
            package_dir=posixpath.normpath(package_dir) if package_dir is not None else None,
            includes=includes,
            custom_hook=build.get("custom-hook", _DEFAULT_CUSTOM_HOOK),
            version_path=version.get("path") if version.get("source") == "file" else None,
        )

    @property
    def module_name(self) -> None:
        return None

    @property
    def module_paths(self) -> list[str] | None:
        if self.includes is not None:
            return [path for path in self.includes if is_module(path)]
        if self.package_dir is not None:
            return [posixpath.normpath(posixpath.join(self.package_dir, self.import_name))]
        return None

    @property
    def source_paths(self) -> list[str] | None:
        if self.includes is not None:
            return self.includes
        if self.package_dir is not None:
            # Copy the package root, or only the module for a flat layout.
            return [self.package_dir] if self.package_dir != "." else self.module_paths
        return None

    @property
    def build_inputs(self) -> list[str]:
        # The hook only runs if the script exists; a missing file is simply not staged.
        inputs = [self.custom_hook]
        if self.version_path:
            inputs.append(self.version_path)
        inputs += [path for path in self.includes or [] if is_file(path) and not is_module(path)]
        return inputs
