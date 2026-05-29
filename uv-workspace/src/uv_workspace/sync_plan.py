import posixpath
import tomllib
from collections import OrderedDict
from typing import Annotated, Self

import dagger
from dagger import Doc, field, object_type

from uv_workspace._codegen import dagger_codegen as _run_codegen
from uv_workspace._utils import (
    _normalize,
    build_uv_sync_args,
    find_transitive_local_deps,
    parse_local_packages,
)


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


async def _filter_reachable(
    packages: OrderedDict[str, str],
    workspace_path: str,
    source_dir: dagger.Directory,
) -> OrderedDict[str, str]:
    """Drop local packages whose paths don't exist in the source directory."""
    pyproject_paths = await source_dir.glob("**/pyproject.toml")
    return _match_reachable(packages, workspace_path, pyproject_paths)


_MODULE_NAME_OVERRIDES: dict[str, str] = {
    "dagger-io": "dagger",
}


def _module_name(pkg_name: str) -> str:
    """Return the Python module name for a distribution name.

    Currently exists only to handle dagger-io, but ideally it should do some real work in the future."""
    return _MODULE_NAME_OVERRIDES.get(pkg_name, pkg_name.replace("-", "_"))


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

    flat_package: Annotated[
        bool,
        Doc("Whether the target package uses a flat layout (no build-system)"),
    ] = field(default=False)

    package: Annotated[
        str | None,
        Doc("Target package name (None means all packages)"),
    ] = field(default=None)

    uv_sync_args: Annotated[
        list[str],
        Doc("Precomputed uv sync argv"),
    ] = field()

    @classmethod
    async def create(
        cls,
        source_dir: dagger.Directory,
        workspace_path: str = ".",
        package: str | None = None,
        extra: list[str] | None = None,
        group: list[str] | None = None,
        all_extras: bool = False,
        all_groups: bool = False,
        all_packages: bool = False,
        dagger_codegen: bool = True,
    ) -> Self:
        """Parse uv.lock and resolve all build configuration up front."""
        ws_dir = source_dir if workspace_path == "." else source_dir.directory(workspace_path)
        if package:
            package = _normalize(package)
        lock_data = tomllib.loads(await ws_dir.file("uv.lock").contents())

        # Run Dagger codegen BEFORE reachability filtering so generated directories
        # (e.g. sdk/) are visible to the filter.  Without this, CI builds
        # (where sdk/ is gitignored) silently drop dagger-io from the plan.
        if dagger_codegen:
            raw_local = parse_local_packages(lock_data)
            codegen_path = raw_local[package] if package and package in raw_local else "."
            ws_dir = await _run_codegen(ws_dir, codegen_path)
            if workspace_path == ".":
                source_dir = ws_dir
            else:
                source_dir = source_dir.with_directory(workspace_path, ws_dir)

        all_local = await _filter_reachable(parse_local_packages(lock_data), workspace_path, source_dir)
        needed_local = (
            await _filter_reachable(
                find_transitive_local_deps(lock_data, package),
                workspace_path,
                source_dir,
            )
            if package
            else all_local
        )

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

        flat_package_flag = False
        if package and package in all_local:
            pkg_toml = tomllib.loads(await ws_dir.file(posixpath.join(all_local[package], "pyproject.toml")).contents())
            flat_package_flag = "build-system" not in pkg_toml

        sync_args = build_uv_sync_args(
            package=package,
            extras=extra or [],
            groups=group or [],
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
        )

        return cls(
            ws_dir=ws_dir,
            all_local=[
                LocalPackage(
                    name=n,
                    path=p,
                    module=_module_name(n),
                    flat=flat_flags.get(n, False),
                )
                for n, p in all_local.items()
            ],
            needed_local=[
                LocalPackage(
                    name=n,
                    path=p,
                    module=_module_name(n),
                    flat=flat_flags.get(n, False),
                )
                for n, p in needed_local.items()
            ],
            flat_package=flat_package_flag,
            package=package,
            uv_sync_args=sync_args,
        )
