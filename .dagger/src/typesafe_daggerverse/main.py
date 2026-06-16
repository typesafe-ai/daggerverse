"""Checks for typesafe-ai daggerverse modules."""

import anyio
from collections.abc import Awaitable
from typing import Annotated, TYPE_CHECKING

import dagger
from dagger import DefaultPath, Doc, check, dag, field, function, object_type
from dagger.telemetry import get_tracer


if TYPE_CHECKING:
    import dagger.UvWorkspaceSource


_PYTEST_MODULE_REF = "github.com/dagger/pytest@main"
_PYTEST_MODULE_PIN = "dd183e94449051abdc3c7d745dd148fdc08396d4"

# Top-level modules that ship a docs site are discovered by globbing for a
# `zensical.toml` (see `TypesafeDaggerverse.docs`).
_ZENSICAL_GLOB = "*/zensical.toml"

# Self-contained project holding the docs landing page (served at `/`).
_LANDING_PROJECT = ".docs"


def _pytest_otel_source() -> dagger.Directory:
    return dag.module_source(_PYTEST_MODULE_REF, ref_pin=_PYTEST_MODULE_PIN).directory("pytest_otel")


def _with_pytest_otel(ctr: dagger.Container) -> dagger.Container:
    """Install pytest_otel into an existing container for OTel test spans."""
    return ctr.with_directory("/opt/pytest_otel", _pytest_otel_source()).with_exec(
        ["uv", "pip", "install", "/opt/pytest_otel"]
    )


def _base() -> dagger.Container:
    return (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.14-bookworm")
        .with_workdir("/workspace")
        # caller picks the project env.
        .with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
        .with_env_variable("UV_SYSTEM_PYTHON", "1")
        .with_env_variable("UV_BREAK_SYSTEM_PACKAGES", "1")
    )


def _assert_modules_script(present: list[str], absent: list[str]) -> str:
    """Python snippet that asserts the given modules are importable (or not)."""
    return (
        "import importlib, sys\n"
        f"present = {present!r}\n"
        f"absent = {absent!r}\n"
        "for name in present:\n"
        "    importlib.import_module(name)\n"
        "for name in absent:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ModuleNotFoundError:\n"
        "        continue\n"
        "    sys.exit(f'expected {name!r} to be absent but import succeeded')\n"
    )


