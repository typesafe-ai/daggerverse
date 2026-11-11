"""maturin: Rust extensions, either pure Rust or a Python package with a native submodule.

See https://www.maturin.rs/project_layout and https://www.maturin.rs/config.
"""

import dataclasses
import posixpath
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Self, override

from dagger import Doc, field, function, object_type
from dagger_clients.core import CacheSharingMode, CacheVolume, Container, core

from uv.utils import normalize_package_name
from uv.workspace.build_backends.handler import BuiltinHandler
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

# Downloaded crates and git checkouts, shared between builds. `CARGO_HOME` moves
# onto the volume, since cargo locks them with a file at its root; the base's own
# `CARGO_HOME` may hold the toolchain, which stays on `PATH`.
_CARGO_HOME = "/mnt/cache/cargo-home"
# Holds the base's `CARGO_HOME` while maturin builds, for `teardown` to restore.
_BASE_CARGO_HOME = "UV_BASE_CARGO_HOME"


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
    def from_pyproject(cls, metadata: dict[str, Any], name: str) -> Self:
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
    def editable_writes_source(self) -> bool:
        # A mixed project's editable build places the extension in its Python package;
        # a pure Rust module's editable wheel carries the extension itself.
        return self._package is not None

    @property
    def build_inputs(self) -> list[str]:
        inputs = [posixpath.normpath(posixpath.join(self.crate_dir, path)) for path in _CRATE_INPUTS]
        if self._package is None:
            # A pure Rust module ships its type stubs from next to `pyproject.toml`.
            inputs += [f"{self.module}.pyi", "py.typed"]
        for metadata in self.cargo_metadata:
            inputs += [
                posixpath.normpath(posixpath.join(self.crate_dir, path)) for path in _CARGO_METADATA_FILES[metadata]
            ]
        inputs += [path for include in self.includes for path in tree(include)]
        return inputs


def target_prune_script(max_target_size: int) -> str:
    """Clear the Cargo target dir once it exceeds `max_target_size` GiB (0: always).

    Cargo never prunes a target dir: every toolchain, dependency and feature change
    leaves its artifacts behind. It has no age-based cleanup either, so this clears
    it all, costing one cold build each time the limit is crossed.
    """
    limit_kib = max_target_size * 1024 * 1024  # GiB -> KiB, to compare with `du -sk`
    gate = f'[ "${{used_kib:-0}}" -gt {limit_kib} ]' if max_target_size > 0 else "true"
    return (
        f"used_kib=$(du -skx {_CARGO_TARGET_DIR} 2>/dev/null | cut -f1); "
        f"if {gate}; then "
        f'echo "cargo target: ${{used_kib}} KiB used > {limit_kib} KiB limit; clearing" >&2; '
        f"find {_CARGO_TARGET_DIR} -mindepth 1 -delete; "
        "else "
        f'echo "cargo target: ${{used_kib}} KiB used <= {limit_kib} KiB limit; keeping" >&2; '
        "fi"
    )


ExtraBuildInputs = Annotated[
    list[str],
    Doc(
        "Files (or globs) the package's build reads on top of the defaults: package-relative, or "
        "workspace-root-relative with `/`."
    ),
]

MaxTargetSize = Annotated[
    int,
    Doc("Clear the Cargo target cache after a build once it grows past this many GiB; 0 clears it every time."),
]


@object_type
class MaturinHandler(BuiltinHandler):
    """Handles packages built with maturin."""

    backend: ClassVar[str] = "maturin"
    parse = MaturinSettings.from_pyproject
    # Caches downloaded and compiled crates while maturin builds, clearing compiled ones once they grow too large.
    has_hooks: ClassVar[bool] = True

    # Extra build inputs per normalized distribution name.
    package_inputs: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    # GiB past which the target cache is cleared after a build.
    max_target_size: int = 50
    home_cache: Annotated[
        CacheVolume,
        Doc(
            "The volume for `CARGO_HOME` (downloaded crates, git dependencies' checkouts), e.g. to mount where "
            "other Cargo commands run. Mount it with `SHARED` sharing and point `CARGO_HOME` at it."
        ),
    ] = field(default=lambda: core().cache_volume("cargo-home"))
    # The volume for the target dir; this module's own when unset.
    target_cache: CacheVolume | None = None

    @function
    def with_package_config(self, name: str, extra_build_inputs: ExtraBuildInputs) -> "MaturinHandler":
        """Configure how the package `name` builds, e.g. with the crates its Cargo workspace keeps above it."""
        inputs = {**self.package_inputs, normalize_package_name(name): extra_build_inputs}
        return dataclasses.replace(self, package_inputs=inputs)

    @function
    @override
    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        extra = self.package_inputs.get(normalize_package_name(name), [])
        return [*self._settings(pyproject, name).build_inputs, *extra]

    @function
    def with_max_target_size(self, max_target_size: MaxTargetSize) -> "MaturinHandler":
        """Bound the Cargo target cache that keeps compiled crates between builds."""
        return dataclasses.replace(self, max_target_size=max_target_size)

    @function
    def with_cache_volumes(
        self,
        home: Annotated[
            CacheVolume | None, Doc("Volume for `CARGO_HOME`: downloaded crates and git dependencies' checkouts")
        ] = None,
        target: Annotated[
            CacheVolume | None, Doc("Volume for the Cargo target dir: compiled crates, mounted private to each build")
        ] = None,
    ) -> "MaturinHandler":
        """Use these Cargo cache volumes, e.g. ones shared with another module, instead of this module's own."""
        return dataclasses.replace(
            self,
            home_cache=home or self.home_cache,
            target_cache=target or self.target_cache,
        )

    @function
    @override
    async def setup(self, container: Container) -> Container:
        if base_home := await container.env_variable("CARGO_HOME"):
            container = container.with_env_variable(_BASE_CARGO_HOME, base_home)
        target = self.target_cache or core().cache_volume("cargo-target")
        return (
            container.with_mounted_cache(_CARGO_HOME, self.home_cache)
            .with_env_variable("CARGO_HOME", _CARGO_HOME)
            .with_mounted_cache(_CARGO_TARGET_DIR, target, sharing=CacheSharingMode.PRIVATE)
            .with_env_variable("CARGO_TARGET_DIR", _CARGO_TARGET_DIR)
        )

    @function
    @override
    async def teardown(self, container: Container) -> Container:
        base_home = await container.env_variable(_BASE_CARGO_HOME)
        # The volume is private to this build, so nothing else is using what gets cleared.
        container = (
            container.with_exec(["sh", "-c", target_prune_script(self.max_target_size)])
            .without_env_variable("CARGO_TARGET_DIR")
            .without_mount(_CARGO_TARGET_DIR)
            .without_env_variable("CARGO_HOME")
            .without_mount(_CARGO_HOME)
        )
        if base_home:
            container = container.with_env_variable("CARGO_HOME", base_home).without_env_variable(_BASE_CARGO_HOME)
        return container
