import posixpath
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

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
        Doc(
            "Path to workspace root (holding uv.lock and pyproject.toml) within source_dir"
        ),
    ] = field(default=".")

    @function
    async def with_remote_dependencies(
        self,
        package: Annotated[
            str | None,
            Doc(
                "Package name; if set, only that package's transitive local deps "
                "are scaffolded. Maps to `uv sync --package`"
            ),
        ] = None,
        extra: Annotated[
            list[str] | None,
            Doc("Extras to install; passed to `uv sync` as repeated `--extra`"),
        ] = None,
        group: Annotated[
            list[str] | None,
            Doc(
                "Dependency groups to install; passed to `uv sync` as repeated `--group`"
            ),
        ] = None,
        all_extras: Annotated[
            bool,
            Doc("Install every extra; maps to `uv sync --all-extras`"),
        ] = False,
        all_groups: Annotated[
            bool,
            Doc("Install every dependency group; maps to `uv sync --all-groups`"),
        ] = False,
        all_packages: Annotated[
            bool,
            Doc("Install every workspace member; maps to `uv sync --all-packages`"),
        ] = False,
        dagger_codegen: Annotated[
            bool,
            Doc(
                "If True (default), and the package being built has a "
                "`dagger.json`, run Dagger codegen and overlay the generated "
                "SDK before `uv sync`. No-op for non-Dagger projects."
            ),
        ] = True,
    ) -> UvRemoteBuild:
        """Install remote (non-local) dependencies.

        Copies the root pyproject.toml and uv.lock into the container and
        runs `uv sync --no-install-local`. Returns a `UvRemoteBuild`
        for the next pipeline steps: `with_workspace_files()` to scaffold
        local packages, then `with_local_dependencies()` to install them.
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

        ctr = ctr.with_exec([*plan.uv_sync_args, "--no-install-local"])

        return UvRemoteBuild(container=ctr, plan=plan)

    @function
    async def build(
        self,
        package: Annotated[
            str | None,
            Doc(
                "Package name; if set, only that package's transitive local deps "
                "are installed. Maps to `uv sync --package`"
            ),
        ] = None,
        extra: Annotated[
            list[str] | None,
            Doc("Extras to install; passed to `uv sync` as repeated `--extra`"),
        ] = None,
        group: Annotated[
            list[str] | None,
            Doc(
                "Dependency groups to install; passed to `uv sync` as repeated `--group`"
            ),
        ] = None,
        all_extras: Annotated[
            bool,
            Doc("Install every extra; maps to `uv sync --all-extras`"),
        ] = False,
        all_groups: Annotated[
            bool,
            Doc("Install every dependency group; maps to `uv sync --all-groups`"),
        ] = False,
        all_packages: Annotated[
            bool,
            Doc(
                "Install every workspace member; maps to `uv sync --all-packages`. "
                "Only meaningful in workspaces"
            ),
        ] = False,
        dagger_codegen: Annotated[
            bool,
            Doc(
                "If True (default), and the package being built has a "
                "`dagger.json`, run Dagger codegen and overlay the generated "
                "SDK before `uv sync`. This makes `[tool.uv.sources]` entries "
                'pointing at the generated tree (e.g. `dagger-io = { path = "sdk" }`) '
                "install correctly even though those paths are gitignored. "
                "No-op for non-Dagger projects. Pass False to skip."
            ),
        ] = True,
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
