"""hatchling: wheel file selection, forced inclusions, hook scripts, and the version source.

See https://hatch.pypa.io/latest/config/build/ and
https://hatch.pypa.io/latest/plugins/builder/wheel/.
"""

import posixpath
from dataclasses import dataclass, field
from typing import Self

from uv.workspace.build_backends.paths import is_file, is_module, tree, within_package

# Hatch's default script for `custom` build and metadata hooks.
_DEFAULT_HOOK_SCRIPT = "hatch_build.py"


@dataclass(frozen=True)
class HatchlingSettings:
    """`[tool.hatch]` settings that determine the import layout and build inputs.

    Wheel-target options (`[tool.hatch.build.targets.wheel]`) take precedence over
    the global `[tool.hatch.build]` ones.
    """

    # `packages` directories; None when unset.
    packages: list[str] | None = None
    # `only-include` paths; None when unset.
    only_include: list[str] | None = None
    # `bypass-selection`: the wheel ships no files of its own (e.g. a metapackage).
    bypass_selection: bool = False
    # Package-relative sources of `force-include` mappings.
    force_include: list[str] = field(default_factory=list)
    # Package-relative sources of the wheel's `shared-data`, `shared-scripts`, and `extra-metadata`.
    shared_paths: list[str] = field(default_factory=list)
    # Scripts of `custom` build and metadata hooks.
    hook_scripts: list[str] = field(default_factory=list)
    # Whether any build hook (e.g. `custom`, or a plugin like hatch-cython) runs for the wheel.
    build_hooks: bool = False
    # File the `regex` or `code` version source reads (`[tool.hatch.version].path`).
    version_path: str | None = None

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        hatch = metadata.get("tool", {}).get("hatch", {})
        build = hatch.get("build", {})
        wheel = build.get("targets", {}).get("wheel", {})

        def option(key: str, default=None):
            return wheel.get(key, build.get(key, default))

        def paths(values) -> list[str] | None:
            return None if values is None else [posixpath.normpath(value) for value in values]

        hooks = [
            hook
            for hook in (
                build.get("hooks", {}).get("custom"),
                wheel.get("hooks", {}).get("custom"),
                hatch.get("metadata", {}).get("hooks", {}).get("custom"),
            )
            if hook is not None
        ]
        return cls(
            packages=paths(option("packages")),
            only_include=paths(option("only-include")),
            bypass_selection=option("bypass-selection", False),
            force_include=within_package({**build.get("force-include", {}), **wheel.get("force-include", {})}),
            shared_paths=within_package(
                {
                    **wheel.get("shared-data", {}),
                    **wheel.get("shared-scripts", {}),
                    **wheel.get("extra-metadata", {}),
                }
            ),
            hook_scripts=list(dict.fromkeys(hook.get("path", _DEFAULT_HOOK_SCRIPT) for hook in hooks)),
            build_hooks=bool(build.get("hooks") or wheel.get("hooks")),
            version_path=hatch.get("version", {}).get("path"),
        )

    @property
    def _selection(self) -> list[str] | None:
        """Explicitly selected wheel paths, or None to leave discovery to hatch."""
        if self.packages is None and self.only_include is None:
            return [] if self.bypass_selection else None
        return [*(self.packages or []), *(self.only_include or [])]

    @property
    def module_name(self) -> None:
        return None

    @property
    def module_paths(self) -> list[str] | None:
        selection = self._selection
        return None if selection is None else [path for path in selection if is_module(path)]

    @property
    def source_paths(self) -> list[str] | None:
        return self._selection

    @property
    def editable_writes_source(self) -> bool:
        # Build hooks also run for editable builds, and may compile extensions in place.
        return self.build_hooks

    @property
    def build_inputs(self) -> list[str]:
        # Forced and shared paths may be files or directories; match both.
        inputs = [path for source in (*self.force_include, *self.shared_paths) for path in tree(source)]
        inputs += self.hook_scripts
        if self.version_path:
            inputs.append(self.version_path)
        # Non-module files picked by `only-include` (e.g. a top-level data file).
        inputs += [path for path in self._selection or [] if is_file(path) and not is_module(path)]
        return inputs
