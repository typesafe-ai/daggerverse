"""Checks for typesafe-ai daggerverse modules."""

import posixpath
import tomllib
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import anyio
import yaml
from dagger import Doc, QueryError, check, function, object_type
from dagger.telemetry import get_tracer
from dagger_clients.core import Changeset, Check, Container, Directory, ReturnType, Secret, Service, Workspace, core
from dagger_clients.deptry import deptry
from dagger_clients.github import github
from dagger_clients.pyproject import pyproject
from dagger_clients.pyrefly import PyreflyWorkspace, pyrefly
from dagger_clients.ruff import ruff
from dagger_clients.setuptools_package_dir import setuptools_package_dir
from dagger_clients.twingate import twingate
from dagger_clients.uv import UvCooldownPolicy, UvUpgradeStrategy, uv
from dagger_clients.zensical import zensical

_PYTEST_MODULE_REF = "github.com/dagger/pytest@main"
_PYTEST_MODULE_PIN = "dd183e94449051abdc3c7d745dd148fdc08396d4"

# Self-contained project holding the docs landing page (served at `/`).
_LANDING_PROJECT = ".docs"

# Pinned open-source projects the `uv` module must build (see `uv_real_world`).
_REAL_WORLD_PROJECTS = "real-world.yaml"


def _pytest_otel_source() -> Directory:
    return core().module_source(_PYTEST_MODULE_REF, ref_pin=_PYTEST_MODULE_PIN).directory("pytest_otel")


def _with_pytest_otel(ctr: Container) -> Container:
    """Install pytest_otel into an existing container for OTel test spans."""
    return ctr.with_directory("/opt/pytest_otel", _pytest_otel_source()).with_exec(
        ["uv", "pip", "install", "/opt/pytest_otel"]
    )


class CheckFailedError(Exception):
    """A dependency's check ran and failed."""


async def _run_check(check: Check) -> None:
    """Run a check returned by a dependency's client, raising when it fails.

    A check called through a client comes back unrun; reading `pass_` runs it.
    """
    if not await check.pass_():
        raise CheckFailedError(await check.error().message())


def _ws() -> Workspace:
    return core().current_workspace()


def _fixture(path: str) -> Workspace:
    return _src(path).as_workspace()


def _src(path: str) -> Directory:
    return _ws().directory(f"/{path}", exclude=["**/.venv", "**/__pycache__", "**/.git"], gitignore=True)


def _base() -> Container:
    return (
        core()
        .container()
        .from_("ghcr.io/astral-sh/uv:python3.14-bookworm")
        .with_workdir("/workspace")
        # caller picks the project env.
        .with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
        .with_env_variable("UV_SYSTEM_PYTHON", "1")
        .with_env_variable("UV_BREAK_SYSTEM_PACKAGES", "1")
    )


def _go_test(module: str) -> Container:
    """Run a Go module's unit tests, on the Go image its Dagger entrypoint builds with."""
    return (
        core()
        .container()
        .from_("golang:1.26.1-alpine")
        .with_mounted_cache("/go/pkg/mod", core().cache_volume("go-mod"))
        .with_mounted_cache("/root/.cache/go-build", core().cache_volume("go-build"))
        .with_env_variable("CGO_ENABLED", "0")
        .with_directory("/src", _src(module))
        .with_workdir("/src")
        .with_exec(["go", "test", "./..."])
    )


def _assert_modules_script(present: list[str], absent: list[str]) -> str:
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


def _deptry_lock(root: str, source: str, groups: dict[str, list[str]]) -> str:
    """A uv.lock whose root project `root` (an `editable` or `virtual` source) locks deptry in `groups`.

    A group lists the deptry versions it locks; with more than one in the lock, each entry names its version.
    """
    versions = sorted({v for vs in groups.values() for v in vs})
    entry: Callable[[str], str] = (
        (lambda v: f'{{ name = "deptry", version = "{v}" }}')
        if len(versions) > 1
        else (lambda v: '{ name = "deptry" }')
    )
    lock = f'version = 1\n\n[[package]]\nname = "{root}"\nversion = "0"\nsource = {{ {source} = "." }}\n\n'
    lock += "[package.dev-dependencies]\n" + "".join(
        f"{group} = [{', '.join(entry(v) for v in vs)}]\n" for group, vs in groups.items()
    )
    for v in versions:
        lock += (
            f'\n[[package]]\nname = "deptry"\nversion = "{v}"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
        )
    return lock