@object_type
class TypesafeDaggerverse:
    """CI checks for modules published from this repo."""

    source: Annotated[
        dagger.Directory,
        Doc("Daggerverse repo root"),
        DefaultPath("."),
    ] = field()

    def _standalone(self) -> "dagger.UvWorkspaceSource":
        return dag.uv(source=self.source.directory("uv/tests/_packages/standalone-app")).workspace()

    def _workspace(self) -> "dagger.UvWorkspaceSource":
        return dag.uv(source=self.source.directory("uv/tests/_packages/workspace")).workspace()

    def _workspace_app(self) -> "dagger.UvWorkspaceSource":
        return dag.uv(source=self.source.directory("uv/tests/_packages/workspace-app")).workspace()

    def _workspace_flat(self) -> "dagger.UvWorkspaceSource":
        return dag.uv(source=self.source.directory("uv/tests/_packages/workspace-flat")).workspace()

    def _partial_workspace(self) -> "dagger.UvWorkspaceSource":
        """A workspace where one local dep in uv.lock doesn't exist in the source tree."""
        return dag.uv(source=self.source.directory("uv/tests/_packages/partial-workspace")).workspace(
            path="sub-project"
        )

    def _nested_standalone(self) -> "dagger.UvWorkspaceSource":
        """A standalone project nested under a non-root path with a relative path dep."""
        return dag.uv(source=self.source.directory("uv/tests/_packages/nested-standalone")).workspace(path="app")

    def _uv_self(self) -> "dagger.UvWorkspaceSource":
        """The uv module built against its own source.

        uv is itself a Dagger module — `install()` detects the
        `dagger.json` and runs codegen so `sdk/` is materialized
        before `uv sync`.
        """
        return dag.uv(source=self.source.directory("uv")).workspace()

    def _github(self) -> "dagger.UvWorkspaceSource":
        return dag.uv(source=self.source.directory("github")).workspace()

    @function(cache="never")
    async def wait_dagger_checks(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[dagger.Secret, Doc("GitHub token with read access")],
        fail_fast: Annotated[bool, Doc("Raise as soon as any check fails instead of waiting for all.")] = False,
    ) -> str:
        """Block until every Dagger check has landed as a successful GitHub commit
        status on `ref`."""
        return (
            await dag.github()
            .status_monitor()
            .wait_for_dagger_checks(repo=repo, ref=ref, token=token, fail_fast=fail_fast)
        )

    # ---- docs sites ----

    async def _zensical_site(self, ctr: dagger.Container, source: dagger.Directory) -> dagger.Directory:
        """Render a zensical site inside `ctr` (which must have zensical installed).

        `source` supplies `docs/` + `zensical.toml`; zensical writes the built
        site next to the config, which we return.
        """
        workdir = await ctr.workdir()
        return (
            ctr.with_directory(f"{workdir}/docs", source.directory("docs"))
            .with_file(f"{workdir}/zensical.toml", source.file("zensical.toml"))
            .with_exec(["zensical", "build", "-f", "zensical.toml", "--clean"])
            .directory(f"{workdir}/site")
        )

    @function
    async def docs_site(
        self,
        module: Annotated[str, Doc("Module directory whose docs to build (e.g. 'uv')")],
    ) -> dagger.Directory:
        """Build a module's static docs site and return the generated `site/` dir.

        Built on the same base as the tests, with the module installed and its
        `docs` uv group (which ships zensical) synced — so docs that introspect
        the codebase can import it. `build` only copies each package's `src/`, so
        the docs sources are supplied separately by `_zensical_site`.
        """
        src = self.source.directory(module)
        ctr = await dag.uv(source=src).workspace().install(package=[module], base_container=_base(), group=["docs"])
        return await self._zensical_site(ctr, src)

    async def _landing(self) -> dagger.Directory:
        """Build the landing site (served at `/`) from the `.docs` project.

        `.docs` is a self-contained, non-packaged uv project whose `docs` group
        (zensical, pinned in `.docs/uv.lock`) is synced with no package — same
        build path as the modules, just nothing to import.
        """
        src = self.source.directory(_LANDING_PROJECT)
        ctr = await dag.uv(source=src).workspace().install(base_container=_base(), group=["docs"], dagger_codegen=False)
        return await self._zensical_site(ctr, src)

    @function
    async def docs_build(self) -> dagger.Directory:
        """Build the combined docs site for GitHub Pages.

        Discovers every top-level module with a `zensical.toml`, builds each site
        in parallel, and lays them out under their module path (e.g. `uv` -> `/uv`).
        Each site's links/assets are relative, so they resolve under their subpath.
        The `.docs` project's zensical site is the landing page (served at `/`).
        """
        # Dot-dirs (`.docs`, `.dagger`, ...) are tooling, not published modules.
        modules = sorted(
            module
            for module in (path.rsplit("/", 1)[0] for path in await self.source.glob(_ZENSICAL_GLOB))
            if not module.startswith(".")
        )

        # Built sites keyed by mount path; "" is the root landing page.
        built: dict[str, dagger.Directory] = {}

        async def _build(path: str, site: Awaitable[dagger.Directory]) -> None:
            built[path] = await site

        async with anyio.create_task_group() as tg:
            tg.start_soon(_build, "", self._landing())
            for module in modules:
                tg.start_soon(_build, module, self.docs_site(module))

        combined = built.pop("")
        for module in sorted(built):
            combined = combined.with_directory(module, built[module])
        return combined

    @function
    async def docs_serve(
        self,
        port: Annotated[int, Doc("Port to listen on")] = 8080,
    ) -> dagger.Service:
        """Serve the combined docs site over HTTP as a Dagger service.

        Uses Python's stdlib `http.server` (already in the uv base image), which
        serves `index.html` for directory URLs — what zensical's relative links
        expect. Run with `dagger call serve-docs up` and browse the printed URL.
        """
        site = await self.docs_build()
        return (
            _base()
            .with_directory("/srv/docs", site)
            .with_workdir("/srv/docs")
            .with_exposed_port(port)
            .as_service(args=["python", "-m", "http.server", str(port)])
        )

    @check
    @function
    async def docs(self) -> None:
        """Build every zensical docs site in parallel."""
        await (await self.docs_build()).sync()

    # ---- uv (audit) module checks ----

    def _uv_src(self, pyproject: str, *, uv_toml: str | None = None) -> dagger.Directory:
        """A single-workspace source tree for exercising the `uv` module.

        `uv_version`/`workspaces` only read pyproject.toml / uv.toml, so an
        empty uv.lock is enough for discovery.
        """
        d = dag.directory().with_new_file("uv.lock", "").with_new_file("pyproject.toml", pyproject)
        if uv_toml is not None:
            d = d.with_new_file("uv.toml", uv_toml)
        return d

    @check
    @function
    async def all_uv(self) -> None:
        """Run all checks for the `uv` module in parallel."""
        tracer = get_tracer()

        async def _run(name: str, fn) -> None:
            with tracer.start_as_current_span(name):
                await fn()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run, "detect_version_pyproject", self.uv_detect_version_pyproject)
            tg.start_soon(_run, "detect_version_uv_toml_precedence", self.uv_detect_version_uv_toml_precedence)
            tg.start_soon(_run, "detect_version_range", self.uv_detect_version_range)
            tg.start_soon(_run, "detect_version_default", self.uv_detect_version_default)
            tg.start_soon(_run, "discovers_workspaces", self.uv_discovers_workspaces)
            tg.start_soon(_run, "audit_clean", self.uv_audit_clean)
            tg.start_soon(_run, "audit_detects_vulnerability", self.uv_audit_detects_vulnerability)
            tg.start_soon(_run, "audit_exclude", self.uv_audit_exclude)
            tg.start_soon(_run, "install_venv", self.uv_install_venv)
            tg.start_soon(
                _run, "relocatable_venv_runs_in_fresh_container", self.uv_relocatable_venv_runs_in_fresh_container
            )
            tg.start_soon(_run, "no_editable_bakes_local_source", self.uv_no_editable_bakes_local_source)
            tg.start_soon(_run, "build_workspace", self.uv_workspace_build_workspace)
            tg.start_soon(_run, "build_full_workspace", self.uv_workspace_build_full_workspace)
            tg.start_soon(_run, "build_workspace_app", self.uv_workspace_build_workspace_app)
            tg.start_soon(_run, "build_workspace_flat", self.uv_workspace_build_workspace_flat)
            tg.start_soon(_run, "build_standalone", self.uv_workspace_build_standalone)
            tg.start_soon(_run, "standalone_selective_extra", self.uv_workspace_standalone_selective_extra)
            tg.start_soon(_run, "standalone_all_extras", self.uv_workspace_standalone_all_extras)
            tg.start_soon(_run, "standalone_selective_group", self.uv_workspace_standalone_selective_group)
            tg.start_soon(_run, "standalone_all_groups", self.uv_workspace_standalone_all_groups)
            tg.start_soon(
                _run, "full_workspace_all_extras_all_groups", self.uv_workspace_full_workspace_all_extras_all_groups
            )
            tg.start_soon(_run, "build_self", self.uv_workspace_build_self)
            tg.start_soon(_run, "pytest_self", self.uv_workspace_pytest_self)
            tg.start_soon(_run, "build_partial_workspace", self.uv_workspace_build_partial_workspace)
            tg.start_soon(_run, "build_nested_standalone", self.uv_workspace_build_nested_standalone)
            tg.start_soon(_run, "auto_install_uv", self.uv_workspace_auto_install_uv)

    @function
    async def uv_detect_version_pyproject(self) -> None:
        """`uv_version` reads `[tool.uv].required-version` from pyproject.toml."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = "==0.5.0"\n')
        ws = (await dag.uv(source=src).get_workspaces())[0]
        version = await ws.uv_version()
        if version != "0.5.0":
            raise AssertionError(f"expected 0.5.0, got {version}")

    @function
    async def uv_detect_version_uv_toml_precedence(self) -> None:
        """uv.toml `required-version` takes precedence over pyproject.toml."""
        src = self._uv_src(
            '[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = "==0.9.0"\n',
            uv_toml='required-version = "==0.4.0"\n',
        )
        ws = (await dag.uv(source=src).get_workspaces())[0]
        version = await ws.uv_version()
        if version != "0.4.0":
            raise AssertionError(f"expected 0.4.0 (uv.toml wins), got {version}")

    @function
    async def uv_detect_version_range(self) -> None:
        """A range specifier resolves to its minimal compatible version (no PyPI lookup)."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = ">=0.5.0,<0.5.5"\n')
        ws = (await dag.uv(source=src).get_workspaces())[0]
        version = await ws.uv_version()
        if version != "0.5.0":
            raise AssertionError(f"expected 0.5.0, got {version}")

    @function
    async def uv_detect_version_default(self) -> None:
        """With no required-version, `uv_version` falls back to the default tag."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n')
        ws = (await dag.uv(source=src).get_workspaces())[0]
        version = await ws.uv_version()
        if version != "latest":
            raise AssertionError(f"expected latest, got {version}")

    @function
    async def uv_discovers_workspaces(self) -> None:
        """`workspaces` finds one workspace per uv.lock in the source tree."""
        pyproject = '[project]\nname = "x"\nversion = "0"\n'
        src = (
            dag.directory()
            .with_new_file("a/uv.lock", "")
            .with_new_file("a/pyproject.toml", pyproject)
            .with_new_file("b/c/uv.lock", "")
            .with_new_file("b/c/pyproject.toml", pyproject)
        )
        workspaces = await dag.uv(source=src).get_workspaces()
        if len(workspaces) != 2:
            raise AssertionError(f"expected 2 workspaces, got {len(workspaces)}")

    @function
    async def uv_audit_clean(self) -> None:
        """Auditing a workspace with no vulnerable deps passes."""
        src = self.source.directory("uv/tests/_packages/clean")
        await dag.uv(source=src).audit()

    @function
    async def uv_audit_detects_vulnerability(self) -> None:
        """Auditing a workspace with a known-vulnerable dependency fails.

        (The audit report itself is folded into each workspace's trace span by
        `Uv.audit`; the message construction is covered by the `format_audit_failure`
        unit tests, since a check can't introspect OTel spans.)
        """
        src = self.source.directory("uv/tests/_packages/vulnerable")
        try:
            await dag.uv(source=src).audit()
        except Exception:
            return
        raise AssertionError("expected uv audit to fail for the vulnerable fixture")

    @function
    async def uv_audit_exclude(self) -> None:
        """`exclude` skips matching workspaces, so a vulnerable-but-excluded tree passes."""
        src = (
            dag.directory()
            .with_directory("clean", self.source.directory("uv/tests/_packages/clean"))
            .with_directory("vulnerable", self.source.directory("uv/tests/_packages/vulnerable"))
        )
        await dag.uv(source=src).audit(exclude=["**/vulnerable"])

    @function
    async def uv_install_venv(self) -> None:
        """`install` with `venv=True` creates a relocatable venv via `uv venv`, then syncs into it.

        Exercises the bare-image default (uv provisions Python), the `with_venv`
        step, and local-package scaffolding; then confirms deps import from the venv.
        """
        src = self.source.directory("uv/tests/_packages/workspace")
        # Object-returning module functions chain lazily; await only the terminal scalar.
        out = await (
            dag.uv(source=src)
            .workspace()
            .install(package=["my-app"], venv=True, venv_relocatable=True)
            .with_exec(["uv", "run", "python", "-c", "import my_app, my_core; print('VENV_OK')"])
            .stdout()
        )
        if "VENV_OK" not in out:
            raise AssertionError(f"expected venv install to import workspace packages, got: {out!r}")

    @function
    async def uv_relocatable_venv_runs_in_fresh_container(self) -> None:
        """`copy_venv` yields a runnable env in a container that has no Python at all.

        Builds standalone-app's `viz` extra into a relocatable venv, then uses
        `copy_venv` (default `.venv` resolved against the workdir, `set_env_vars`)
        to drop the venv **and the uv-managed standalone Python it bundles** into a
        plain `debian:bookworm-slim` image — no Python, no uv, no project. We first
        assert the base really has no Python, then run plain `python` (resolved via
        the venv's `PATH`) and import the installed dep (`tabulate`).

        The bundled Python matches the build base's libc — here the default Debian
        (glibc) base, so the target is glibc (debian-slim). Building on an Alpine uv
        base would instead produce a musl venv for alpine targets.
        """
        src = self.source.directory("uv/tests/_packages/standalone-app")
        base = dag.container().from_("debian:bookworm-slim").with_workdir("/srv/app")
        # Sanity-check the premise: the base ships no Python of its own.
        probe = await base.with_exec(["sh", "-c", "command -v python3 || command -v python || echo NO_PYTHON"]).stdout()
        if "NO_PYTHON" not in probe:
            raise AssertionError(f"expected the base image to have no Python, but found: {probe!r}")

        # Object-returning module functions chain lazily; await only the terminal scalar.
        out = await (
            dag.uv(source=src)
            .workspace()
            .build(extra=["viz"])
            .with_venv(relocatable=True)
            .with_remote_dependencies()
            .copy_venv(base, set_env_vars=True)
            .with_exec(["python", "-c", "import tabulate; print('FRESH_VENV_OK')"])
            .stdout()
        )
        if "FRESH_VENV_OK" not in out:
            raise AssertionError(f"expected the copied venv to run without a base Python, got: {out!r}")

    @function
    async def uv_no_editable_bakes_local_source(self) -> None:
        """`no_editable=True` bakes real local-package *source* into site-packages.

        Regression test for the editable-only copy-last optimization in
        `with_local_dependencies`: under `no_editable`, `uv sync` builds wheels from
        whatever source is on disk at sync time, so real source must be copied in
        *before* the sync — otherwise the empty scaffold stubs get baked into
        site-packages. We export the venv into a fresh Python-less container (so
        leftover workspace source can't mask the install) and assert each local
        package's `hello()` returns its real string. Asserting the *return value*,
        not just importability, is the point: an empty stub still imports fine.
        """
        src = self.source.directory("uv/tests/_packages/workspace")
        base = dag.container().from_("debian:bookworm-slim").with_workdir("/srv/app")
        script = (
            "import my_app, my_core, my_lib\n"
            "got = (my_app.hello(), my_core.hello(), my_lib.hello())\n"
            "want = ('Hello from my-app!', 'Hello from my-core!', 'Hello from my-lib!')\n"
            "assert got == want, f'baked source mismatch: {got!r}'\n"
            "print('NO_EDITABLE_OK')\n"
        )
        # Object-returning module functions chain lazily; await only the terminal scalar.
        out = await (
            dag.uv(source=src)
            .workspace()
            .venv(package=["my-app"], no_editable=True)
            .into(base, set_env_vars=True)
            .with_exec(["python", "-c", script])
            .stdout()
        )
        if "NO_EDITABLE_OK" not in out:
            raise AssertionError(f"expected non-editable venv to bake real local source, got: {out!r}")

    @function
    async def repro_directory_symlink_roundtrip(self) -> str:
        """Minimal repro of the Dagger v0.21.4 behavior that broke `copy_venv`.

        Mirrors what `copy_venv` did: extract `Container.directory(<symlinked dir path>)`,
        then mount it back into a fresh container at that same path and read it.
        `/store/link -> /store/real` stands in for uv's `cpython-<minor> -> cpython-<patch>`.

        Observed on v0.21.4: the destination gets the bare symlink (`link -> /store/real`,
        now dangling) and `marker.txt` is MISSING — the round-trip drops the real contents.
        (v0.21.0 materialized the followed contents.) That dangling symlink is why the
        copied venv's `bin/python` couldn't resolve. Workaround: export the *parent* dir so
        the symlink and its target travel together (see `UvVenv.create`).
        """
        built = (
            dag.container()
            .from_("debian:bookworm-slim")
            .with_exec(
                [
                    "sh",
                    "-c",
                    "mkdir -p /store/real && echo hi > /store/real/marker.txt && ln -s /store/real /store/link",
                ]
            )
        )
        fresh = (
            dag.container().from_("debian:bookworm-slim").with_directory("/store/link", built.directory("/store/link"))
        )
        return await fresh.with_exec(
            ["sh", "-c", "ls -la /store; echo ---; cat /store/link/marker.txt 2>&1 || echo MISSING"]
        ).stdout()

    @function
    async def uv_workspace_auto_install_uv(self) -> None:
        """Build with a base container that has no uv and verify auto_install_uv works."""
        bare = dag.container().from_("debian:bookworm-slim").with_workdir("/workspace")
        ctr = await self._workspace().install(package=["my-app"], base_container=bare)
        uv_path = await ctr.with_exec(["which", "uv"]).stdout()
        assert uv_path.strip() == "/uv/uv"

    @function
    async def uv_workspace_build_workspace(self) -> None:
        """Build the multi-package fixture and verify every local package imports."""
        ctr = await self._workspace().install(package=["my-app"], base_container=_base())
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_full_workspace(self) -> None:
        """Build the full workspace (no package filter) and verify every local package imports."""
        ctr = await self._workspace().install(all_packages=True, base_container=_base())
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_workspace_app(self) -> None:
        """Build a workspace where the target package is a flat app (no build-system)."""
        ctr = await self._workspace_app().install(package=["my-app"], base_container=_base())
        await ctr.with_exec(["python", "-c", "import my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_workspace_flat(self) -> None:
        """Build a workspace where dependencies use flat layout (no src/ directory)."""
        ctr = await self._workspace_flat().install(package=["my-app"], base_container=_base())
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_standalone(self) -> None:
        """Build the standalone fixture and verify it imports."""
        ctr = await self._standalone().install(package=["standalone-app"], base_container=_base())
        await ctr.with_exec(["python", "-c", "import standalone_app"]).stdout()

    @function
    async def uv_workspace_standalone_selective_extra(self) -> None:
        """`extra=['viz']` installs only the viz extra's deps, not other extras or groups."""
        ctr = await self._standalone().install(package=["standalone-app"], base_container=_base(), extra=["viz"])
        script = _assert_modules_script(
            present=["tabulate"],
            absent=["idna", "six", "packaging"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_all_extras(self) -> None:
        """`all_extras=True` installs every extra but no groups."""
        ctr = await self._standalone().install(package=["standalone-app"], base_container=_base(), all_extras=True)
        script = _assert_modules_script(
            present=["tabulate", "idna"],
            absent=["six", "packaging"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_selective_group(self) -> None:
        """`group=['docs']` installs only the docs group's deps, not other groups or extras."""
        ctr = await self._standalone().install(package=["standalone-app"], base_container=_base(), group=["docs"])
        script = _assert_modules_script(
            present=["six"],
            absent=["packaging", "tabulate", "idna"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_all_groups(self) -> None:
        """`all_groups=True` installs every group but no extras."""
        ctr = await self._standalone().install(package=["standalone-app"], base_container=_base(), all_groups=True)
        script = _assert_modules_script(
            present=["six", "packaging"],
            absent=["tabulate", "idna"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_full_workspace_all_extras_all_groups(self) -> None:
        """Full workspace build with all extras (from my-app) and all groups (from root) installed."""
        ctr = await self._workspace().install(
            all_packages=True, all_extras=True, all_groups=True, base_container=_base()
        )
        script = _assert_modules_script(
            present=["tabulate", "idna", "six", "packaging"],
            absent=[],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_build_self(self) -> None:
        """Build the uv module from its own source on a fresh tree."""
        ctr = await self._uv_self().install(package=["uv"], base_container=_base())
        await ctr.with_exec(["python", "-c", "import uv"]).stdout()

    @check
    @function
    async def github_pytest(self) -> None:
        """Run the github module's unit-test suite inside a freshly-built
        container."""
        ctr = await self._github().install(package=["github"], base_container=_base(), all_groups=True)
        workdir = await ctr.workdir()
        tests = self.source.directory("github").directory("tests")
        ctr = ctr.with_directory(f"{workdir}/tests", tests)
        await _with_pytest_otel(ctr).with_exec(["pytest", "-q"]).stdout()

    @check
    @function
    async def twingate_build(self) -> str:
        """Build the Twingate client container (no credentials needed)."""
        tg = dag.twingate(service_key=dag.set_secret("dummy", "{}"))
        return await tg.ctr().with_exec(["twingated", "--version"]).stdout()

    @function
    async def uv_workspace_pytest_self(self) -> None:
        """Run the uv module's own pytest suite inside a freshly-built container.

        `install()` only copies `src/` for each package (to keep layer
        granularity tight). `tests/` has to be mounted explicitly so
        pytest can find them — without this, `pytest -q` collects nothing
        and exits with code 5 (NO_TESTS_COLLECTED).
        """
        ctr = await self._uv_self().install(package=["uv"], base_container=_base(), all_groups=True)
        workdir = await ctr.workdir()
        tests = self.source.directory("uv").directory("tests")
        ctr = ctr.with_directory(f"{workdir}/tests", tests)
        await _with_pytest_otel(ctr).with_exec(["pytest", "-q"]).stdout()

    def _ruff_dirty_source(self) -> dagger.Directory:
        """A directory with a deliberately messy Python file for ruff to fix."""
        return dag.directory().with_new_file(
            "dirty.py",
            contents=("import os, sys, json\nimport os\nx=  1\ny = [1,2,3,]\nif x == True:\n    pass\n"),
        )

    async def _assert_changeset_only_modifies(self, changeset: dagger.Changeset, expected: set[str]) -> str:
        added = await changeset.added_paths()
        modified = await changeset.modified_paths()
        removed = await changeset.removed_paths()
        if added:
            msg = f"unexpected added paths: {added}"
            raise AssertionError(msg)
        if removed:
            msg = f"unexpected removed paths: {removed}"
            raise AssertionError(msg)
        if set(modified) != expected:
            msg = f"expected modified {expected}, got {set(modified)}"
            raise AssertionError(msg)
        return await changeset.as_patch().contents()

    @check
    @function
    async def all_ruff(self) -> None:
        """Run all checks for the `ruff` module in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.ruff_check_fix)
            tg.start_soon(self.ruff_format_fix)
            tg.start_soon(self.ruff_check_lint_clean)
            tg.start_soon(self.ruff_format_lint_clean)

    @function
    async def ruff_check_fix(self) -> str:
        """Run ruff check --fix on a dirty file and verify only dirty.py is changed."""
        ruff = dag.ruff()
        changeset = ruff.check().fix(source=self._ruff_dirty_source())
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @function
    async def ruff_format_fix(self) -> str:
        """Run ruff format on a dirty file and verify only dirty.py is changed."""
        ruff = dag.ruff()
        changeset = ruff.format().fix(source=self._ruff_dirty_source())
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @function
    async def ruff_check_lint_clean(self) -> str:
        """Run ruff check on a clean file and verify it passes."""
        clean = dag.directory().with_new_file(
            "clean.py",
            contents="x = 1\ny = [1, 2, 3]\n",
        )
        return await dag.ruff().check().lint(source=clean)

    @function
    async def ruff_format_lint_clean(self) -> str:
        """Run ruff format --check on a clean file and verify it passes."""
        clean = dag.directory().with_new_file(
            "clean.py",
            contents="x = 1\ny = [1, 2, 3]\n",
        )
        return await dag.ruff().format().lint(source=clean)

    @function
    async def uv_workspace_build_partial_workspace(self) -> None:
        """Build a project whose uv.lock references local deps that don't exist in the source tree.

        The missing dep (ext-pkg at ../../gone/ext-pkg) should be silently
        skipped. The present dep (my-dep at ../my-dep) should be installed.
        """
        ctr = await self._partial_workspace().install(package=["sub-project"], base_container=_base(), group=["dev"])
        script = _assert_modules_script(
            present=["my_dep"],
            absent=["ext_pkg"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_build_nested_standalone(self) -> None:
        """Build a standalone project at a non-root workspace path with a relative path dep.

        Regression test: when workspace_path != "." the container must mirror
        the repo's directory structure so that relative paths like ../lib in
        uv.lock resolve correctly. Also verifies dev deps are installed.
        """
        ctr = await self._nested_standalone().install(base_container=_base(), group=["dev"])
        script = _assert_modules_script(
            present=["six", "lib_pkg"],
            absent=[],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()
