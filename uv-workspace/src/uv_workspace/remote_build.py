import posixpath
from typing import Annotated

import dagger
from dagger import Doc, field, function, object_type
from dagger.telemetry import get_tracer

from uv_workspace.sync_plan import LocalPackage, UvSyncPlan


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

    def _scaffold_package(self, ctr: dagger.Container, workdir: str, pkg: LocalPackage) -> dagger.Container:
        """Scaffold a single package stub (pyproject.toml + README + empty module)."""
        ctr = ctr.with_file(
            posixpath.join(workdir, pkg.path, "pyproject.toml"),
            self.plan.ws_dir.file(posixpath.join(pkg.path, "pyproject.toml")),
        )
        if pkg.name == self.plan.package and self.plan.flat_package:
            return ctr
        src_name = pkg.module
        ctr = ctr.with_new_file(posixpath.join(workdir, pkg.path, "README.md"), "")
        if pkg.flat:
            ctr = ctr.with_new_file(posixpath.join(workdir, pkg.path, src_name, "__init__.py"), "")
        else:
            ctr = ctr.with_new_file(
                posixpath.join(workdir, pkg.path, "src", src_name, "__init__.py"),
                "",
            )
        return ctr

    async def _scaffold(
        self,
        packages: list[LocalPackage],
        workdir: str,
        span_name: str,
    ) -> dagger.Container:
        """Scaffold package stubs (pyproject.toml + README + empty module) for `packages`.

        The per-package `with_file`/`with_new_file` calls are lazy, so the span
        forces evaluation with `sync()` before closing; otherwise it would capture
        only Python graph-building and report ~zero duration.
        """
        ctr = self.container
        with get_tracer().start_as_current_span(span_name) as span:
            span.set_attribute("packages.count", len(packages))
            span.set_attribute("packages.names", [pkg.name for pkg in packages])
            for pkg in packages:
                ctr = self._scaffold_package(ctr, workdir, pkg)
            return await ctr.sync()

    @function
    async def with_workspace_files(self) -> "UvRemoteBuild":
        """Scaffold needed local package stubs (pyproject.toml + empty src/) into the container."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.needed_local, workdir, "scaffold local dependencies")
        return UvRemoteBuild(container=ctr, plan=self.plan)

    @function
    async def with_all_workspace_members(self) -> "UvRemoteBuild":
        """Like with_workspace_files but scaffolds every local package, not just transitive deps."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.all_local, workdir, "scaffold all workspace members")
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

    def _copy_package(self, ctr: dagger.Container, workdir: str, pkg: LocalPackage) -> dagger.Container:
        """Copy a single local package's real source into the container."""
        if pkg.flat:
            src_name = pkg.module
            ctr = ctr.with_directory(
                posixpath.join(workdir, pkg.path, src_name),
                self.plan.ws_dir.directory(posixpath.join(pkg.path, src_name)),
            )
        else:
            ctr = ctr.with_directory(
                posixpath.join(workdir, pkg.path, "src"),
                self.plan.ws_dir.directory(posixpath.join(pkg.path, "src")),
            )
        return ctr

    async def _copy_sources(self, workdir: str) -> dagger.Container:
        """Copy real source for each needed local package into the container.

        The per-package `with_directory` calls are lazy, so the span forces
        evaluation with `sync()` before closing; otherwise it would capture only
        Python graph-building and report ~zero duration.
        """
        ctr = self.container
        with get_tracer().start_as_current_span("copy local dependency sources") as span:
            span.set_attribute("packages.count", len(self.plan.needed_local))
            span.set_attribute("packages.names", [pkg.name for pkg in self.plan.needed_local])
            for pkg in self.plan.needed_local:
                if pkg.name == self.plan.package and self.plan.flat_package:
                    continue
                ctr = self._copy_package(ctr, workdir, pkg)
            return await ctr.sync()

    @function
    async def with_local_dependencies(self) -> dagger.Container:
        """Copy real source, run final `uv sync`, and strip the build-time cache mount."""
        workdir = await self.container.workdir()
        ctr = await self._copy_sources(workdir)
        with get_tracer().start_as_current_span("install local dependencies") as span:
            span.set_attribute("uv.sync_args", self.plan.uv_sync_args)
            # `with_exec` is lazy; sync() inside the span so it captures the actual
            # install rather than just the query-graph construction.
            ctr = await ctr.with_exec(self.plan.uv_sync_args).sync()
        return ctr.without_mount("/root/.cache/uv")