@object_type
class TypesafeDaggerverse:
    """CI checks for modules published from this repo."""

    # The SDK's `function` overloads leave out `cache`.
    @function(cache="never")  # pyrefly: ignore[no-matching-overload]
    async def wait_dagger_checks(
        self,
        repo: Annotated[str, Doc("GitHub repo as 'owner/name'")],
        ref: Annotated[str, Doc("Commit SHA to poll")],
        token: Annotated[Secret, Doc("GitHub token with read access")],
        fail_fast: Annotated[bool, Doc("Raise as soon as any check fails instead of waiting for all.")] = False,
    ) -> str:
        """Block until every Dagger check has landed as a successful GitHub commit
        status on `ref`."""
        return (
            await github().status_monitor().wait_for_dagger_checks(repo=repo, ref=ref, token=token, fail_fast=fail_fast)
        )

    # ---- docs sites ----

    @function
    async def docs_build(self) -> Directory:
        """Build the combined docs site for GitHub Pages.

        The `zensical` module builds every docs site; each module's lands under its
        path (e.g. `uv` -> `/uv`), and the `.docs` project's is the landing page at
        `/`. Each site's links and assets are relative, so they resolve under their
        subpath.
        """
        sites = zensical(_ws()).projects()
        # Dot-dirs (`.docs`, `.dagger`, ...) are tooling, not published modules.
        modules = [path for path in await sites.keys() if not path.startswith(".")]
        combined = sites.get(_LANDING_PROJECT).site()
        for module in modules:
            combined = combined.with_directory(module, sites.get(module).site())
        return combined

    @function
    async def docs_serve(
        self,
        port: Annotated[int, Doc("Port to listen on")] = 8080,
    ) -> Service:
        """Serve the combined docs site over HTTP as a Dagger service.

        Uses Python's stdlib `http.server` (already in the uv base image), which
        serves `index.html` for directory URLs, as zensical's relative links
        expect. Run with `dagger call docs-serve up` and browse the printed URL.
        """
        site = await self.docs_build()
        return (
            _base()
            .with_directory("/srv/docs", site)
            .with_workdir("/srv/docs")
            .with_exposed_port(port)
            .as_service(args=["python", "-m", "http.server", str(port)])
        )

    # ---- uv (audit) module checks ----

    def _uv_src(self, pyproject: str, *, uv_toml: str | None = None) -> Directory:
        """A single-workspace source tree for exercising the `uv` module.

        `uv_version`/`workspaces` only read pyproject.toml / uv.toml, so an
        empty uv.lock is enough for discovery.
        """
        d = core().directory().with_new_file("uv.lock", "").with_new_file("pyproject.toml", pyproject)
        if uv_toml is not None:
            d = d.with_new_file("uv.toml", uv_toml)
        return d

    @staticmethod
    async def _uv_audit_all(src: Directory, subset: list[str] | None = None) -> None:
        """Audit every workspace in `src` (or the `subset` of them), failing unless each is clean,
        as `dagger check --generated` does."""
        workspaces = uv(src.as_workspace()).workspaces()
        if subset is not None:
            workspaces = workspaces.subset(subset)
        for workspace in await workspaces.list_():
            if not await workspace.audit().is_empty():
                raise AssertionError(f"expected no audit fixes for {await workspace.path()}")

    @staticmethod
    async def _uv_audit_fixes(fixture: str, **kwargs: Any) -> tuple[str, str]:
        """The fixed uv.lock and pyproject.toml `audit` generates for `fixture`."""
        after = uv(_fixture(fixture), **kwargs).workspace(".").audit().after()
        return await after.file("uv.lock").contents(), await after.file("pyproject.toml").contents()

    @staticmethod
    def _locked_version(lock: str, package: str) -> tuple[int, ...]:
        version = next(p["version"] for p in tomllib.loads(lock)["package"] if p["name"] == package)
        return tuple(int(part) for part in version.split("."))

    @check
    @function
    async def all_uv(self) -> None:
        """Run all checks for the `uv` module in parallel."""
        tracer = get_tracer()

        async def _run(name: str, fn: Callable[[], Awaitable[object]]) -> None:
            with tracer.start_as_current_span(name):
                await fn()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run, "detect_version_uv_toml_precedence", self.uv_detect_version_uv_toml_precedence)
            tg.start_soon(_run, "discovers_workspaces", self.uv_discovers_workspaces)
            tg.start_soon(_run, "filters_workspaces", self.uv_filters_workspaces)
            tg.start_soon(_run, "audit_detects_vulnerability", self.uv_audit_detects_vulnerability)
            tg.start_soon(_run, "audit_subset", self.uv_audit_subset)
            tg.start_soon(_run, "audit_exclude", self.uv_audit_exclude)
            tg.start_soon(_run, "audit_upgrades_latest", self.uv_audit_upgrades_latest)
            tg.start_soon(_run, "audit_upgrades_minimal", self.uv_audit_upgrades_minimal)
            tg.start_soon(_run, "audit_skip_upgrades", self.uv_audit_skip_upgrades)
            tg.start_soon(_run, "audit_respects_cooldown", self.uv_audit_respects_cooldown)
            tg.start_soon(_run, "audit_exempts_cooldown", self.uv_audit_exempts_cooldown)
            tg.start_soon(_run, "project_environment", self.uv_project_environment)
            tg.start_soon(
                _run, "relocatable_venv_runs_in_fresh_container", self.uv_relocatable_venv_runs_in_fresh_container
            )
            tg.start_soon(_run, "no_editable_bakes_local_source", self.uv_no_editable_bakes_local_source)
            tg.start_soon(_run, "license_files", self.uv_license_files)
            tg.start_soon(_run, "license_file_patterns", self.uv_license_file_patterns)
            tg.start_soon(_run, "workspace_layer_cache", self.uv_workspace_layer_cache)
            tg.start_soon(_run, "build_workspace_app", self.uv_workspace_build_workspace_app)
            tg.start_soon(_run, "build_workspace_flat", self.uv_workspace_build_workspace_flat)
            tg.start_soon(_run, "uv_build_layouts", self.uv_build_layouts)
            tg.start_soon(_run, "uv_build_backend_handler", self.uv_build_backend_handler)
            tg.start_soon(_run, "uv_maturin_outside_workspace", self.uv_maturin_outside_workspace)
            tg.start_soon(_run, "uv_build_input_above_package", self.uv_build_input_above_package)
            tg.start_soon(_run, "uv_maturin_editable_cached_build", self.uv_maturin_editable_cached_build)
            tg.start_soon(_run, "uv_maturin_target_cache_tracks_source", self.uv_maturin_target_cache_tracks_source)
            tg.start_soon(_run, "uv_maturin_dist", self.uv_maturin_dist)
            tg.start_soon(_run, "uv_workspace_inputs", self.uv_workspace_inputs)
            tg.start_soon(_run, "standalone_selective_extra", self.uv_workspace_standalone_selective_extra)
            tg.start_soon(_run, "standalone_selective_group", self.uv_workspace_standalone_selective_group)
            tg.start_soon(_run, "no_install_packages", self.uv_workspace_no_install_packages)
            tg.start_soon(_run, "builder_selection", self.uv_workspace_builder_selection)
            tg.start_soon(_run, "locked_packages", self.uv_workspace_locked_packages)
            tg.start_soon(
                _run, "full_workspace_all_extras_all_groups", self.uv_workspace_full_workspace_all_extras_all_groups
            )
            tg.start_soon(_run, "pytest_self", self.uv_workspace_pytest_self)
            tg.start_soon(_run, "build_partial_workspace", self.uv_workspace_build_partial_workspace)
            tg.start_soon(_run, "build_nested_standalone", self.uv_workspace_build_nested_standalone)
            tg.start_soon(_run, "auto_install_uv", self.uv_workspace_auto_install_uv)

    @function
    async def uv_detect_version_uv_toml_precedence(self) -> None:
        """uv.toml `required-version` takes precedence over pyproject.toml."""
        src = self._uv_src(
            '[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = "==0.9.0"\n',
            uv_toml='required-version = "==0.4.0"\n',
        )
        ws = (await uv(src.as_workspace()).workspaces().list_())[0]
        version = await ws.uv_version()
        if version != "0.4.0":
            raise AssertionError(f"expected 0.4.0 (uv.toml wins), got {version}")

    @function
    async def uv_discovers_workspaces(self) -> None:
        """`workspaces` finds one workspace per uv.lock in the source tree."""
        pyproject = '[project]\nname = "x"\nversion = "0"\n'
        src = (
            core()
            .directory()
            .with_new_file("a/uv.lock", "")
            .with_new_file("a/pyproject.toml", pyproject)
            .with_new_file("b/c/uv.lock", "")
            .with_new_file("b/c/pyproject.toml", pyproject)
        )
        paths = await uv(src.as_workspace()).workspaces().keys()
        if paths != ["a", "b/c"]:
            raise AssertionError(f"expected ['a', 'b/c'], got {paths}")

    @function
    async def uv_filters_workspaces(self) -> None:
        """`workspaces().filter` keeps the workspaces whose uv.lock locks every package in `has_packages`."""
        pyproject = '[project]\nname = "x"\nversion = "0"\n'

        def lock(*names: str) -> str:
            return "".join(f'[[package]]\nname = "{name}"\nversion = "0"\n\n' for name in names)

        src = (
            core()
            .directory()
            .with_new_file("a/uv.lock", lock("x", "pyrefly", "ruff"))
            .with_new_file("a/pyproject.toml", pyproject)
            .with_new_file("b/uv.lock", lock("x", "typing-extensions"))
            .with_new_file("b/pyproject.toml", pyproject)
        )
        workspaces = uv(src.as_workspace()).workspaces()
        cases: list[tuple[list[str], list[str]]] = [
            (["pyrefly"], ["a"]),
            (["pyrefly", "ruff"], ["a"]),
            (["pyrefly", "typing-extensions"], []),
            # Names match as uv.lock spells them.
            (["Typing_Extensions"], ["b"]),
            ([], ["a", "b"]),
        ]
        for has_packages, expected in cases:
            paths = await workspaces.filter(has_packages=has_packages).keys()
            if paths != expected:
                raise AssertionError(f"has_packages={has_packages}: expected {expected}, got {paths}")
        # A kept workspace still loads.
        path = await workspaces.filter(has_packages=["pyrefly"]).get("a").path()
        if path != "a":
            raise AssertionError(f"expected the workspace at 'a', got {path!r}")

    @function
    async def uv_audit_detects_vulnerability(self) -> None:
        """Auditing a workspace with a known-vulnerable dependency fails.

        Its only fix needs a looser constraint than `urllib3==2.6.3`, so there's nothing to
        generate. (The audit report itself is folded into the error by
        `UvWorkspaceSource.audit`; the message construction is covered by the
        `format_audit_failure` unit tests.)
        """
        src = _src("uv/tests/_packages/vulnerable")
        try:
            await self._uv_audit_all(src)
        except QueryError:
            return
        raise AssertionError("expected uv audit to fail for the vulnerable fixture")

    @function
    async def uv_audit_upgrades_latest(self) -> None:
        """`audit` upgrades a vulnerable package within its constraints, leaving the settings alone."""
        lock, pyproject = await self._uv_audit_fixes("uv/tests/_packages/upgradable")
        if self._locked_version(lock, "urllib3") < (2, 8, 0):
            raise AssertionError(f"expected urllib3 >= 2.8.0, got:\n{lock}")
        if "exclude-newer" in pyproject:
            raise AssertionError(f"expected no cooldown exemption, got:\n{pyproject}")

    @function
    async def uv_audit_upgrades_minimal(self) -> None:
        """`upgrade_strategy=MINIMAL` upgrades to the lowest version that fixes every advisory."""
        lock, _ = await self._uv_audit_fixes(
            "uv/tests/_packages/upgradable", upgrade_strategy=UvUpgradeStrategy.MINIMAL
        )
        if self._locked_version(lock, "urllib3") != (2, 8, 0):
            raise AssertionError(f"expected urllib3 == 2.8.0, got:\n{lock}")

    @function
    async def uv_audit_skip_upgrades(self) -> None:
        """`upgrade_strategy=SKIP` fails on a fixable vulnerability instead of upgrading it."""
        try:
            await (
                uv(_fixture("uv/tests/_packages/upgradable"), upgrade_strategy=UvUpgradeStrategy.SKIP)
                .workspace(".")
                .audit()
                .is_empty()
            )
        except QueryError:
            return
        raise AssertionError("expected audit to fail for the upgradable fixture with upgrade_strategy=SKIP")

    @function
    async def uv_audit_respects_cooldown(self) -> None:
        """A fix the cooldown holds back stays unapplied, but the partial upgrade before the cutoff is kept."""
        fixes = uv(_fixture("uv/tests/_packages/cooldown")).workspace(".").audit()
        lock = await fixes.after().file("uv.lock").contents()
        if self._locked_version(lock, "urllib3") != (2, 7, 0):
            raise AssertionError(f"expected urllib3 == 2.7.0 (the last release before the cutoff), got:\n{lock}")
        if await fixes.modified_paths() != ["uv.lock"]:
            raise AssertionError(f"expected only uv.lock to change, got {await fixes.modified_paths()}")

    @function
    async def uv_audit_exempts_cooldown(self) -> None:
        """`audit_cooldown=EXEMPT` admits just the fixing version past the cooldown, and records why."""
        lock, pyproject = await self._uv_audit_fixes(
            "uv/tests/_packages/cooldown", audit_cooldown=UvCooldownPolicy.EXEMPT
        )
        if self._locked_version(lock, "urllib3") != (2, 8, 0):
            raise AssertionError(f"expected urllib3 == 2.8.0, got:\n{lock}")
        exemption = tomllib.loads(pyproject)["tool"]["uv"].get("exclude-newer-package", {}).get("urllib3")
        if exemption != "2026-09-15T19:29:37Z" or "UvAudit[dagger]:" not in pyproject:
            raise AssertionError(f"expected a marked urllib3 exemption at its upload time, got:\n{pyproject}")

    @function
    async def uv_audit_subset(self) -> None:
        """A `subset` of the workspaces audits only those, so a vulnerable-but-unselected tree passes."""
        src = (
            core()
            .directory()
            .with_directory("clean", _src("uv/tests/_packages/clean"))
            .with_directory("vulnerable", _src("uv/tests/_packages/vulnerable"))
        )
        await self._uv_audit_all(src, subset=["clean"])

    @function
    async def uv_audit_exclude(self) -> None:
        """`audit_exclude` leaves a matching workspace unaudited, so a vulnerable one passes."""
        src = core().directory().with_directory("vulnerable", _src("uv/tests/_packages/vulnerable"))
        if not await uv(src.as_workspace(), audit_exclude=["vulnerable"]).workspace("vulnerable").audit().is_empty():
            raise AssertionError("expected no audit changes for an excluded workspace")

    @function
    async def uv_project_environment(self) -> None:
        """Where `install` puts packages, depending on the base container.

        - The default base, or a Python that doesn't match `requires-python`: a
          uv-managed Python, installed into directly.
        - A matching Python and no `UV_PROJECT_ENVIRONMENT`: an activated `.venv`, or an
          inactive one with `activate_venv=False`.
        - `UV_PROJECT_ENVIRONMENT` set: that environment, with no `.venv`.
        """
        src = _src("uv/tests/_packages/workspace")
        workspace = uv(src.as_workspace()).workspace(".")
        imports = "import my_app, my_core, sys; print(sys.prefix)"
        with_python = _base().without_env_variable("UV_PROJECT_ENVIRONMENT")

        async def prefix(ctr: Container) -> str:
            return (await ctr.with_exec(["python", "-c", imports]).stdout()).strip()

        async def has_venv(ctr: Container) -> bool:
            return await ctr.with_exec(["test", "-e", ".venv"], expect=ReturnType.ANY).exit_code() == 0

        managed = workspace.builder().with_packages(["my-app"]).with_dependencies().container()
        managed_prefix = await prefix(managed)
        if await has_venv(managed) or "/uv/python/" not in managed_prefix:
            raise AssertionError(f"expected a uv-managed Python and no .venv, got prefix {managed_prefix!r}")

        # Debian's Python 3.13 doesn't satisfy the fixture's `>=3.14`.
        mismatched = (
            workspace.builder(base_container=core().container().from_("ghcr.io/astral-sh/uv:0.11.19-debian"))
            .with_packages(["my-app"])
            .with_dependencies()
            .container()
        )
        mismatched_prefix = await prefix(mismatched)
        if await has_venv(mismatched) or "/uv/python/" not in mismatched_prefix:
            raise AssertionError(f"expected a uv-managed Python for a non-matching base, got {mismatched_prefix!r}")

        activated = (
            workspace.builder(base_container=with_python).with_packages(["my-app"]).with_dependencies().container()
        )
        venv = posixpath.join(await activated.workdir(), ".venv")
        if await activated.env_variable("VIRTUAL_ENV") != venv or await prefix(activated) != venv:
            raise AssertionError("expected an activated .venv when the base has Python")

        inactive = (
            workspace.builder(base_container=with_python, activate_venv=False)
            .with_packages(["my-app"])
            .with_dependencies()
            .container()
        )
        if not await has_venv(inactive) or await inactive.env_variable("VIRTUAL_ENV"):
            raise AssertionError("expected an inactive .venv with activate_venv=False")

        system = workspace.builder(base_container=_base()).with_packages(["my-app"]).with_dependencies().container()
        if await has_venv(system) or await prefix(system) != "/usr/local":
            raise AssertionError("expected UV_PROJECT_ENVIRONMENT=/usr/local to be installed into directly")

    @function
    async def uv_relocatable_venv_runs_in_fresh_container(self) -> None:
        """`copy_venv` yields a runnable env in a container that has no Python at all.

        Builds standalone-app's `viz` extra into a relocatable venv, then uses
        `copy_venv` (default `.venv` resolved against the workdir, `set_env_vars`)
        to drop the venv **and the uv-managed standalone Python it bundles** into a
        plain `debian:bookworm-slim` image with no Python, uv, or project. We first
        assert the base has no Python, then run plain `python` (resolved via
        the venv's `PATH`) and import the installed dep (`tabulate`).

        The bundled Python matches the build base's libc. Here that is the default
        Debian (glibc) base, so the target is glibc (debian-slim). Building on an Alpine uv
        base would instead produce a musl venv for alpine targets.
        """
        src = _src("uv/tests/_packages/standalone-app")
        base = core().container().from_("debian:bookworm-slim").with_workdir("/srv/app")
        # Sanity-check the premise: the base ships no Python of its own.
        probe = await base.with_exec(["sh", "-c", "command -v python3 || command -v python || echo NO_PYTHON"]).stdout()
        if "NO_PYTHON" not in probe:
            raise AssertionError(f"expected the base image to have no Python, but found: {probe!r}")

        # Object-returning module functions chain lazily; await only the terminal scalar.
        out = await (
            uv(src.as_workspace())
            .workspace(".")
            .builder(venv_relocatable=True)
            .with_extras(["viz"])
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
        *before* the sync. Otherwise the empty scaffold stubs get baked into
        site-packages. We export the venv into a fresh Python-less container (so
        leftover workspace source can't mask the install) and assert each local
        package's `hello()` returns its real string. Asserting the *return value*,
        not just importability, is the point: an empty stub still imports fine.
        """
        src = _src("uv/tests/_packages/workspace")
        base = core().container().from_("debian:bookworm-slim").with_workdir("/srv/app")
        script = (
            "import my_app, my_core, my_lib\n"
            "got = (my_app.hello(), my_core.hello(), my_lib.hello())\n"
            "want = ('Hello from my-app!', 'Hello from my-core!', 'Hello from my-lib!')\n"
            "assert got == want, f'baked source mismatch: {got!r}'\n"
            "print('NO_EDITABLE_OK')\n"
        )
        # Object-returning module functions chain lazily; await only the terminal scalar.
        out = await (
            uv(src.as_workspace())
            .workspace(".")
            .builder(no_editable=True, venv_relocatable=True)
            .with_packages(["my-app"])
            .with_dependencies()
            .venv()
            .into(base, set_env_vars=True)
            .with_exec(["python", "-c", script])
            .stdout()
        )
        if "NO_EDITABLE_OK" not in out:
            raise AssertionError(f"expected non-editable venv to bake real local source, got: {out!r}")

    @function
    async def uv_build_layouts(self) -> None:
        """Virtual members, renamed modules, multiple modules, custom roots, namespace and stub packages build together.

        Besides uv_build members, flit and pdm-backend members find a namespace and a stub package under `src/`.
        """
        source = _src("uv/tests/_packages/uv-build-layouts")
        source = source.with_new_file("flat/tests/unrelated.py", "Must stay out of the package build layer\n")
        script = (
            "from importlib.metadata import distribution, distributions\n"
            "from pathlib import Path\n"
            "import actual_package, compat_package, vendor_sdk, layout_ns.plugin, layout_flit.plugin\n"
            "assert actual_package.VALUE == compat_package.VALUE == vendor_sdk.VALUE == 'real source'\n"
            "assert layout_ns.plugin.VALUE == layout_flit.plugin.VALUE == 'real source'\n"
            "assert layout_ns.__file__ is None, 'namespace package became a regular package'\n"
            "assert layout_flit.__file__ is None, 'namespace package became a regular package'\n"
            "assert not Path('stubs/src/vendor_sdk-stubs/__init__.py').exists()\n"
            "assert not Path('pdm-stubs/src/pdm_sdk-stubs/__init__.py').exists()\n"
            "dists = ('namespaced-dist', 'vendor-sdk-stubs', 'flit-namespaced-dist', 'pdm-sdk-stubs')\n"
            "files = {str(f) for d in dists for f in distribution(d).files}\n"
            "stubs = {'layout_ns/__init__.py', 'layout_flit/__init__.py', 'vendor_sdk-stubs/__init__.py'}\n"
            "assert not {*stubs, 'pdm_sdk-stubs/__init__.py'} & files, files\n"
            "assert not Path('flat/tests/unrelated.py').exists()\n"
            "installed = {d.metadata['Name'] for d in distributions()}\n"
            "assert not {'layout-workspace', 'virtual-app'} & installed\n"
        )
        for no_editable in (False, True):
            for package in (None, ["virtual-app"]):
                await (
                    uv(source.as_workspace())
                    .workspace(".")
                    .builder(no_editable=no_editable)
                    .with_packages(package or [])
                    .with_remote_dependencies(prune_cache=False)
                    .with_workspace_files()
                    .with_local_dependencies()
                    .container()
                    .with_exec(["uv", "run", "--no-sync", "python", "-c", script])
                    .sync()
                )
        # Some tools install the entire workspace even when building one member.
        await (
            uv(source.as_workspace())
            .workspace(".")
            .builder()
            .with_packages(["flat-dist"])
            .with_all_workspace_members()
            .container()
            .with_exec(["uv", "sync", "--frozen", "--all-packages"])
            .sync()
        )

    @function
    async def uv_build_backend_handler(self) -> None:
        """Another module's handler (`setuptools-package-dir`) builds a package the built-in backends can't."""
        source = _src("uv/tests/_packages/custom-handler")
        script = (
            "from importlib.metadata import version\n"
            "import plugged\n"
            "assert plugged.VALUE == 'real source'\n"
            "assert version('plugged-app') == '0.1.0'\n"
        )
        ws = source.as_workspace()
        for no_editable in (False, True):
            ctr = (
                uv(ws)
                .with_build_backend_handlers([setuptools_package_dir()])
                .workspace(".")
                .builder(no_editable=no_editable)
                .with_dependencies()
                .container()
                .with_exec(["uv", "run", "--no-sync", "python", "-c", script])
            )
            # The handler's `setup` ran before the build and its `teardown` after it.
            await ctr.file("/tmp/setuptools-package-dir-setup").sync()
            if await ctr.env_variable("SETUPTOOLS_PACKAGE_DIR"):
                raise AssertionError("expected the handler's teardown to remove SETUPTOOLS_PACKAGE_DIR")

    @function
    async def uv_maturin_outside_workspace(self) -> None:
        """A maturin crate builds against a crate outside its uv workspace, staged from the workspace root.

        The Cargo target cache is cleared after every build here, which must leave the
        installed extension working.
        """
        ws = _fixture("uv/tests/_packages/maturin-outside-workspace")
        uv_ = uv(ws)
        maturin = (
            uv_.build_backend_handlers()
            .maturin()
            .with_package_config(
                name="outside-app", extra_build_inputs=["/crates/core/Cargo.toml", "/crates/core/src/**"]
            )
            .with_max_target_size(0)
        )
        uv_ = uv_.with_build_backend_handlers([maturin])
        base = core().container().from_("rust:1.90.0-slim-bookworm").with_workdir("/workspace")
        for no_editable in (False, True):
            await (
                uv_.workspace("py")
                .builder(base_container=base, no_editable=no_editable)
                .with_dependencies()
                .container()
                # Isolated (`-I`), so the installed package is imported rather than the source.
                .with_exec(
                    ["uv", "run", "--no-sync", "python", "-I", "-c", "import outside; assert outside.add(2, 3) == 5"]
                )
                .sync()
            )

    @function
    async def uv_build_input_above_package(self) -> None:
        """A package builds when its backend reads a file above it, staged from the workspace root.

        `my-lib` takes its version from hatch's `version.path = "../VERSION"`.
        """
        src = _src("uv/tests/_packages/workspace")
        pyproject = await src.file("my-lib/pyproject.toml").contents()
        for old, new in (
            ('version = "0.1.0"', 'dynamic = ["version"]'),
            ('requires = ["uv_build>=0.10.4,<0.11.0"]', 'requires = ["hatchling==1.27.0"]'),
            ('build-backend = "uv_build"', 'build-backend = "hatchling.build"'),
        ):
            if old not in pyproject:
                raise AssertionError(f"expected {old!r} in the fixture's my-lib/pyproject.toml")
            pyproject = pyproject.replace(old, new)
        pyproject += '\n[tool.hatch.version]\npath = "../VERSION"\npattern = "^(?P<version>.+?)$"\n'
        src = src.with_new_file("my-lib/pyproject.toml", pyproject).with_new_file("VERSION", "1.2.3\n")
        script = "from importlib.metadata import version; assert version('my-lib') == '1.2.3', version('my-lib')"
        for no_editable in (False, True):
            await (
                uv(src.as_workspace())
                .workspace(".")
                .builder(no_editable=no_editable)
                .with_packages(["my-app"])
                .with_dependencies()
                .container()
                .with_exec(["python", "-c", script])
                .sync()
            )

    @function
    async def uv_maturin_target_cache_tracks_source(self) -> None:
        """A maturin build over a kept Cargo target cache compiles the current source, not a cached build.

        Builds two versions of a path crate in turn over one target volume, then the first
        again: its files are older than the second version's build, so Cargo would reuse it.
        """
        source = _src("uv/tests/_packages/maturin-outside-workspace")
        versions = {
            5: source,
            6: source.with_new_file(
                "crates/core/src/lib.rs", "pub fn add(a: i64, b: i64) -> i64 {\n    a + b + 1\n}\n"
            ),
        }
        target = core().cache_volume("uv-check-maturin-target-cache-tracks-source")
        base = core().container().from_("rust:1.90.0-slim-bookworm").with_workdir("/workspace")
        for expected in (5, 6, 5):
            uv_ = uv(versions[expected].as_workspace())
            maturin = (
                uv_.build_backend_handlers()
                .maturin()
                .with_package_config(
                    name="outside-app", extra_build_inputs=["/crates/core/Cargo.toml", "/crates/core/src/**"]
                )
                .with_cache_volumes(target=target)
            )
            await (
                uv_.with_build_backend_handlers([maturin])
                .workspace("py")
                .builder(base_container=base, no_editable=True)
                .with_dependencies()
                .container()
                .with_exec(
                    [
                        "uv",
                        "run",
                        "--no-sync",
                        "python",
                        "-I",
                        "-c",
                        f"import outside; assert outside.add(2, 3) == {expected}, outside.add(2, 3)",
                    ]
                )
                .sync()
            )

    @function
    async def uv_workspace_inputs(self) -> None:
        """A workspace is loaded from just what its builds read, the handlers' build inputs included."""
        source = _src("uv/tests/_packages/maturin-outside-workspace").with_new_file("docs/unrelated.md", "unread")
        uv_ = uv(source.as_workspace())
        maturin = (
            uv_.build_backend_handlers()
            .maturin()
            .with_package_config(
                name="outside-app", extra_build_inputs=["/crates/core/Cargo.toml", "/crates/core/src/**"]
            )
        )
        workspace = uv_.with_build_backend_handlers([maturin]).workspace("py")
        found = sorted(await workspace.inputs().glob("**/*.*"))
        expected = sorted(path for path in await source.glob("**/*.*") if not path.startswith("docs/"))
        if found != expected:
            raise AssertionError(f"inputs: expected {expected}, got {found}")

    @function
    async def uv_maturin_dist(self) -> None:
        """`dist` builds a maturin package's sdist and wheel against a crate outside its uv workspace."""
        uv_ = uv(_fixture("uv/tests/_packages/maturin-outside-workspace"))
        maturin = (
            uv_.build_backend_handlers()
            .maturin()
            .with_package_config(
                name="outside-app", extra_build_inputs=["/crates/core/Cargo.toml", "/crates/core/src/**"]
            )
        )
        base = core().container().from_("rust:1.90.0-slim-bookworm").with_workdir("/workspace")
        dist = (
            uv_.with_build_backend_handlers([maturin])
            .workspace("py")
            .builder(base_container=base)
            .with_packages(["outside-app"])
            .build(python_version=["3.13"])
        )
        entries = await dist.entries()
        if not any(e.endswith(".whl") for e in entries) or not any(e.endswith(".tar.gz") for e in entries):
            raise AssertionError(f"expected an sdist and a wheel, got {entries}")
        await (
            core()
            .container()
            .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
            .with_directory("/dist", dist)
            .with_exec(["sh", "-c", "uv venv /venv && uv pip install --python /venv /dist/*.whl"])
            .with_exec(["/venv/bin/python", "-I", "-c", "import outside; assert outside.add(2, 3) == 5"])
            .sync()
        )

    @function
    async def uv_maturin_editable_cached_build(self) -> None:
        """An editable maturin install has its extension even when uv reuses its cached build.

        maturin writes an editable package's extension into its source tree, not
        into the wheel uv caches. The local install runs twice on the same
        scaffold, the second time as its own step, so uv's cache holds the first
        one's build.
        """
        ws = _fixture("uv/tests/_packages/maturin-outside-workspace")
        uv_ = uv(ws)
        maturin = (
            uv_.build_backend_handlers()
            .maturin()
            .with_package_config(
                name="outside-app", extra_build_inputs=["/crates/core/Cargo.toml", "/crates/core/src/**"]
            )
        )
        base = core().container().from_("rust:1.90.0-slim-bookworm").with_workdir("/workspace")
        scaffold = (
            uv_.with_build_backend_handlers([maturin])
            .workspace("py")
            .builder(base_container=base)
            .with_remote_dependencies()
            .with_workspace_files()
        )
        await scaffold.with_local_dependencies().container().sync()
        rerun = scaffold.with_container(scaffold.container().with_env_variable("UV_MATURIN_CACHED_BUILD", "rerun"))
        await (
            rerun.with_local_dependencies()
            .container()
            .with_exec(
                ["uv", "run", "--no-sync", "python", "-I", "-c", "import outside; assert outside.add(2, 3) == 5"]
            )
            .sync()
        )

    @function
    async def uv_license_files(self) -> None:
        """Declared license globs are available for editable and non-editable builds."""
        src = _src("uv/tests/_packages/workspace-flat")
        files = {
            "LICENSE.txt": "Main license\n",
            "NOTICE": "Notice\n",
            "README.md": "License in the readme\n",
            "licenses/COPYING.md": "Third-party license\n",
            "licenses/vendor/COPYING.txt": "Nested third-party license\n",
        }
        for package in ("my-app", "my-lib"):
            pyproject = await src.file(f"{package}/pyproject.toml").contents()
            pyproject = pyproject.replace(
                "[project]\n",
                '[project]\nlicense-files = ["LICEN[CS]E*", "NOTIC?", "licenses/**/COPYING.*", "README.md"]\n',
            )
            src = src.with_new_file(f"{package}/pyproject.toml", pyproject)
            for path, contents in files.items():
                src = src.with_new_file(f"{package}/{path}", contents)
            src = src.with_new_file(f"{package}/licenses/unrelated.txt", "Must not be copied\n")
        src = core().directory().with_directory("nested", src)
        script = (
            "from pathlib import Path\n"
            f"expected = {files!r}\n"
            "for name in ('my-app', 'my-lib'):\n"
            "    for path, contents in expected.items():\n"
            "        assert Path(name, path).read_text() == contents\n"
            "    assert not Path(name, 'licenses/unrelated.txt').exists()\n"
        )
        for no_editable in (False, True):
            await (
                uv(src.as_workspace())
                .workspace(path="nested")
                .builder(no_editable=no_editable)
                .with_packages(["my-app"])
                .with_remote_dependencies(prune_cache=False)
                .with_workspace_files()
                .with_local_dependencies()
                .container()
                .with_exec(["uv", "run", "--no-sync", "python", "-c", script])
                .sync()
            )

    @function
    async def uv_license_file_patterns(self) -> None:
        """License arrays filter package-relative files without replacing the scaffold."""
        source = _src("uv/tests/_packages/standalone-app")
        pyproject = await source.file("pyproject.toml").contents()
        files = ["LICENSE.txt", "NOTICE", "licenses/COPYING.md", "licenses/vendor/COPYING.txt"]
        for path in [*files, "nested/LICENSE.txt", "licenses/unrelated.txt"]:
            source = source.with_new_file(path, path)
        cases: list[tuple[list[str] | None, list[str]]] = [
            (None, []),
            ([], []),
            (["LICENSE.txt", "NOTICE"], ["LICENSE.txt", "NOTICE"]),
            (["LICEN[CS]E*", "NOTIC?", "licenses/**/COPYING.*"], files),
        ]
        for patterns, expected in cases:
            metadata = pyproject
            if patterns is not None:
                metadata = metadata.replace("[project]\n", f"[project]\nlicense-files = {patterns!r}\n")
            ctr = (
                uv(source.with_new_file("pyproject.toml", metadata).as_workspace())
                .workspace(".")
                .builder()
                .with_workspace_files()
                .container()
            )
            scaffold = ctr.directory(await ctr.workdir())
            actual = {path for path in await scaffold.glob("**/*") if not path.endswith("/")}
            assert actual == {"pyproject.toml", "uv.lock", "README.md", "src/standalone_app/__init__.py", *expected}, (
                patterns,
                actual,
            )
            assert await scaffold.file("pyproject.toml").contents() == metadata
            assert await scaffold.file("src/standalone_app/__init__.py").contents() == ""
            for path in expected:
                assert await scaffold.file(path).contents() == path

    @check
    @function
    async def uv_real_world(
        self,
        repo: Annotated[str | None, Doc("Only build this `owner/name` entry")] = None,
    ) -> None:
        """Build the pinned open-source projects in `uv/tests/real-world.yaml`.

        Each project is cloned at its pinned commit and installed both editable
        and non-editable (plus any extra `install` options), then its declared
        modules must import.
        """
        projects = yaml.safe_load(await _src("uv/tests").file(_REAL_WORLD_PROJECTS).contents())
        if repo is not None:
            projects = [p for p in projects if p["repo"] == repo]
            if not projects:
                raise ValueError(f"no entry for {repo!r} in {_REAL_WORLD_PROJECTS}")
        tracer = get_tracer()

        async def _build(project: dict[str, Any], no_editable: bool) -> None:
            with tracer.start_as_current_span(f"{project['repo']} (no_editable={no_editable})"):
                source = core().git(f"https://github.com/{project['repo']}").commit(project["rev"]).tree()
                names = [
                    tomllib.loads(await source.file(f"{path}/pyproject.toml").contents())["project"]["name"]
                    for path in project["packages"]
                ]
                base = core().container().from_(project["base"]) if "base" in project else None
                project_uv = uv(source.as_workspace())
                builtin = project_uv.build_backend_handlers()
                handlers = []
                for backend, packages in project.get("handlers", {}).items():
                    handler = getattr(builtin, backend)()
                    for name, extra_build_inputs in packages.items():
                        handler = handler.with_package_config(name=name, extra_build_inputs=extra_build_inputs)
                    handlers.append(handler)
                builder = (
                    project_uv.with_build_backend_handlers(handlers)
                    .workspace(".")
                    .builder(no_editable=no_editable, base_container=base)
                    .with_packages(names)
                )
                for method in project.get("builder", []):
                    builder = getattr(builder, method)()
                ctr = await builder.with_dependencies().container()
                script = "".join(f"import {module}\n" for module in project["imports"])
                # Isolated (`-I`), so the installed package is imported rather than the source
                # in the working directory, which lacks a compiled extension.
                await ctr.with_exec(["uv", "run", "--no-sync", "python", "-I", "-c", script]).sync()

        async with anyio.create_task_group() as tg:
            for project in projects:
                for no_editable in (False, True):
                    tg.start_soon(_build, project, no_editable)

    @function
    async def uv_workspace_auto_install_uv(self) -> None:
        """Build with a base container that has no uv and verify auto_install_uv works."""
        bare = core().container().from_("debian:bookworm-slim").with_workdir("/workspace")
        ctr = (
            await uv(_fixture("uv/tests/_packages/workspace"))
            .workspace(".")
            .builder(base_container=bare)
            .with_packages(["my-app"])
            .with_dependencies()
            .container()
        )
        uv_path = await ctr.with_exec(["which", "uv"]).stdout()
        assert uv_path.strip() == "/uv/uv"

    @function
    async def uv_workspace_layer_cache(self) -> None:
        """Keep workspace metadata/dependencies stable when only package source changes."""
        source = _src("uv/tests/_packages/workspace")
        changed = source.with_new_file("my-app/src/my_app/cache_marker.py", "CACHE_MARKER = 'changed'\n")
        base = _base()
        first = (
            await uv(source.as_workspace())
            .workspace(".")
            .builder(base_container=base)
            .with_all_packages()
            .with_dependencies()
            .container()
        )
        second = (
            await uv(changed.as_workspace())
            .workspace(".")
            .builder(base_container=base)
            .with_all_packages()
            .with_dependencies()
            .container()
        )
        marker = await second.with_exec(
            ["python", "-c", "from my_app.cache_marker import CACHE_MARKER; print(CACHE_MARKER)"]
        ).stdout()
        if marker.strip() != "changed":
            raise AssertionError(f"expected source overlay to be live, got: {marker!r}")

        checker = core().container().from_("python:3.14-slim").with_workdir("/check")
        for name, image in (("base", base), ("first", first), ("second", second)):
            checker = checker.with_file(f"/check/{name}.tar", image.as_tarball())
        script = r"""
import json, tarfile

def layers(path):
    with tarfile.open(path) as archive:
        index = json.load(archive.extractfile("index.json"))
        digest = index["manifests"][0]["digest"].split(":", 1)[1]
        manifest = json.load(archive.extractfile(f"blobs/sha256/{digest}"))
        return [layer["digest"] for layer in manifest["layers"]]

base, first, second = (layers(f"/check/{name}.tar") for name in ("base", "first", "second"))
assert len(first) - len(base) <= 8, (len(base), len(first))
assert len(first) == len(second)
assert first[:-1] == second[:-1], (first, second)
assert first[-1] != second[-1]
print("LAYER_CACHE_OK")
"""
        output = await checker.with_exec(["python", "-c", script]).stdout()
        if "LAYER_CACHE_OK" not in output:
            raise AssertionError(f"expected source-only change to preserve build layers, got: {output!r}")

    @function
    async def uv_workspace_build_workspace_app(self) -> None:
        """Build a workspace where the target package is a flat app (no build-system)."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/workspace-app"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["my-app"])
            .with_dependencies()
            .container()
        )
        await ctr.with_exec(["python", "-c", "import my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_workspace_flat(self) -> None:
        """Build a workspace where dependencies use flat layout (no src/ directory)."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/workspace-flat"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["my-app"])
            .with_dependencies()
            .container()
        )
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_standalone_selective_extra(self) -> None:
        """`extra=['viz']` installs only the viz extra's deps, not other extras or groups."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/standalone-app"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["standalone-app"])
            .with_extras(["viz"])
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(
            present=["standalone_app", "tabulate"],
            absent=["idna", "six", "packaging"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_selective_group(self) -> None:
        """`group=['docs']` installs only the docs group's deps, not other groups or extras."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/standalone-app"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["standalone-app"])
            .with_groups(["docs"])
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(
            present=["six"],
            absent=["packaging", "tabulate", "idna"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_no_install_packages(self) -> None:
        """`with_no_install_packages` leaves a locked dependency uninstalled and installs the rest."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/standalone-app"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["standalone-app"])
            .with_extras(["viz"])
            .with_no_install_packages(["tabulate"])
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(present=["standalone_app"], absent=["tabulate"])
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_locked_packages(self) -> None:
        """`locked_packages` lists every package in `uv.lock`, the workspace's own included."""
        packages = await uv(_fixture("uv/tests/_packages/standalone-app")).workspace(".").locked_packages()
        expected = ["idna", "packaging", "six", "standalone-app", "tabulate"]
        if packages != expected:
            raise AssertionError(f"expected {expected}, got {packages}")

    @function
    async def uv_workspace_builder_selection(self) -> None:
        """`builder` takes the selection as arguments, the same as the `with_*` calls."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/standalone-app"))
            .workspace(".")
            .builder(
                base_container=_base(),
                extras=["viz"],
                groups=["docs"],
                no_install_packages=["tabulate"],
                packages=["standalone-app"],
            )
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(present=["standalone_app", "six"], absent=["tabulate", "idna", "packaging"])
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_full_workspace_all_extras_all_groups(self) -> None:
        """Full workspace build with all extras (from my-app) and all groups (from root) installed."""
        ctr = (
            await uv(_fixture("uv/tests/_packages/workspace"))
            .workspace(".")
            .builder(base_container=_base())
            .with_all_packages()
            .with_all_extras()
            .with_all_groups()
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(
            present=["tabulate", "idna", "six", "packaging"],
            absent=[],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def github_pytest(self) -> None:
        """Run the github module's unit-test suite inside a freshly built container."""
        ctr = (
            await uv(_fixture("github"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["github"])
            .with_all_groups()
            .with_dependencies()
            .container()
        )
        workdir = await ctr.workdir()
        tests = _src("github/tests")
        ctr = ctr.with_directory(f"{workdir}/tests", tests)
        await _with_pytest_otel(ctr).with_exec(["pytest", "-q"]).stdout()

    @check
    @function
    async def twingate_build(self) -> None:
        """Build the Twingate client container (no credentials needed)."""
        tg = twingate(service_key=core().set_secret("dummy", "{}"))
        await tg.ctr().with_exec(["twingated", "--version"]).sync()

    @function
    async def uv_workspace_pytest_self(self) -> None:
        """Run the uv module's own pytest suite inside a freshly built container.

        `install()` only copies `src/` for each package (to keep layer
        granularity tight). `tests/` has to be mounted explicitly so
        pytest can find them. Without it, `pytest -q` collects nothing
        and exits with code 5 (NO_TESTS_COLLECTED).
        """
        ctr = (
            await uv(_fixture("uv"))
            .workspace(".")
            .builder(base_container=_base())
            .with_packages(["uv"])
            .with_all_groups()
            .with_dependencies()
            .container()
        )
        workdir = await ctr.workdir()
        tests = _src("uv/tests")
        ctr = ctr.with_directory(f"{workdir}/tests", tests)
        await _with_pytest_otel(ctr).with_exec(["pytest", "-q"]).stdout()

    def _ruff_dirty_source(self) -> Directory:
        return (
            core()
            .directory()
            .with_new_file(
                "dirty.py",
                contents=("import os, sys, json\nimport os\nx=  1\ny = [1,2,3,]\nif x == True:\n    pass\n"),
            )
        )

    def _ruff_clean_source(self) -> Directory:
        return core().directory().with_new_file("clean.py", contents="x = 1\ny = [1, 2, 3]\n")

    async def _assert_changeset_only_modifies(self, changeset: Changeset, expected: set[str]) -> str:
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
            tg.start_soon(self.ruff_check_clean)
            tg.start_soon(self.ruff_format_clean)
            tg.start_soon(self.ruff_check_unfixable)

    @function
    async def ruff_check_fix(self) -> str:
        """The `check` generator fixes a dirty file and changes nothing else."""
        changeset = ruff(self._ruff_dirty_source().as_workspace()).check()
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @function
    async def ruff_format_fix(self) -> str:
        """The `format` generator formats a dirty file and changes nothing else."""
        changeset = ruff(self._ruff_dirty_source().as_workspace()).format()
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @function
    async def ruff_check_clean(self) -> None:
        """The `check` generator changes nothing on a clean file."""
        if not await ruff(self._ruff_clean_source().as_workspace()).check().is_empty():
            raise AssertionError("expected ruff check to change nothing on a clean file")

    @function
    async def ruff_format_clean(self) -> None:
        """The `format` generator changes nothing on a clean file."""
        if not await ruff(self._ruff_clean_source().as_workspace()).format().is_empty():
            raise AssertionError("expected ruff format to change nothing on a clean file")

    @function
    async def ruff_check_unfixable(self) -> None:
        """The `check` generator fails on a violation ruff can't fix."""
        source = core().directory().with_new_file("unfixable.py", contents="print(undefined)\n")
        try:
            await ruff(source.as_workspace()).check().sync()
        except QueryError:
            return
        raise AssertionError("expected ruff check to fail on an undefined name")

    @function
    async def uv_workspace_build_partial_workspace(self) -> None:
        """Build a project whose uv.lock references local deps that don't exist in the source tree.

        The missing dep (ext-pkg at ../../gone/ext-pkg) should be silently
        skipped. The present dep (my-dep at ../my-dep) should be installed.
        """
        ctr = (
            await uv(_fixture("uv/tests/_packages/partial-workspace"))
            .workspace("sub-project")
            .builder(base_container=_base())
            .with_packages(["sub-project"])
            .with_groups(["dev"])
            .with_dependencies()
            .container()
        )
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
        ctr = (
            await uv(_fixture("uv/tests/_packages/nested-standalone"))
            .workspace("app")
            .builder(base_container=_base())
            .with_groups(["dev"])
            .with_dependencies()
            .container()
        )
        script = _assert_modules_script(
            present=["six", "lib_pkg"],
            absent=[],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    # ---- pyproject module checks ----

    @staticmethod
    def _pyproject_tree() -> Directory:
        """A uv workspace with a workspace source, an inherited path source and unrelated nested projects."""

        def manifest(name: str, extra: str = "") -> str:
            return f'[project]\nname = "{name}"\nversion = "0"\n{extra}'

        return (
            core()
            .directory()
            .with_new_file(
                "pyproject.toml",
                manifest(
                    "root",
                    '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
                    '[tool.uv.sources]\nshared = { path = "vendor/shared" }\n',
                ),
            )
            .with_new_file("uv.lock", "")
            .with_new_file(
                "packages/a/pyproject.toml",
                manifest("a", 'dependencies = ["b", "shared"]\n[tool.uv.sources]\nb = { workspace = true }\n'),
            )
            .with_new_file("packages/a/src/a/__init__.py", "")
            .with_new_file("packages/b/pyproject.toml", manifest("b"))
            .with_new_file("packages/b/src/b/__init__.py", "")
            .with_new_file("packages/b/tests/fixture/pyproject.toml", manifest("fixture"))
            .with_new_file("vendor/shared/pyproject.toml", manifest("shared"))
            .with_new_file("vendor/shared/shared.py", "")
            .with_new_file("tools/x/pyproject.toml", manifest("x"))
            .with_new_file("tools/x/pylock.prod.toml", "")
            .with_new_file("tests/_packages/f/pyproject.toml", manifest("f"))
            .with_new_file("tests/_packages/f/uv.lock", "")
        )

    @staticmethod
    def _assert_equal(actual: object, expected: object) -> None:
        if actual != expected:
            raise AssertionError(f"expected {expected!r}, got {actual!r}")

    @check
    @function
    async def all_pyproject(self) -> None:
        """Run all checks for the `pyproject` module in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.pyproject_go_test)
            tg.start_soon(self.pyproject_discovers_projects_and_workspaces)
            tg.start_soon(self.pyproject_workspace_markers)
            tg.start_soon(self.pyproject_respects_gitignore)
            tg.start_soon(self.pyproject_project_source)
            tg.start_soon(self.pyproject_project_files)
            tg.start_soon(self.pyproject_workspace_source_and_manifests)

    @function
    async def pyproject_go_test(self) -> None:
        """Run the pyproject module's Go unit tests (the discovery logic, engine-free)."""
        await _go_test("pyproject").sync()

    @function
    async def pyproject_discovers_projects_and_workspaces(self) -> None:
        """Every pyproject.toml is a project and every uv.lock a workspace, less `exclude`."""
        found = pyproject(self._pyproject_tree().as_workspace(), exclude=["**/tests/_packages/**"])
        self._assert_equal(
            await found.projects().keys(),
            [".", "packages/a", "packages/b", "packages/b/tests/fixture", "tools/x", "vendor/shared"],
        )
        self._assert_equal(await found.workspaces().keys(), ["."])
        root = found.workspaces().get(".")
        self._assert_equal(await root.members(), [".", "packages/a", "packages/b"])
        self._assert_equal(await root.local_dependencies(), ["vendor/shared"])
        self._assert_equal(await root.projects().keys(), [".", "packages/a", "packages/b"])
        a = found.projects().get("packages/a")
        self._assert_equal(await a.workspace(), ".")
        self._assert_equal(await a.local_dependencies(), ["packages/b", "vendor/shared"])
        self._assert_equal(await found.projects().get("tools/x").workspace(), None)
        # The batch functions hold the same items, in the collections' order.
        self._assert_equal(
            [await project.path() for project in await found.project_list()], await found.projects().keys()
        )
        self._assert_equal(
            [await workspace.path() for workspace in await found.workspace_list()], await found.workspaces().keys()
        )

    @function
    async def pyproject_workspace_markers(self) -> None:
        """`workspace_markers` decides which files make a directory a workspace."""
        found = pyproject(
            self._pyproject_tree().as_workspace(),
            exclude=["**/tests/_packages/**"],
            workspace_markers=["uv.lock", "pylock.prod.toml"],
        )
        self._assert_equal(await found.workspaces().keys(), [".", "tools/x"])
        x = found.workspaces().get("tools/x")
        self._assert_equal(await x.lockfiles(), ["pylock.prod.toml"])
        self._assert_equal(await x.members(), ["tools/x"])

    @function
    async def pyproject_respects_gitignore(self) -> None:
        """A gitignored copy of the repository (e.g. an agent worktree) adds no projects or workspaces.

        `Workspace.find_roots` walks gitignored directories, so discovery has to
        filter what it finds through the `.gitignore` files itself.
        """
        tree = (
            self._pyproject_tree()
            .with_new_file(".gitignore", ".claude/worktrees/\n")
            .with_new_file(".claude/worktrees/wt1/pyproject.toml", '[project]\nname = "root"\nversion = "0"\n')
            .with_new_file(".claude/worktrees/wt1/uv.lock", "")
        )
        found = pyproject(tree.as_workspace(), exclude=["**/tests/_packages/**"])
        self._assert_equal(
            await found.projects().keys(),
            [".", "packages/a", "packages/b", "packages/b/tests/fixture", "tools/x", "vendor/shared"],
        )
        self._assert_equal(await found.workspaces().keys(), ["."])

    @function
    async def pyproject_project_source(self) -> None:
        """A project's source holds its own files and its local dependencies', and nothing else."""
        ws = self._pyproject_tree().as_workspace()
        source = pyproject(ws).projects().get("packages/a").source()
        self._assert_equal(
            sorted(await source.glob("**/*.py")),
            ["packages/a/src/a/__init__.py", "packages/b/src/b/__init__.py", "vendor/shared/shared.py"],
        )
        self._assert_equal(await source.glob("packages/b/tests/**"), [])

    @function
    async def pyproject_project_files(self) -> None:
        """A project's files stop at the projects nested in it, needed or not."""
        ws = self._pyproject_tree().as_workspace()
        files = pyproject(ws).projects().get("packages/b").files()
        self._assert_equal(
            sorted(await files.glob("**/*.toml")),
            ["packages/b/pyproject.toml"],
        )
        root = pyproject(ws).projects().get(".").files()
        self._assert_equal(sorted(await root.glob("**/pyproject.toml")), ["pyproject.toml"])

    @function
    async def pyproject_workspace_source_and_manifests(self) -> None:
        """A workspace's source leaves out unrelated nested projects; its manifests hold only resolver inputs."""
        ws = self._pyproject_tree().as_workspace()
        root = pyproject(ws).workspaces().get(".")
        self._assert_equal(
            sorted(await root.source().glob("**/pyproject.toml")),
            [
                "packages/a/pyproject.toml",
                "packages/b/pyproject.toml",
                "pyproject.toml",
                "vendor/shared/pyproject.toml",
            ],
        )
        manifests = await root.manifests().glob("**/*.*")
        self._assert_equal(
            sorted(manifests),
            [
                "packages/a/pyproject.toml",
                "packages/b/pyproject.toml",
                "pyproject.toml",
                "uv.lock",
                "vendor/shared/pyproject.toml",
            ],
        )

    # ---- deptry module checks ----

    def _deptry_project(self, pyproject: str, sources: dict[str, str]) -> Directory:
        """A standalone project from `pyproject` and `sources`, with a uv.lock locking deptry in its `dev` group."""
        name = tomllib.loads(pyproject)["project"]["name"]
        d = (
            core()
            .directory()
            .with_new_file("pyproject.toml", pyproject)
            .with_new_file("uv.lock", _deptry_lock(name, source="editable", groups={"dev": ["0.25.1"]}))
        )
        for path, content in sources.items():
            d = d.with_new_file(path, content)
        return d

    @staticmethod
    async def _deptry_check_all(src: Directory, subset: list[str] | None = None) -> None:
        """Run deptry on every project in `src` (or the `subset` of them), as `dagger check` does."""
        projects = deptry(src.as_workspace()).projects()
        if subset is not None:
            projects = projects.subset(subset)
        for project in await projects.list_():
            await _run_check(project.check())

    # ---- zensical module checks ----

    @check
    @function
    async def all_zensical(self) -> None:
        """Run all checks for the `zensical` module in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.zensical_discovers_projects)
            tg.start_soon(self.zensical_builds_module_docs)
            tg.start_soon(self.zensical_strict_fails_on_broken_link)
            tg.start_soon(self.zensical_site_settings)

    @function
    async def zensical_discovers_projects(self) -> None:
        """`projects` finds one site per zensical.toml, in the innermost uv workspace holding it.

        Docs in a workspace member build that member's package; docs outside
        every uv workspace have none.
        """
        src = (
            core()
            .directory()
            .with_new_file("pyproject.toml", '[project]\nname = "root"\nversion = "0"\n')
            .with_new_file("uv.lock", "version = 1\n")
            .with_new_file("zensical.toml", "[project]\n")
            .with_new_file("member/pyproject.toml", '[project]\nname = "member"\nversion = "0"\n')
            .with_new_file("member/zensical.toml", "[project]\n")
            .with_new_file("nested/uv.lock", "version = 1\n")
            .with_new_file("nested/pyproject.toml", '[project]\nname = "nested"\nversion = "0"\n')
            .with_new_file("nested/zensical.toml", "[project]\n")
        )
        projects = zensical(src.as_workspace()).projects()
        paths = await projects.keys()
        if paths != [".", "member", "nested"]:
            raise AssertionError(f"expected ['.', 'member', 'nested'], got {paths}")
        workspaces = {path: await projects.get(path).workspace() for path in paths}
        if workspaces != {".": ".", "member": ".", "nested": "nested"}:
            raise AssertionError(f"unexpected workspaces: {workspaces}")

    @function
    async def zensical_builds_module_docs(self) -> None:
        """The deptry module's docs build in its uv workspace, with its `docs` group."""
        site = zensical(_src("deptry").as_workspace()).projects().get(".").site()
        entries = await site.entries()
        if "index.html" not in entries:
            raise AssertionError(f"expected index.html in the built site, got {entries}")

    @function
    async def zensical_strict_fails_on_broken_link(self) -> None:
        """With `strict` (the default), a broken link fails the build."""
        src = _src("deptry").with_new_file("docs/index.md", "[broken](missing.md)\n")
        try:
            await _run_check(zensical(src.as_workspace()).projects().get(".").check())
        except CheckFailedError:
            return
        raise AssertionError("expected the strict build to fail on a broken link")

    @check
    @function
    async def all_deptry(self) -> None:
        """Run all checks for the `deptry` module in parallel."""
        tests = [
            self.deptry_discovers_projects,
            self.deptry_clean_flat_layout,
            self.deptry_clean_src_layout,
            self.deptry_detects_issue,
            self.deptry_project_settings,
            self.deptry_subset,
            self.deptry_projects_exclude,
            self.deptry_config_opts_in_nonstandard_layout,
            self.deptry_skips_project_without_layout_or_config,
            self.deptry_version_from_lock,
        ]

        async def run(test: Callable[[], Awaitable[None]]) -> None:
            # Most fixtures are a single project at `.`: the test's name says which one a span belongs to.
            with get_tracer().start_as_current_span(f"Test deptry: {test.__name__.removeprefix('deptry_')}"):
                await test()

        async with anyio.create_task_group() as tg:
            for test in tests:
                tg.start_soon(run, test)

    @function
    async def deptry_version_from_lock(self) -> None:
        """Each project runs the deptry its uv workspace's uv.lock locks in the `group` setting's group, `dev`
        by default, and a project without one fails."""
        pyproject = '[project]\nname = "{name}"\nversion = "0"\ndependencies = []\n'
        src = (
            core()
            .directory()
            .with_new_file("pyproject.toml", '[tool.uv.workspace]\nmembers = ["member"]\n')
            .with_new_file(
                "uv.lock", _deptry_lock("root", source="virtual", groups={"dev": ["0.24.0"], "lint": ["0.25.1"]})
            )
            .with_new_file("member/pyproject.toml", pyproject.format(name="member"))
            .with_new_file("member/member/__init__.py", "")
            .with_new_file("unlocked/pyproject.toml", pyproject.format(name="unlocked"))
            .with_new_file("unlocked/unlocked/__init__.py", "")
        )
        member = deptry(src.as_workspace()).projects().get("member")
        if (version := await member.version()) != "0.24.0":
            raise AssertionError(f"expected the dev group's 0.24.0, got {version}")
        # The locked version installs and runs.
        await _run_check(member.check())
        lint = deptry(src.as_workspace(), group="lint").projects().get("member")
        if (version := await lint.version()) != "0.25.1":
            raise AssertionError(f"expected the lint group's 0.25.1, got {version}")
        try:
            await deptry(src.as_workspace()).projects().get("unlocked").version()
        except QueryError as exc:
            # The module's error is in the stderr of the failed call, not its message.
            if "no unlocked/uv.lock" not in getattr(exc, "stderr", str(exc)):
                raise
        else:
            raise AssertionError("expected a project without a uv.lock to fail")

    @function
    async def deptry_discovers_projects(self) -> None:
        """`projects` finds one entry per pyproject.toml that declares dependencies.

        A tooling-only pyproject (no `[project].dependencies`) and a
        Dagger-generated one (`[tool.dagger] generated`) are left out.
        """
        pyproject = '[project]\nname = "x"\nversion = "0"\ndependencies = []\n'
        src = (
            core()
            .directory()
            .with_new_file("a/pyproject.toml", pyproject)
            .with_new_file("a/x/__init__.py", "")
            .with_new_file("b/c/pyproject.toml", pyproject)
            .with_new_file("b/c/x/__init__.py", "")
            .with_new_file("tool/pyproject.toml", "[tool.ruff]\nline-length = 100\n")
            .with_new_file("a/sdk/pyproject.toml", pyproject + '[tool.dagger]\ngenerated = "runtime"\n')
            .with_new_file("a/sdk/src/x/__init__.py", "")
        )
        projects = await deptry(src.as_workspace()).projects().keys()
        if projects != ["a", "b/c"]:
            raise AssertionError(f"expected ['a', 'b/c'], got {projects}")

    @function
    async def deptry_clean_flat_layout(self) -> None:
        """A flat-layout project whose deps are all declared and used passes."""
        src = self._deptry_project(
            '[project]\nname = "flatpkg"\nversion = "0"\ndependencies = ["requests"]\n',
            {
                "flatpkg/__init__.py": "import requests\nfrom flatpkg import foo\n",
                "flatpkg/foo.py": "",
            },
        )
        await self._deptry_check_all(src)

    @function
    async def deptry_clean_src_layout(self) -> None:
        """A `src`-layout project passes: its own package isn't misreported as third-party.

        Regression test for `--known-first-party`: a bare `deptry .` on a src
        layout flags the project's own imports as DEP001/DEP003.
        """
        src = self._deptry_project(
            '[project]\nname = "src-pkg"\nversion = "0"\ndependencies = ["requests"]\n',
            {
                "src/src_pkg/__init__.py": "import requests\nimport src_pkg.foo\n",
                "src/src_pkg/foo.py": "",
            },
        )
        await self._deptry_check_all(src)

    @function
    async def deptry_project_settings(self) -> None:
        """A project's `[tool.typesafe_daggerverse.deptry]` table overrides the module's `args`."""
        pyproject = '[project]\nname = "badpkg"\nversion = "0"\ndependencies = ["requests"]\n'
        sources = {"src/badpkg/__init__.py": "x = 1\n"}
        # The unused dependency fails the check (see `deptry_detects_issue`) unless the project ignores the rule.
        table = '\n[tool.typesafe_daggerverse.deptry]\nargs = ["--ignore", "DEP002"]\n'
        await self._deptry_check_all(self._deptry_project(pyproject + table, sources))
        try:
            await (
                deptry(
                    self._deptry_project(
                        pyproject + "\n[tool.typesafe_daggerverse.deptry]\nversion = 1\n", sources
                    ).as_workspace()
                )
                .projects()
                .keys()
            )
        except QueryError:
            return
        raise AssertionError("expected a key that is no project setting to fail")

    @function
    async def deptry_detects_issue(self) -> None:
        """A project with an unused dependency fails the check."""
        src = self._deptry_project(
            '[project]\nname = "badpkg"\nversion = "0"\ndependencies = ["requests"]\n',
            {"src/badpkg/__init__.py": "x = 1\n"},
        )
        try:
            await self._deptry_check_all(src)
        except CheckFailedError:
            return
        raise AssertionError("expected deptry to fail for an unused dependency")

    @function
    async def deptry_projects_exclude(self) -> None:
        """`exclude` leaves matching projects out of `projects`."""
        pyproject = '[project]\nname = "x"\nversion = "0"\ndependencies = []\n'
        src = (
            core()
            .directory()
            .with_new_file("a/pyproject.toml", pyproject)
            .with_new_file("a/x/__init__.py", "")
            .with_new_file("tests/_packages/b/pyproject.toml", pyproject)
            .with_new_file("tests/_packages/b/x/__init__.py", "")
        )
        projects = await deptry(src.as_workspace(), exclude=["**/tests/_packages/**"]).projects().keys()
        if projects != ["a"]:
            raise AssertionError(f"expected ['a'], got {projects}")

    @function
    async def deptry_subset(self) -> None:
        """A `subset` of the projects checks only those, so a broken-but-unselected tree passes."""
        good = self._deptry_project(
            '[project]\nname = "goodpkg"\nversion = "0"\ndependencies = ["requests"]\n',
            {"goodpkg/__init__.py": "import requests\n"},
        )
        bad = self._deptry_project(
            '[project]\nname = "badpkg"\nversion = "0"\ndependencies = ["requests"]\n',
            {"badpkg/__init__.py": "x = 1\n"},
        )
        src = core().directory().with_directory("good", good).with_directory("bad", bad)
        await self._deptry_check_all(src, subset=["good"])

    @function
    async def deptry_config_opts_in_nonstandard_layout(self) -> None:
        """A `[tool.deptry]` section opts a non-standard-layout project in.

        The project has neither a `src` nor a flat package directory, but
        declares `[tool.deptry]`, so it is checked rather than skipped. Here it
        has an unused dependency and must fail.
        """
        src = self._deptry_project(
            '[project]\nname = "flatmod"\nversion = "0"\ndependencies = ["requests"]\n\n[tool.deptry]\n',
            {"run.py": "x = 1\n"},
        )
        try:
            await self._deptry_check_all(src)
        except CheckFailedError:
            return
        raise AssertionError("expected deptry to check a [tool.deptry]-configured project and fail")

    @function
    async def deptry_skips_project_without_layout_or_config(self) -> None:
        """A project with no `src`/flat layout and no `[tool.deptry]` is left out of `projects`."""
        src = self._deptry_project(
            '[project]\nname = "flatmod"\nversion = "0"\ndependencies = ["requests"]\n',
            {"run.py": "x = 1\n"},
        )
        projects = await deptry(src.as_workspace()).projects().keys()
        if projects:
            raise AssertionError(f"expected no projects, got {projects}")

    # ---- pyrefly module checks ----

    _PYREFLY_FIXTURE = "pyrefly/tests/_packages/typed"

    @check
    @function
    async def all_pyrefly(self) -> None:
        """Run all checks for the `pyrefly` module in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.pyrefly_discovers_workspaces)
            tg.start_soon(self.pyrefly_clean)
            tg.start_soon(self.pyrefly_base_container_at_root)
            tg.start_soon(self.pyrefly_fails_on_type_error)
            tg.start_soon(self.pyrefly_baseline)
            tg.start_soon(self.pyrefly_workspace_settings)
            tg.start_soon(self.pyrefly_go_test)

    def _pyrefly_broken_source(self) -> Directory:
        return _src(self._PYREFLY_FIXTURE).with_new_file("src/typed/broken.py", 'count: int = "one"\n')

    @function
    async def pyrefly_discovers_workspaces(self) -> None:
        """`workspaces` holds the uv workspaces that install `pyrefly`, and no other."""
        pyproject = '[project]\nname = "x"\nversion = "0"\n'
        src = (
            core()
            .directory()
            .with_new_file("checked/pyproject.toml", pyproject)
            .with_new_file("checked/uv.lock", 'version = 1\n\n[[package]]\nname = "pyrefly"\nversion = "1.4.1"\n')
            .with_new_file("unchecked/pyproject.toml", pyproject)
            .with_new_file("unchecked/uv.lock", "version = 1\n")
        )
        paths = await pyrefly(src.as_workspace()).workspaces().keys()
        if paths != ["checked"]:
            raise AssertionError(f"expected ['checked'], got {paths}")

    @function
    async def pyrefly_clean(self) -> None:
        """`check` passes in a workspace with no type error."""
        await _run_check(pyrefly(_src(self._PYREFLY_FIXTURE).as_workspace()).workspaces().get(".").check())

    @function
    async def pyrefly_base_container_at_root(self) -> None:
        """A base container whose working directory is `/` still checks only the workspace."""
        base = _base().with_workdir("/")
        workspace = pyrefly(_src(self._PYREFLY_FIXTURE).as_workspace(), base_container=base).workspaces().get(".")
        await _run_check(workspace.check())

    @function
    async def pyrefly_fails_on_type_error(self) -> None:
        """`check` fails on a type error."""
        try:
            await _run_check(pyrefly(self._pyrefly_broken_source().as_workspace()).workspaces().get(".").check())
        except CheckFailedError:
            return
        raise AssertionError("expected pyrefly check to fail on a type error")

    @function
    async def pyrefly_baseline(self) -> None:
        """`update_baseline` records the type errors, and `check` then passes."""
        fixture = _src(self._PYREFLY_FIXTURE)
        # `[tool.pyrefly]` is the fixture's last table.
        config = await fixture.file("pyproject.toml").contents() + 'baseline = "pyrefly-baseline.json"\n'
        broken = self._pyrefly_broken_source().with_new_file("pyproject.toml", config)

        def workspace(source: Directory) -> PyreflyWorkspace:
            return pyrefly(source.as_workspace()).workspaces().get(".")

        recorded = workspace(broken).update_baseline()
        if await recorded.added_paths() != ["pyrefly-baseline.json"]:
            raise AssertionError(f"expected only the baseline to be added, got {await recorded.added_paths()}")
        await _run_check(workspace(broken.with_changes(recorded)).check())

    @function
    async def pyrefly_workspace_settings(self) -> None:
        """A workspace's `[tool.typesafe_daggerverse.pyrefly]` table overrides the module's settings."""
        pyproject = await _src(self._PYREFLY_FIXTURE).file("pyproject.toml").contents()

        def workspace(table: str) -> PyreflyWorkspace:
            source = self._pyrefly_broken_source().with_new_file(
                "pyproject.toml", f"{pyproject}\n[tool.typesafe_daggerverse.pyrefly]\n{table}"
            )
            return pyrefly(source.as_workspace()).workspaces().get(".")

        # The type error fails the check (see `pyrefly_fails_on_type_error`) unless the workspace ignores its kind.
        await _run_check(workspace('args = ["--ignore", "bad-assignment"]\n').check())
        try:
            await workspace("allExtras = false\n").check().pass_()
        except QueryError:
            return
        raise AssertionError("expected a key that is no setting to fail")

    @function
    async def pyrefly_go_test(self) -> None:
        """Run the pyrefly module's Go unit tests (the settings overrides, engine-free)."""
        await _go_test("pyrefly").sync()

    @function
    async def zensical_site_settings(self) -> None:
        """A site's `[tool.typesafe_daggerverse.zensical]` table overrides the module's settings."""
        src = _src("deptry")
        pyproject = await src.file("pyproject.toml").contents()
        # The broken link fails the strict build (see `zensical_strict_fails_on_broken_link`).
        src = src.with_new_file("docs/index.md", "[broken](missing.md)\n").with_new_file(
            "pyproject.toml", f"{pyproject}\n[tool.typesafe_daggerverse.zensical]\nstrict = false\n"
        )
        await _run_check(zensical(src.as_workspace()).projects().get(".").check())
