import posixpath
import tomllib
from typing import Annotated, Self

import dagger
from dagger import Doc, field, object_type

from uv_workspace._codegen import dagger_codegen as _run_codegen
from uv_workspace._utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    parse_local_packages,
)


@object_type
class LocalPackage:
    """A local (editable/directory) package in a uv workspace."""

    name: Annotated[str, Doc("Package name")] = field()
    path: Annotated[str, Doc("Workspace-relative path")] = field()


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
        ws_dir = (
            source_dir
            if workspace_path == "."
            else source_dir.directory(workspace_path)
        )
        lock_data = tomllib.loads(await ws_dir.file("uv.lock").contents())
        all_local = parse_local_packages(lock_data)
        needed_local = (
            find_transitive_local_deps(lock_data, package) if package else all_local
        )

        if dagger_codegen:
            codegen_path = (
                all_local[package] if package and package in all_local else "."
            )
            ws_dir = await _run_codegen(ws_dir, codegen_path)

        flat_package_flag = False
        if package and package in all_local:
            pkg_toml = tomllib.loads(
                await ws_dir.file(
                    posixpath.join(all_local[package], "pyproject.toml")
                ).contents()
            )
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
            all_local=[LocalPackage(name=n, path=p) for n, p in all_local.items()],
            needed_local=[
                LocalPackage(name=n, path=p) for n, p in needed_local.items()
            ],
            flat_package=flat_package_flag,
            package=package,
            uv_sync_args=sync_args,
        )
