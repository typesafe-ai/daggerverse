"""maturin: Rust extensions, either pure Rust or a Python package with a native submodule.

See https://www.maturin.rs/project_layout and https://www.maturin.rs/config.
"""

import posixpath
from dataclasses import dataclass
from typing import Self

from dagger_clients.core import CacheSharingMode, Container, core

from uv.workspace.build_backends.paths import tree

# The package's own crate, relative to its `Cargo.toml`. Crates elsewhere in a
# Cargo workspace (a root `Cargo.toml`/`Cargo.lock`, path dependencies) are
# passed as `build_inputs` to the maturin handler.
_CRATE_INPUTS = [
    "Cargo.toml",
    "Cargo.lock",
    "build.rs",
    "src/**",
    ".cargo/config.toml",
    "rust-toolchain.toml",
    "rust-toolchain",
]

# `[project].dynamic` fields maturin fills from `Cargo.toml`, with the files they usually point at.
_CARGO_METADATA_FILES = {"readme": ["README*"], "license": ["LICENSE*", "COPYING*"]}

# Compiled crates are kept between builds; only the extension itself is installed.
# Concurrent builds get their own copy: maturin moves the built library out of the
# target directory after cargo releases its lock, so sharing one races.
_CARGO_TARGET_DIR = "/mnt/cache/cargo-target"


@dataclass(frozen=True)
class MaturinSettings:
    """`[tool.maturin]` settings that determine the import layout and build inputs."""

    # `module-name`: the native module's import name. A dotted name places it in a
    # Python package (a mixed project); otherwise it is the whole package.
    module: str
    # `python-source`: the directory holding the Python package of a mixed project.
    python_source: str | None = None
    # The directory of `manifest-path`, holding the crate's `Cargo.toml`.
    crate_dir: str = "."
    # `include` entries that ship in wheels.
    includes: tuple[str, ...] = ()
    # `[project].dynamic` fields maturin reads from `Cargo.toml`.
    cargo_metadata: tuple[str, ...] = ()

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        maturin = metadata.get("tool", {}).get("maturin", {})
        includes = [
            entry if isinstance(entry, str) else entry["path"]
            for entry in maturin.get("include", [])
            if isinstance(entry, str) or entry.get("format", "wheel") in ("wheel", ["wheel"])
        ]
        dynamic = set(metadata.get("project", {}).get("dynamic", []))
        return cls(
            module=maturin.get("module-name", name.lower().replace("-", "_")),
            python_source=maturin.get("python-source"),
            crate_dir=posixpath.dirname(posixpath.normpath(maturin.get("manifest-path", "Cargo.toml"))) or ".",
            includes=tuple(includes),
            cargo_metadata=tuple(sorted(dynamic & set(_CARGO_METADATA_FILES))),
        )

    @property
    def _package(self) -> str | None:
        """The Python package of a mixed project, or None for pure Rust."""
        if self.python_source is None and "." not in self.module:
            return None
        return self.module.split(".", 1)[0]

    @property
    def module_name(self) -> None:
        return None

    @property
    def module_paths(self) -> list[str]:
        if self._package is None:
            return []
        return [f"{self.python_source}/{self._package}" if self.python_source else self._package]

    @property
    def source_paths(self) -> list[str]:
        if self._package is None:
            return []
        return [self.python_source or self._package]

    @property
    def build_inputs(self) -> list[str]:
        inputs = [posixpath.normpath(posixpath.join(self.crate_dir, path)) for path in _CRATE_INPUTS]
        if self._package is None:
            # A pure Rust module ships its type stubs from next to `pyproject.toml`.
            inputs += [f"{self.module}.pyi", "py.typed"]
        for field in self.cargo_metadata:
            inputs += [
                posixpath.normpath(posixpath.join(self.crate_dir, path)) for path in _CARGO_METADATA_FILES[field]
            ]
        inputs += [path for include in self.includes for path in tree(include)]
        return inputs


class MaturinHooks:
    """Keeps compiled crates in a cache volume while maturin builds."""

    def setup(self, container: Container) -> Container:
        return container.with_mounted_cache(
            _CARGO_TARGET_DIR, core().cache_volume("cargo-target"), sharing=CacheSharingMode.PRIVATE
        ).with_env_variable("CARGO_TARGET_DIR", _CARGO_TARGET_DIR)

    def teardown(self, container: Container) -> Container:
        return container.without_env_variable("CARGO_TARGET_DIR").without_mount(_CARGO_TARGET_DIR)
