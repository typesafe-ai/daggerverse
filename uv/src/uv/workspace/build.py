import posixpath
from typing import Annotated

import dagger
from dagger import Doc, field, function, object_type
from dagger.telemetry import get_tracer

from uv.workspace.plan import LocalPackage, UvSyncPlan
from uv.workspace.venv import UvVenv


@object_type
class UvWorkspaceBuild:
    """An in-progress workspace build: a container plus its resolved sync plan.

    Drives the install pipeline: `with_remote_dependencies` to install remote
    deps, `with_workspace_files` to scaffold local packages, then
    `with_local_dependencies` to install them.
    """

    container: Annotated[
        dagger.Container,
        Doc("Container carrying the workspace's pyproject.toml and uv.lock"),
    ] = field()

    plan: Annotated[
        UvSyncPlan,
        Doc("Build configuration carried through the pipeline"),
    ] = field()

    async def _exec_step(self, span_name: str, argv: list[str], attributes: dict[str, object]) -> "UvWorkspaceBuild":
        """Run `argv` in the build container under a span, returning a new build with the result.

        `with_exec` is lazy; sync() inside the span so it captures the actual work
        rather than just the query-graph construction.
        """
        with get_tracer().start_as_current_span(span_name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
            ctr = await self.container.with_exec(argv).sync()
        return self.with_container(ctr)

    @function
    async def with_remote_dependencies(self) -> "UvWorkspaceBuild":
        """Install remote (non-local) dependencies via `uv sync --no-install-local`.

        Skip this step when another tool (e.g. `pulumi install`) handles
        dependency installation.
        """
        args = [*self.plan.uv_sync_args, "--no-install-local"]
        return await self._exec_step("install remote dependencies", args, {"uv.sync_args": args})

    @function
    async def with_venv(
        self,
        relocatable: Annotated[
            bool,
            Doc("Create a relocatable virtual environment (`uv venv --relocatable`). Useful for multi-stage builds."),
        ] = False,
        args: Annotated[
            list[str] | None,
            Doc("Additional arguments passed through to `uv venv` (e.g. `--python`, `--seed`)."),
        ] = None,
    ) -> "UvWorkspaceBuild":
        """Create the project virtual environment with `uv venv`.

        Run before the install steps so the subsequent `uv sync` populates this
        environment rather than creating its own (e.g. a `relocatable` venv that
        can be copied to a different path in a later stage).
        """
        argv = ["uv", "venv"]
        if relocatable:
            argv.append("--relocatable")
        argv += args or []
        return await self._exec_step("create virtual environment", argv, {"uv.venv_args": argv})

    @function
    async def with_python_install(
        self,
        version: Annotated[
            str,
            Doc("Python version to install via `uv python install` (e.g. `3.12`, `3.13.7`)."),
        ],
    ) -> "UvWorkspaceBuild":
        """Install a managed Python via `uv python install`.

        Useful on a bare base with no system Python; pass the version the
        workspace's `requires-python` resolves to.
        """
        argv = ["uv", "python", "install", version]
        return await self._exec_step(f"install python {version}", argv, {"uv.python_version": version})

    @function
    async def with_python_pin(
        self,
        version: Annotated[
            str,
            Doc("Python version to pin via `uv python pin` (writes a `.python-version` file)."),
        ],
    ) -> "UvWorkspaceBuild":
        """Pin the project's Python with `uv python pin` (writes `.python-version`).

        Makes subsequent `uv venv`/`uv sync` select this exact version.
        """
        argv = ["uv", "python", "pin", version]
        return await self._exec_step(f"pin python {version}", argv, {"uv.python_version": version})

    @function
    async def venv(self) -> UvVenv:
        """Export this build's virtual environment together with the Python it needs.

        Bundles the venv and the exact interpreter it links against into a
        `UvVenv` (see `UvVenv.create`). Call after the venv is populated (e.g.
        after `with_remote_dependencies`/`with_local_dependencies`). Requires a
        relocatable venv built against a uv-managed Python; raises otherwise.
        """
        workdir = await self.container.workdir()
        return await UvVenv.create(self.container, posixpath.join(workdir, ".venv"))

    @function
    async def copy_venv(
        self,
        container: Annotated[dagger.Container, Doc("Container to copy the venv and its Python into.")],
        path: Annotated[
            str,
            Doc(
                "Where to mount the venv; relative paths resolve against the container's workdir. Defaults to `.venv`."
            ),
        ] = ".venv",
        set_env_vars: Annotated[
            bool,
            Doc("Also set the standard activation env vars (`VIRTUAL_ENV` and a `PATH` with the venv's `bin/` first)."),
        ] = False,
    ) -> dagger.Container:
        """Copy this build's venv (and the uv-managed Python it needs) into `container`.

        Convenience over `venv().into(...)`: mounts the relocatable venv at `path`
        and its Python at the absolute path the venv expects, yielding a container
        that can run the environment without uv. Same constraints as `venv`.
        """
        return await (await self.venv()).into(container, path, set_env_vars)

    def _scaffold_package(self, ctr: dagger.Container, workdir: str, pkg: LocalPackage) -> dagger.Container:
        """Scaffold a single package stub (pyproject.toml + README + empty module)."""
        ctr = ctr.with_file(
            posixpath.join(workdir, pkg.path, "pyproject.toml"),
            self.plan.ws_dir.file(posixpath.join(pkg.path, "pyproject.toml")),
        )
        if pkg.name in self.plan.flat_packages:
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
    async def with_workspace_files(self) -> "UvWorkspaceBuild":
        """Scaffold needed local package stubs (pyproject.toml + empty src/) into the container."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.needed_local, workdir, "scaffold local dependencies")
        return UvWorkspaceBuild(container=ctr, plan=self.plan)

    @function
    async def with_all_workspace_members(self) -> "UvWorkspaceBuild":
        """Like with_workspace_files but scaffolds every local package, not just transitive deps."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.all_local, workdir, "scaffold all workspace members")
        return UvWorkspaceBuild(container=ctr, plan=self.plan)

    @function
    def with_container(
        self,
        container: Annotated[
            dagger.Container,
            Doc("Replacement container (e.g. after installing non-Python packages)"),
        ],
    ) -> "UvWorkspaceBuild":
        """Return a new UvWorkspaceBuild with a different container but the same plan."""
        return UvWorkspaceBuild(container=container, plan=self.plan)

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

    async def _copy_sources(self, ctr: dagger.Container, workdir: str) -> dagger.Container:
        """Copy real source for each needed local package into `ctr`.

        The per-package `with_directory` calls are lazy, so the span forces
        evaluation with `sync()` before closing; otherwise it would capture only
        Python graph-building and report ~zero duration.
        """
        with get_tracer().start_as_current_span("copy local dependency sources") as span:
            span.set_attribute("packages.count", len(self.plan.needed_local))
            span.set_attribute("packages.names", [pkg.name for pkg in self.plan.needed_local])
            for pkg in self.plan.needed_local:
                if pkg.name in self.plan.flat_packages:
                    continue
                ctr = self._copy_package(ctr, workdir, pkg)
            return await ctr.sync()

    @function
    async def with_local_dependencies(self) -> dagger.Container:
        """Editable-install the scaffolded local packages, then copy their real source in last.

        Runs `uv sync` against the package stubs from `with_workspace_files` to
        editable-install the local members, then copies their real source over the
        stubs. Editable installs are path links, so the source goes live without a
        re-sync — meaning source-only changes don't invalidate the cached install layer.
        """
        workdir = await self.container.workdir()
        with get_tracer().start_as_current_span("install local dependencies") as span:
            span.set_attribute("uv.sync_args", self.plan.uv_sync_args)
            # `with_exec` is lazy; sync() inside the span so it captures the actual
            # install rather than just the query-graph construction.
            ctr = await self.container.with_exec(self.plan.uv_sync_args).sync()
        # Copy real source last: the editable installs above already point at these
        # paths, so the code goes live with no re-sync — keeping the install layer
        # cached across source-only changes.
        return await self._copy_sources(ctr, workdir)
