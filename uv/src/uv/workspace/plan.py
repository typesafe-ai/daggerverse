import posixpath
import tomllib
from collections import OrderedDict
from typing import Annotated, Self

import dagger
from dagger import Doc, field, object_type
from dagger.telemetry import get_tracer

from uv.utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    normalize_package_name,
    parse_local_packages,
    parse_project_name,
    require_package_selection,
)
from uv.workspace._codegen import dagger_codegen as _run_codegen


def _match_reachable(
    packages: OrderedDict[str, str],
    workspace_path: str,
    pyproject_paths: list[str],
) -> OrderedDict[str, str]:
    """Keep only local packages whose resolved path has a pyproject.toml in the source tree."""
    existing = {posixpath.normpath(posixpath.dirname(p)) for p in pyproject_paths}
    result = OrderedDict()
    for name, path in packages.items():
        resolved = posixpath.normpath(posixpath.join(workspace_path, path))
        if resolved in existing:
            result[name] = path
    return result


_MODULE_NAME_OVERRIDES: dict[str, str] = {
    "dagger-io": "dagger",
}


def _module_name(pkg_name: str) -> str:
    """Return the Python module name for a distribution name.

    Currently exists only to handle dagger-io, but ideally it should do some real work in the future."""
    return _MODULE_NAME_OVERRIDES.get(pkg_name, pkg_name.replace("-", "_"))


def _transitive_union(lock_data: dict, packages: list[str]) -> OrderedDict[str, str]:
    """Union of every target package's transitive local deps (sorted by name)."""
    merged: OrderedDict[str, str] = OrderedDict()
    for package in packages:
        merged.update(find_transitive_local_deps(lock_data, package))
    return OrderedDict(sorted(merged.items()))


async def _discover_local_packages(
    lock_data: dict,
    packages: list[str],
    all_packages: bool,
    default_package: str | None,
    workspace_path: str,
    source_dir: dagger.Directory,
    ws_dir: dagger.Directory,
) -> tuple[OrderedDict[str, str], OrderedDict[str, str], dict[str, bool]]:
    """Find reachable local packages and detect each one's layout (flat vs src).

    Runs in-process with many glob calls against the Dagger API, so it gets its
    own span to surface the file-discovery phase as a node in the trace.
    Returns `(all_local, needed_local, flat_flags)`.

    `needed_local` mirrors `uv sync`'s selection: every member when
    `all_packages` is set, the union of the explicit `packages`' transitive
    local deps when given, otherwise the transitive local deps of
    `default_package` (the current package, à la a bare `uv sync`).
    """
    with get_tracer().start_as_current_span("discover local packages") as span:
        # Glob the source tree's pyproject.toml paths once and reuse them for both
        # all-local and needed-local reachability filtering.
        pyproject_paths = await source_dir.glob("**/pyproject.toml")
        all_local = _match_reachable(parse_local_packages(lock_data), workspace_path, pyproject_paths)
        if all_packages:
            needed_local = all_local
        else:
            # create()'s require_package_selection guarantees a target here:
            # the explicit packages, or the current (workspace-root) package.
            targets = packages or ([default_package] if default_package else [])
            needed_local = _match_reachable(_transitive_union(lock_data, targets), workspace_path, pyproject_paths)

        src_init_paths: set[str] = set()
        for p in await source_dir.glob("**/src/*/__init__.py"):
            src_init_paths.add(posixpath.normpath(p))
        for p in await ws_dir.glob("**/src/*/__init__.py"):
            resolved = p if workspace_path == "." else posixpath.join(workspace_path, p)
            src_init_paths.add(posixpath.normpath(resolved))

        flat_flags: dict[str, bool] = {}
        for name, path in all_local.items():
            module = _module_name(name)
            resolved_pkg = posixpath.normpath(posixpath.join(workspace_path, path))
            expected = posixpath.normpath(posixpath.join(resolved_pkg, "src", module, "__init__.py"))
            flat_flags[name] = expected not in src_init_paths

        span.set_attribute("packages.all", len(all_local))
        span.set_attribute("packages.needed", len(needed_local))
        span.set_attribute("packages.names", sorted(all_local))

    return all_local, needed_local, flat_flags


@object_type
class LocalPackage:
    """A local (editable/directory) package in a uv workspace."""

    name: Annotated[str, Doc("Package name")] = field()
    path: Annotated[str, Doc("Workspace-relative path")] = field()
    module: Annotated[str, Doc("Python module name")] = field()
    flat: Annotated[
        bool,
        Doc("Flat layout (module at package root) vs src layout (module under src/)"),
    ] = field(default=False)


