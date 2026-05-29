"""Checks for typesafe-ai daggerverse modules."""

import anyio
from typing import Annotated, TYPE_CHECKING

import dagger
from dagger import DefaultPath, Doc, check, dag, field, function, object_type


if TYPE_CHECKING:
    import dagger.UvWorkspace


_PYTEST_MODULE_REF = "github.com/dagger/pytest@main"
_PYTEST_MODULE_PIN = "dd183e94449051abdc3c7d745dd148fdc08396d4"


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

    def _standalone(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/standalone-app"),
            base_container=_base(),
        )

    def _workspace(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace"),
            base_container=_base(),
        )

    def _workspace_app(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace-app"),
            base_container=_base(),
        )

    def _workspace_flat(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace-flat"),
            base_container=_base(),
        )

    def _partial_workspace(self) -> "dagger.UvWorkspace":
        """A workspace where one local dep in uv.lock doesn't exist in the source tree."""
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/partial-workspace"),
            base_container=_base(),
            workspace_path="sub-project",
        )

    def _self(self) -> "dagger.UvWorkspace":
        """uv-workspace built against its own source.

        uv-workspace is itself a Dagger module — `UvWorkspace.build()`
        detects the `dagger.json` and runs codegen so `sdk/` is
        materialized before `uv sync`.
        """
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace"),
            base_container=_base(),
        )

    def _status_monitor(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("github-status-monitor"),
            base_container=_base(),
        )

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
        return await dag.github_status_monitor().wait_for_dagger_checks(
            repo=repo, ref=ref, token=token, fail_fast=fail_fast
        )

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
    async def all_uv_audit(self) -> None:
        """Run all checks for the `uv` (audit) module in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.uv_detect_version_pyproject)
            tg.start_soon(self.uv_detect_version_uv_toml_precedence)
            tg.start_soon(self.uv_detect_version_range)
            tg.start_soon(self.uv_detect_version_default)
            tg.start_soon(self.uv_discovers_workspaces)
            tg.start_soon(self.uv_audit_clean)
            tg.start_soon(self.uv_audit_detects_vulnerability)
            tg.start_soon(self.uv_audit_exclude)

    @function
    async def uv_detect_version_pyproject(self) -> None:
        """`uv_version` reads `[tool.uv].required-version` from pyproject.toml."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = "==0.5.0"\n')
        ws = (await dag.uv(source=src).workspaces())[0]
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
        ws = (await dag.uv(source=src).workspaces())[0]
        version = await ws.uv_version()
        if version != "0.4.0":
            raise AssertionError(f"expected 0.4.0 (uv.toml wins), got {version}")

    @function
    async def uv_detect_version_range(self) -> None:
        """A range specifier resolves to its minimal compatible version (no PyPI lookup)."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n[tool.uv]\nrequired-version = ">=0.5.0,<0.5.5"\n')
        ws = (await dag.uv(source=src).workspaces())[0]
        version = await ws.uv_version()
        if version != "0.5.0":
            raise AssertionError(f"expected 0.5.0, got {version}")

    @function
    async def uv_detect_version_default(self) -> None:
        """With no required-version, `uv_version` falls back to the default tag."""
        src = self._uv_src('[project]\nname = "x"\nversion = "0"\n')
        ws = (await dag.uv(source=src).workspaces())[0]
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
        workspaces = await dag.uv(source=src).workspaces()
        if len(workspaces) != 2:
            raise AssertionError(f"expected 2 workspaces, got {len(workspaces)}")

    @function
    async def uv_audit_clean(self) -> None:
        """Auditing a workspace with no vulnerable deps passes."""
        src = self.source.directory("uv/tests/_packages/clean")
        await dag.uv(source=src).audit()

    @function
    async def uv_audit_detects_vulnerability(self) -> None:
        """Auditing a workspace with a known-vulnerable dependency fails."""
        src = self.source.directory("uv/tests/_packages/vulnerable")
        try:
            await dag.uv(source=src).audit()
        except Exception:
            return
        raise AssertionError("expected uv audit to fail for the vulnerable fixture")

    @function
    async def uv_audit_exclude(self) -> None:
        """`exclude` skips matching workspaces, so a vulnerable-but-excluded tree passes."""
        src = self.source.directory("uv/tests/_packages")
        await dag.uv(source=src).audit(exclude=["**/vulnerable"])

    @check
    @function
    async def all_uv(self):
        """Run all uv-workspace checks in parallel."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self.uv_workspace_build_workspace)
            tg.start_soon(self.uv_workspace_build_full_workspace)
            tg.start_soon(self.uv_workspace_build_workspace_app)
            tg.start_soon(self.uv_workspace_build_workspace_flat)
            tg.start_soon(self.uv_workspace_build_standalone)
            tg.start_soon(self.uv_workspace_standalone_selective_extra)
            tg.start_soon(self.uv_workspace_standalone_all_extras)
            tg.start_soon(self.uv_workspace_standalone_selective_group)
            tg.start_soon(self.uv_workspace_standalone_all_groups)
            tg.start_soon(self.uv_workspace_full_workspace_all_extras_all_groups)
            tg.start_soon(self.uv_workspace_build_self)
            tg.start_soon(self.uv_workspace_pytest_self)
            tg.start_soon(self.uv_workspace_build_partial_workspace)

    @function
    async def uv_workspace_build_workspace(self) -> None:
        """Build the uv-workspace multi-package fixture and verify every local package imports."""
        ctr = await self._workspace().build(package="my-app")
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_full_workspace(self) -> None:
        """Build the full uv-workspace (no package filter) and verify every local package imports."""
        ctr = await self._workspace().build(all_packages=True)
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_workspace_app(self) -> None:
        """Build a workspace where the target package is a flat app (no build-system)."""
        ctr = await self._workspace_app().build(package="my-app")
        await ctr.with_exec(["python", "-c", "import my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_workspace_flat(self) -> None:
        """Build a workspace where dependencies use flat layout (no src/ directory)."""
        ctr = await self._workspace_flat().build(package="my-app")
        await ctr.with_exec(["python", "-c", "import my_app, my_lib, my_core"]).stdout()

    @function
    async def uv_workspace_build_standalone(self) -> None:
        """Build the uv-workspace standalone fixture and verify it imports."""
        ctr = await self._standalone().build(package="standalone-app")
        await ctr.with_exec(["python", "-c", "import standalone_app"]).stdout()

    @function
    async def uv_workspace_standalone_selective_extra(self) -> None:
        """`extra=['viz']` installs only the viz extra's deps, not other extras or groups."""
        ctr = await self._standalone().build(package="standalone-app", extra=["viz"])
        script = _assert_modules_script(
            present=["tabulate"],
            absent=["idna", "six", "packaging"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_all_extras(self) -> None:
        """`all_extras=True` installs every extra but no groups."""
        ctr = await self._standalone().build(package="standalone-app", all_extras=True)
        script = _assert_modules_script(
            present=["tabulate", "idna"],
            absent=["six", "packaging"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_selective_group(self) -> None:
        """`group=['docs']` installs only the docs group's deps, not other groups or extras."""
        ctr = await self._standalone().build(package="standalone-app", group=["docs"])
        script = _assert_modules_script(
            present=["six"],
            absent=["packaging", "tabulate", "idna"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_standalone_all_groups(self) -> None:
        """`all_groups=True` installs every group but no extras."""
        ctr = await self._standalone().build(package="standalone-app", all_groups=True)
        script = _assert_modules_script(
            present=["six", "packaging"],
            absent=["tabulate", "idna"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_full_workspace_all_extras_all_groups(self) -> None:
        """Full workspace build with all extras (from my-app) and all groups (from root) installed."""
        ctr = await self._workspace().build(all_packages=True, all_extras=True, all_groups=True)
        script = _assert_modules_script(
            present=["tabulate", "idna", "six", "packaging"],
            absent=[],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()

    @function
    async def uv_workspace_build_self(self) -> None:
        """Build uv-workspace from its own source on a fresh tree."""
        ctr = await self._self().build(package="uv-workspace")
        await ctr.with_exec(["python", "-c", "import uv_workspace"]).stdout()

    @check
    @function
    async def github_status_monitor_pytest(self) -> None:
        """Run the github-status-monitor unit-test suite inside a freshly-
        built container."""
        ctr = await self._status_monitor().build(package="github-status-monitor", all_groups=True)
        workdir = await ctr.workdir()
        tests = self.source.directory("github-status-monitor").directory("tests")
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
        """Run uv-workspace's own pytest suite inside a freshly-built container.

        `UvWorkspace.build()` only copies `src/` for each package (to keep
        layer granularity tight). `tests/` has to be mounted explicitly so
        pytest can find them — without this, `pytest -q` collects nothing
        and exits with code 5 (NO_TESTS_COLLECTED).
        """
        ctr = await self._self().build(package="uv-workspace", all_groups=True)
        workdir = await ctr.workdir()
        tests = self.source.directory("uv-workspace").directory("tests")
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
    async def ruff_check_fix(self) -> str:
        """Run ruff check --fix on a dirty file and verify only dirty.py is changed."""
        ruff = dag.ruff()
        changeset = ruff.check().fix(source=self._ruff_dirty_source())
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @check
    @function
    async def ruff_format_fix(self) -> str:
        """Run ruff format on a dirty file and verify only dirty.py is changed."""
        ruff = dag.ruff()
        changeset = ruff.format().fix(source=self._ruff_dirty_source())
        return await self._assert_changeset_only_modifies(changeset, {"dirty.py"})

    @check
    @function
    async def ruff_check_lint_clean(self) -> str:
        """Run ruff check on a clean file and verify it passes."""
        clean = dag.directory().with_new_file(
            "clean.py",
            contents="x = 1\ny = [1, 2, 3]\n",
        )
        return await dag.ruff().check().lint(source=clean)

    @check
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
        ctr = await self._partial_workspace().build(package="sub-project", group=["dev"])
        script = _assert_modules_script(
            present=["my_dep"],
            absent=["ext_pkg"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()
