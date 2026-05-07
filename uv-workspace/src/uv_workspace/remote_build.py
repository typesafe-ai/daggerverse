import posixpath
from typing import Annotated

import dagger
from dagger import Doc, field, function, object_type

from uv_workspace.sync_plan import UvSyncPlan


@object_type
class UvRemoteBuild:
    """Container with remote deps installed; chain with_workspace_files then with_local_dependencies."""

    container: Annotated[
        dagger.Container,
        Doc("Container with remote deps installed"),
    ] = field()

    plan: Annotated[
        UvSyncPlan,
        Doc("Build configuration carried through the pipeline"),
    ] = field()

    @function
    async def with_workspace_files(self) -> "UvRemoteBuild":
        """Scaffold needed local package stubs (pyproject.toml + empty src/) into the container."""
        ctr = self.container
        workdir = await ctr.workdir()

        for pkg in self.plan.needed_local:
            ctr = ctr.with_file(
                posixpath.join(workdir, pkg.path, "pyproject.toml"),
                self.plan.ws_dir.file(posixpath.join(pkg.path, "pyproject.toml")),
            )
            if pkg.name == self.plan.package and self.plan.flat_package:
                continue
            src_name = pkg.name.replace("-", "_")
            ctr = ctr.with_new_file(
                posixpath.join(workdir, pkg.path, "README.md"), ""
            ).with_new_file(
                posixpath.join(workdir, pkg.path, "src", src_name, "__init__.py"), ""
            )

        return UvRemoteBuild(container=ctr, plan=self.plan)

    @function
    async def with_all_workspace_members(self) -> "UvRemoteBuild":
        """Like with_workspace_files but scaffolds every local package, not just transitive deps."""
        ctr = self.container
        workdir = await ctr.workdir()

        for pkg in self.plan.all_local:
            ctr = ctr.with_file(
                posixpath.join(workdir, pkg.path, "pyproject.toml"),
                self.plan.ws_dir.file(posixpath.join(pkg.path, "pyproject.toml")),
            )
            if pkg.name == self.plan.package and self.plan.flat_package:
                continue
            src_name = pkg.name.replace("-", "_")
            ctr = ctr.with_new_file(
                posixpath.join(workdir, pkg.path, "README.md"), ""
            ).with_new_file(
                posixpath.join(workdir, pkg.path, "src", src_name, "__init__.py"),
                "",
            )

        return UvRemoteBuild(container=ctr, plan=self.plan)

    @function
    def with_container(
        self,
        container: Annotated[
            dagger.Container,
            Doc("Replacement container (e.g. after installing non-Python packages)"),
        ],
    ) -> "UvRemoteBuild":
        """Return a new UvRemoteBuild with a different container but the same plan."""
        return UvRemoteBuild(container=container, plan=self.plan)

    @function
    async def with_local_dependencies(self) -> dagger.Container:
        """Copy real source, run final `uv sync`, and strip the build-time cache mount."""
        ctr = self.container
        workdir = await ctr.workdir()

        for pkg in self.plan.needed_local:
            if pkg.name == self.plan.package and self.plan.flat_package:
                continue
            ctr = ctr.with_directory(
                posixpath.join(workdir, pkg.path, "src"),
                self.plan.ws_dir.directory(posixpath.join(pkg.path, "src")),
            )

        ctr = ctr.with_exec(self.plan.uv_sync_args)

        return ctr.without_mount("/root/.cache/uv")
