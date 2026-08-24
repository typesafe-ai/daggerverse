from __future__ import annotations

import posixpath
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type
from dagger.telemetry import get_tracer

from uv.args import LockTimeout, MaxCacheSize, PruneCache
from uv.utils import _DEFAULT_BASE_UV_VERSION, image_ref
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

    async def _exec_step(self, span_name: str, argv: list[str], attributes: dict[str, object]) -> UvWorkspaceBuild:
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
    async def with_uv(
        self,
        version: Annotated[
            str | None,
            Doc("uv version to install. Defaults to the version detected from the workspace."),
        ] = None,
    ) -> UvWorkspaceBuild:
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
    async def with_remote_dependencies(
        self,
        prune_cache: PruneCache = True,
        max_cache_size: MaxCacheSize = 100,
        lock_timeout: LockTimeout = 600,
    ) -> UvWorkspaceBuild:
        """Install remote (non-local) dependencies via `uv sync --no-install-local`.

        When `prune_cache` is set (the default), the
        install is followed by a size-gated `uv cache prune --ci` (see
        `with_cache_prune`): the cache is pruned only once it exceeds
        `max_cache_size` GiB, and `lock_timeout` bounds how long that prune waits
        for the cache lock.
        """
        args = [*self.plan.uv_sync_args, "--no-install-local"]
        build = await self._exec_step("install remote dependencies", args, {"uv.sync_args": args})
        if prune_cache:
            build = await build.with_cache_prune(max_cache_size=max_cache_size, lock_timeout=lock_timeout)
        return build

    @function
    async def with_cache_prune(
        self,
        max_cache_size: MaxCacheSize = 100,
        lock_timeout: LockTimeout = 600,
    ) -> UvWorkspaceBuild:
        """Prune the uv cache with `uv cache prune --ci`, gated on cache size.

        Measures the cache dir (`uv cache dir`) and only prunes when it exceeds `max_cache_size`
        GiB, so most builds skip the prune (and never take the cache lock). Because
        `uv cache prune --ci` keeps only compiled wheels — nearly a full clean — one
        prune resets the footprint. `lock_timeout` sets `UV_LOCK_TIMEOUT` for the
        prune so concurrent builds sharing the cache volume wait instead of erroring.
        Set `max_cache_size=0` to prune on every build.

        The size check and conditional prune run in a single exec so the live cache
        volume is measured each time this step actually runs, rather than reading a
        stale, separately-cached size.

        Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration).
        """
        limit_kib = max_cache_size * 1024 * 1024  # GiB -> KiB, to compare with `du -sk`
        gate = f'[ "${{used_kib:-0}}" -gt {limit_kib} ]' if max_cache_size > 0 else "true"
        # `-x` keeps du on the cache mount's own filesystem (don't wander into
        # other mounts nested under it).
        script = (
            'used_kib=$(du -skx "$(uv cache dir)" 2>/dev/null | cut -f1 || echo 0); '
            f"if {gate}; then "
            f'echo "uv cache: ${{used_kib}} KiB used > {limit_kib} KiB limit; pruning" >&2; '
            f"UV_LOCK_TIMEOUT={lock_timeout} uv cache prune --ci; "
            "else "
            f'echo "uv cache: ${{used_kib}} KiB used <= {limit_kib} KiB limit; skipping prune" >&2; '
            "fi"
        )
        argv = ["sh", "-c", script]
        with get_tracer().start_as_current_span("prune uv cache") as span:
            span.set_attribute("uv.cache_max_size_gib", max_cache_size)
            span.set_attribute("uv.cache_lock_timeout", lock_timeout)
            executed = self.container.with_exec(argv)
            ctr = await executed.sync()
            # uv (and the gate) report on stderr what was measured / removed; surface
            # it so the span shows the outcome rather than just the command that ran.
            summary = (await executed.stderr()).strip()
            if summary:
                span.set_attribute("uv.cache_prune_summary", summary)
        return self.with_container(ctr)

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
    ) -> UvWorkspaceBuild:
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
    ) -> UvWorkspaceBuild:
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
    ) -> UvWorkspaceBuild:
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

    def _scaffold_package(self, overlay: dagger.Directory, workdir: str, pkg: LocalPackage) -> dagger.Directory:
        """Scaffold a single package stub (pyproject.toml + README + empty module)."""
        ctr_base = posixpath.normpath(posixpath.join(workdir, pkg.path))
        overlay = overlay.with_new_file(
            posixpath.join(ctr_base, "pyproject.toml").lstrip("/"),
            pkg.pyproject_contents,
        )
        if pkg.name in self.plan.flat_packages:
            return overlay
        src_name = pkg.module
        overlay = overlay.with_new_file(posixpath.join(ctr_base, "README.md").lstrip("/"), "")
        if pkg.flat:
            overlay = overlay.with_new_file(posixpath.join(ctr_base, src_name, "__init__.py").lstrip("/"), "")
        else:
            overlay = overlay.with_new_file(
                posixpath.join(ctr_base, "src", src_name, "__init__.py").lstrip("/"),
                "",
            )
        return overlay

    async def _scaffold(
        self,
        packages: list[LocalPackage],
        workdir: str,
        span_name: str,
    ) -> dagger.Container:
        """Scaffold package stubs (pyproject.toml + README + empty module) for `packages`.

        The per-package `with_new_file` calls are lazy, so the span
        forces evaluation with `sync()` before closing; otherwise it would capture
        only Python graph-building and report ~zero duration.
        """
        overlay = dag.directory()
        with get_tracer().start_as_current_span(span_name) as span:
            span.set_attribute("packages.count", len(packages))
            span.set_attribute("packages.names", [pkg.name for pkg in packages])
            for pkg in packages:
                overlay = self._scaffold_package(overlay, workdir, pkg)
            ctr = self.container.with_directory("/", overlay)
            return await ctr.sync()

    @function
    async def with_workspace_files(self) -> UvWorkspaceBuild:
        """Scaffold needed local package stubs (pyproject.toml + empty src/) into the container."""
        workdir = await self.container.workdir()
        ctr = await self._scaffold(self.plan.needed_local, workdir, "scaffold local dependencies")
        return UvWorkspaceBuild(container=ctr, plan=self.plan)

    @function
    async def with_all_workspace_members(self) -> UvWorkspaceBuild:
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
    ) -> UvWorkspaceBuild:
        """Return a new UvWorkspaceBuild with a different container but the same plan."""
        return UvWorkspaceBuild(container=container, plan=self.plan)

    def _copy_package(self, overlay: dagger.Directory, workdir: str, pkg: LocalPackage) -> dagger.Directory:
        """Copy a single local package's real source into the container."""
        resolved = posixpath.normpath(posixpath.join(self.plan.workspace_path, pkg.path))
        ctr_base = posixpath.normpath(posixpath.join(workdir, pkg.path))
        if pkg.flat:
            src_name = pkg.module
            overlay = overlay.with_directory(
                posixpath.join(ctr_base, src_name).lstrip("/"),
                self.plan.source_dir.directory(posixpath.join(resolved, src_name)),
            )
        else:
            overlay = overlay.with_directory(
                posixpath.join(ctr_base, "src").lstrip("/"),
                self.plan.source_dir.directory(posixpath.join(resolved, "src")),
            )
        return overlay

    async def _copy_sources(self, ctr: dagger.Container, workdir: str) -> dagger.Container:
        """Copy real source for each needed local package into `ctr`.

        The per-package `with_directory` calls are lazy, so the span forces
        evaluation with `sync()` before closing; otherwise it would capture only
        Python graph-building and report ~zero duration.
        """
        overlay = dag.directory()
        with get_tracer().start_as_current_span("copy local dependency sources") as span:
            span.set_attribute("packages.count", len(self.plan.needed_local))
            span.set_attribute("packages.names", [pkg.name for pkg in self.plan.needed_local])
            for pkg in self.plan.needed_local:
                if pkg.name in self.plan.flat_packages:
                    continue
                overlay = self._copy_package(overlay, workdir, pkg)
            return await ctr.with_directory("/", overlay).sync()

    async def _sync_local(self, ctr: dagger.Container) -> dagger.Container:
        """Run the plan's `uv sync` to install the local members, under a span."""
        with get_tracer().start_as_current_span("install local dependencies") as span:
            span.set_attribute("uv.sync_args", self.plan.uv_sync_args)
            # `with_exec` is lazy; sync() inside the span so it captures the actual
            # install rather than just the query-graph construction.
            return await ctr.with_exec(self.plan.uv_sync_args).sync()

    @function
    async def with_local_dependencies(self) -> dagger.Container:
        """Install the scaffolded local packages, copying their real source at the right time.

        For **editable** installs (the default), run `uv sync` against the package
        stubs from `with_workspace_files`, then copy real source over the stubs last.
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
            return await self._sync_local(ctr)
        ctr = await self._sync_local(self.container)
        # Copy real source last: the editable installs above already point at these
        # paths, so the code goes live with no re-sync — keeping the install layer
        # cached across source-only changes.
        return await self._copy_sources(ctr, workdir)