@object_type
class UvSyncPlan:
    """Resolved build configuration for a uv workspace sync."""

    ws_dir: Annotated[
        dagger.Directory,
        Doc("Resolved workspace directory (with codegen overlay if applicable)"),
    ] = field()

    all_local: Annotated[
        list[LocalPackage],
        Doc("Every local package in the workspace (sorted by name)"),
    ] = field()

    needed_local: Annotated[
        list[LocalPackage],
        Doc("Local packages the target transitively depends on (sorted by name)"),
    ] = field()

    flat_packages: Annotated[
        list[str],
        Doc("Local packages with no build-system (virtual/deps-only: pyproject scaffolded, source skipped)"),
    ] = field(default=list)

    uv_sync_args: Annotated[
        list[str],
        Doc("Precomputed uv sync argv"),
    ] = field()

    no_editable: Annotated[
        bool,
        Doc("Local packages installed non-editable (source baked into site-packages, not path-linked)"),
    ] = field(default=False)

    @classmethod
    async def create(
        cls,
        source_dir: dagger.Directory,
        workspace_path: str = ".",
        package: list[str] | None = None,
        extra: list[str] | None = None,
        group: list[str] | None = None,
        all_extras: bool = False,
        all_groups: bool = False,
        all_packages: bool = False,
        dagger_codegen: bool = True,
        no_editable: bool = False,
    ) -> Self:
        """Parse uv.lock and resolve all build configuration up front."""
        ws_dir = source_dir if workspace_path == "." else source_dir.directory(workspace_path)
        packages = [normalize_package_name(p) for p in (package or [])]
        lock_data = tomllib.loads(await ws_dir.file("uv.lock").contents())

        # The workspace root's package name is `uv sync`'s implicit target when no
        # package is requested; resolve it so we mirror that default.
        root_name = parse_project_name(await ws_dir.file("pyproject.toml").contents())
        default_package = normalize_package_name(root_name) if root_name else None
        # Fail fast on a pure workspace root with no selection (nothing to install).
        require_package_selection(packages, all_packages, default_package)

        # Run Dagger codegen BEFORE reachability filtering so generated directories
        # (e.g. sdk/) are visible to the filter.  Without this, CI builds
        # (where sdk/ is gitignored) silently drop dagger-io from the plan.
        if dagger_codegen:
            raw_local = parse_local_packages(lock_data)
            # Overlay each target package's SDK (deduped); fall back to the
            # workspace root when no target is a local package.
            codegen_paths = list(dict.fromkeys(raw_local[p] for p in packages if p in raw_local)) or ["."]
            for codegen_path in codegen_paths:
                ws_dir = await _run_codegen(ws_dir, codegen_path)
            if workspace_path == ".":
                source_dir = ws_dir
            else:
                source_dir = source_dir.with_directory(workspace_path, ws_dir)

        all_local, needed_local, flat_flags = await _discover_local_packages(
            lock_data, packages, all_packages, default_package, workspace_path, source_dir, ws_dir
        )

        # A local package with no [build-system] is a virtual (deps-only) project:
        # uv installs its dependencies but never builds the package itself, so its
        # source must not be scaffolded or copied — only its pyproject.toml (for
        # dependency resolution). This holds whether the package is a build target
        # or a transitive workspace dependency (e.g. a Pulumi program whose code
        # lives at the package root, with no src/ or module dir to copy).
        flat_packages: list[str] = []
        for name, pkg_path in all_local.items():
            pkg_toml = tomllib.loads(await ws_dir.file(posixpath.join(pkg_path, "pyproject.toml")).contents())
            if "build-system" not in pkg_toml:
                flat_packages.append(name)

        sync_args = build_uv_sync_args(
            packages=packages,
            extras=extra or [],
            groups=group or [],
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            no_editable=no_editable,
        )

        def to_pkgs(local: OrderedDict[str, str]) -> list[LocalPackage]:
            return [
                LocalPackage(name=n, path=p, module=_module_name(n), flat=flat_flags.get(n, False))
                for n, p in local.items()
            ]

        return cls(
            ws_dir=ws_dir,
            all_local=to_pkgs(all_local),
            needed_local=to_pkgs(needed_local),
            flat_packages=flat_packages,
            uv_sync_args=sync_args,
            no_editable=no_editable,
        )
