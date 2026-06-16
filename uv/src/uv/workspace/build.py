import posixpath
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type
from dagger.telemetry import get_tracer

from uv.utils import _DEFAULT_BASE_UV_VERSION, image_ref
from uv.workspace.plan import LocalPackage, UvBuildPlan
from uv.workspace.venv import UvVenv


@object_type
class UvWorkspaceContainerBuilder:
    """An in-progress workspace build: a container plus its resolved build plan.

    Drives the install pipeline: `with_remote_sync` to install remote deps,
    `with_local_sources` to scaffold local packages, then `with_local_sync`
    to install them.
    """

    container: Annotated[
        dagger.Container,
        Doc("Container carrying the workspace's pyproject.toml and uv.lock"),
    ] = field()

    plan: Annotated[
        UvBuildPlan,
        Doc("Build configuration carried through the pipeline"),
    ] = field()

    async def _exec_step(
        self, span_name: str, argv: list[str], attributes: dict[str, object]
    ) -> "UvWorkspaceContainerBuilder":
        """Run `argv` in the build container under a span, returning a new build with the result.

        `with_exec` is lazy; sync() inside the span so it captures the actual work
        rather than just the query-graph construction.
        """
        with get_tracer().start_as_current_span(span_name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
            ctr = await self.container.with_exec(argv).sync()
        return self.with_container(ctr)

    @staticmethod
    async def _has_uv(ctr: dagger.Container) -> bool:
        """Check whether uv is on $PATH in the given container."""
        with get_tracer().start_as_current_span("detect uv on PATH") as span:
            exit_code = await ctr.with_exec(["sh", "-c", "command -v uv"], expect=dagger.ReturnType.ANY).exit_code()
            found = exit_code == 0
            span.set_attribute("uv.found", found)
            return found

    @function
    async def with_uv(
        self,
        version: Annotated[
            str | None,
            Doc("uv version to install. Defaults to the version detected from the workspace."),
        ] = None,
    ) -> "UvWorkspaceContainerBuilder":
        """Copy the uv binary into the build container.

        Useful when using a custom `base_container` that doesn't ship uv.
        Copies the static binary from the official distroless image to `/uv/uv`
        and prepends `/uv` to `$PATH`.
        """
        v = version or _DEFAULT_BASE_UV_VERSION
        with get_tracer().start_as_current_span("install uv binary") as span:
            span.set_attribute("uv.version", v)
            span.set_attribute("uv.image", image_ref(v))
            uv_bin = dag.container().from_(image_ref(v)).file("/uv")
            ctr = await (
                self.container.with_file("/uv/uv", uv_bin).with_env_variable("PATH", "/uv:${PATH}", expand=True).sync()
            )
        return self.with_container(ctr)

    @function
    async def ensure_uv(
        self,
        version: Annotated[
            str | None,
            Doc("uv version to install if missing. Defaults to the version detected from the workspace."),
        ] = None,
    ) -> "UvWorkspaceContainerBuilder":
        """Install the uv binary only if it is not already on $PATH.

        Checks for an existing uv and skips installation when found. Otherwise
        delegates to `with_uv` to copy the binary from the official image.
        """
        if await self._has_uv(self.container):
            return self
        return await self.with_uv(version)

    @function
    async def ensure_python(self) -> "UvWorkspaceContainerBuilder":
        """Install the workspace's pinned Python if `.python-version` was declared.

        Runs `uv python install <version>` when the build plan carries a
        `.python-version` pin. No-op when no pin is declared.
        """
        if not self.plan.python_version:
            return self
        return await self.with_python_install(self.plan.python_version)

    @function
    async def with_remote_sync(self) -> "UvWorkspaceContainerBuilder":
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
    ) -> "UvWorkspaceContainerBuilder":
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
    async def with_system_env(self) -> "UvWorkspaceContainerBuilder":
        """Configure uv to install into the system Python environment instead of a venv.

        Discovers the system site-packages directory and the Python binary location,
        then sets `UV_PROJECT_ENVIRONMENT` to that path, `UV_BREAK_SYSTEM_PACKAGES=1`,
        and prepends the Python `bin/` directory to `$PATH`.

        Use instead of `with_venv()` when packages should be installed system-wide
        (e.g. in a container image where a venv adds no value).
        Requires Python to be available in the container (run after `ensure_python`).
        """
        with get_tracer().start_as_current_span("configure system environment") as span:
            site_packages = (
                await self.container.with_exec(
                    ["python3", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                ).stdout()
            ).strip()
            python_bin = (
                await self.container.with_exec(
                    ["python3", "-c", "import sys, os; print(os.path.dirname(sys.executable))"],
                ).stdout()
            ).strip()
            span.set_attribute("uv.project_environment", site_packages)
            span.set_attribute("python.bin_dir", python_bin)

            ctr = await (
                self.container.with_env_variable("UV_PROJECT_ENVIRONMENT", site_packages)
                .with_env_variable("UV_BREAK_SYSTEM_PACKAGES", "1")
                .with_env_variable("PATH", f"{python_bin}:${{PATH}}", expand=True)
                .sync()
            )
        return self.with_container(ctr)

    @function
    async def with_python_install(
        self,
        version: Annotated[
            str,
            Doc("Python version to install via `uv python install` (e.g. `3.12`, `3.13.7`)."),
        ],
    ) -> "UvWorkspaceContainerBuilder":
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
    ) -> "UvWorkspaceContainerBuilder":
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
        after `with_remote_sync`/`with_local_sync`). Requires a relocatable venv
        built against a uv-managed Python; raises otherwise.
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
        resolved = posixpath.normpath(posixpath.join(self.plan.workspace_path, pkg.path))
        ctr_base = posixpath.normpath(posixpath.join(workdir, pkg.path))
        ctr = ctr.with_file(
            posixpath.join(ctr_base, "pyproject.toml"),
            self.plan.source_dir.file(posixpath.join(resolved, "pyproject.toml")),
        )
        if pkg.name in self.plan.flat_packages:
            return ctr
        src_name = pkg.module
        ctr = ctr.with_new_file(posixpath.join(ctr_base, "README.md"), "")
        if pkg.flat:
            ctr = ctr.with_new_file(posixpath.join(ctr_base, src_name, "__init__.py"), "")
        else:
            ctr = ctr.with_new_file(
                posixpath.join(ctr_base, "src", src_name, "__init__.py"),
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
    async def with_local_sources(self) -> "UvWorkspaceContainerBuilder":
        """Scaffold needed local package stubs (pyproject.toml + empty src/) into the container."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.needed_local, workdir, "scaffold local dependencies")
        return UvWorkspaceContainerBuilder(container=ctr, plan=self.plan)

    @function
    async def with_all_workspace_members(self) -> "UvWorkspaceContainerBuilder":
        """Like with_local_sources but scaffolds every local package, not just transitive deps."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.all_local, workdir, "scaffold all workspace members")
        return UvWorkspaceContainerBuilder(container=ctr, plan=self.plan)

    @function
    def with_container(
        self,
        container: Annotated[
            dagger.Container,
            Doc("Replacement container (e.g. after installing non-Python packages)"),
        ],
    ) -> "UvWorkspaceContainerBuilder":
        """Return a new builder with a different container but the same plan."""
        return UvWorkspaceContainerBuilder(container=container, plan=self.plan)

    def _copy_package(self, ctr: dagger.Container, workdir: str, pkg: LocalPackage) -> dagger.Container:
        """Copy a single local package's real source into the container."""
        resolved = posixpath.normpath(posixpath.join(self.plan.workspace_path, pkg.path))
        ctr_base = posixpath.normpath(posixpath.join(workdir, pkg.path))
        if pkg.flat:
            src_name = pkg.module
            ctr = ctr.with_directory(
                posixpath.join(ctr_base, src_name),
                self.plan.source_dir.directory(posixpath.join(resolved, src_name)),
            )
        else:
            ctr = ctr.with_directory(
                posixpath.join(ctr_base, "src"),
                self.plan.source_dir.directory(posixpath.join(resolved, "src")),
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

    async def _sync_local(self, ctr: dagger.Container) -> dagger.Container:
        """Run the plan's `uv sync` to install the local members, under a span."""
        with get_tracer().start_as_current_span("install local dependencies") as span:
            span.set_attribute("uv.sync_args", self.plan.uv_sync_args)
            return await ctr.with_exec(self.plan.uv_sync_args).sync()

    @function
    async def with_local_sync(self) -> "UvWorkspaceContainerBuilder":
        """Install the scaffolded local packages, copying their real source at the right time.

        For **editable** installs (the default), run `uv sync` against the package
        stubs from `with_local_sources`, then copy real source over the stubs last.
        Editable installs are path links, so the source goes live without a re-sync —
        meaning source-only changes don't invalidate the cached install layer.

        For **non-editable** installs (`no_editable=True`), `uv sync` builds a wheel
        from whatever source is present and bakes it into `site-packages`, so the real
        source must be copied in *before* the sync — there are no path links for a
        copy-last to make live, and syncing against the stubs would bake empty modules.
        """
        workdir = await self.container.workdir()
        if self.plan.no_editable:
            ctr = await self._copy_sources(self.container, workdir)
            ctr = await self._sync_local(ctr)
        else:
            ctr = await self._sync_local(self.container)
            ctr = await self._copy_sources(ctr, workdir)
        return self.with_container(ctr)
