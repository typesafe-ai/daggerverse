import posixpath
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type
from dagger.telemetry import get_tracer

from uv_workspace.params import (
    AllExtras,
    AllGroups,
    AllPackages,
    DaggerCodegen,
    Extra,
    Group,
    Package,
)
from uv_workspace.remote_build import UvRemoteBuild
from uv_workspace.sync_plan import UvSyncPlan


@object_type
class UvWorkspace:
    """Builds minimal project containers by parsing uv.lock to resolve local dependencies."""

    source_dir: Annotated[
        dagger.Directory,
        Doc("Source directory containing the workspace"),
    ] = field()

    base_container: Annotated[
        dagger.Container,
        Doc("Pre-configured container (with auth, system packages, etc.)"),
    ] = field()

    workspace_path: Annotated[
        str,
        Doc("Path to workspace root (holding uv.lock and pyproject.toml) within source_dir"),
    ] = field(default=".")

    @function
    async def with_remote_dependencies(
        self,
        package: Package | None = None,
        extra: Extra | None = None,
        group: Group | None = None,
        all_extras: AllExtras = False,
        all_groups: AllGroups = False,
        all_packages: AllPackages = False,
        dagger_codegen: DaggerCodegen = True,
        install: Annotated[
            bool,
            Doc(
                "Run `uv sync --no-install-local` to install remote deps. "
                "Set to False when another tool (e.g. `pulumi install`) "
                "handles dependency installation."
            ),
        ] = True,
    ) -> UvRemoteBuild:
        """Prepare the build plan and (optionally) install remote dependencies.

        Copies the root pyproject.toml and uv.lock into the container and,
        when `install` is True (the default), runs `uv sync --no-install-local`.
        Returns a `UvRemoteBuild` for the next pipeline steps:
        `with_workspace_files()` to scaffold local packages, then
        `with_local_dependencies()` to install them.
        """
        plan = await UvSyncPlan.create(
            source_dir=self.source_dir,
            workspace_path=self.workspace_path,
            package=package,
            extra=extra,
            group=group,
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            dagger_codegen=dagger_codegen,
        )

        ctr = self.base_container
        workdir = await ctr.workdir()

        ctr = ctr.with_mounted_cache("/root/.cache/uv", dag.cache_volume("uv-cache"))

        ctr = ctr.with_file(
            posixpath.join(workdir, "pyproject.toml"),
            plan.ws_dir.file("pyproject.toml"),
        ).with_file(posixpath.join(workdir, "uv.lock"), plan.ws_dir.file("uv.lock"))

        if install:
            with get_tracer().start_as_current_span("install remote dependencies") as span:
                args = [*plan.uv_sync_args, "--no-install-local"]
                span.set_attribute("uv.sync_args", args)
                # `with_exec` is lazy; sync() inside the span so it captures the
                # actual install rather than just the query-graph construction.
                ctr = await ctr.with_exec(args).sync()

        return UvRemoteBuild(container=ctr, plan=plan)

    @function
    async def build(
        self,
        package: Package | None = None,
        extra: Extra | None = None,
        group: Group | None = None,
        all_extras: AllExtras = False,
        all_groups: AllGroups = False,
        all_packages: AllPackages = False,
        dagger_codegen: DaggerCodegen = True,
    ) -> dagger.Container:
        """Build a minimal container with deps installed for the given package.

        Convenience method composing `with_remote_dependencies`,
        `with_workspace_files`, and `with_local_dependencies`.
        For fine-grained control (e.g. running `pulumi install` between
        remote deps and local source), call them individually.
        """
        b = await self.with_remote_dependencies(
            package=package,
            extra=extra,
            group=group,
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            dagger_codegen=dagger_codegen,
        )
        b = await b.with_workspace_files()
        return await b.with_local_dependencies()
