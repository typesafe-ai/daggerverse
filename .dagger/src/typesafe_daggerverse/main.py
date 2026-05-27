"""Checks for typesafe-ai daggerverse modules."""

import anyio
from typing import Annotated, TYPE_CHECKING

import dagger
from dagger import DefaultPath, Doc, check, dag, field, function, object_type


if TYPE_CHECKING:
    import dagger.UvWorkspace


def _base() -> dagger.Container:
    return (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.14-bookworm")
        .with_workdir("/workspace")
        # caller picks the project env.
        .with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
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
            source_dir=self.source.directory(
                "uv-workspace/tests/_packages/standalone-app"
            ),
            base_container=_base(),
        )

    def _workspace(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace"),
            base_container=_base(),
        )

    def _workspace_app(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory(
                "uv-workspace/tests/_packages/workspace-app"
            ),
            base_container=_base(),
        )

    def _workspace_flat(self) -> "dagger.UvWorkspace":
        return dag.uv_workspace(
            source_dir=self.source.directory(
                "uv-workspace/tests/_packages/workspace-flat"
            ),
            base_container=_base(),
        )

    def _partial_workspace(self) -> "dagger.UvWorkspace":
        """A workspace where one local dep in uv.lock doesn't exist in the source tree."""
        return dag.uv_workspace(
            source_dir=self.source.directory(
                "uv-workspace/tests/_packages/partial-workspace"
            ),
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
        fail_fast: Annotated[
            bool, Doc("Raise as soon as any check fails instead of waiting for all.")
        ] = False,
    ) -> str:
        """Block until every Dagger check has landed as a successful GitHub commit
        status on `ref`."""
        return await dag.github_status_monitor().wait_for_dagger_checks(
            repo=repo, ref=ref, token=token, fail_fast=fail_fast
        )

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
        ctr = await self._workspace().build(
            all_packages=True, all_extras=True, all_groups=True
        )
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
    async def github_status_monitor_pytest(self) -> str:
        """Run the github-status-monitor unit-test suite inside a freshly-
        built container."""
        ctr = await self._status_monitor().build(
            package="github-status-monitor", all_groups=True
        )
        workdir = await ctr.workdir()
        tests = self.source.directory("github-status-monitor").directory("tests")
        ctr = ctr.with_directory(f"{workdir}/tests", tests)
        return await ctr.with_exec(["pytest", "-q"]).stdout()

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
        await ctr.with_exec(["pytest", "-q"]).stdout()

    @function
    async def uv_workspace_build_partial_workspace(self) -> None:
        """Build a project whose uv.lock references local deps that don't exist in the source tree.

        The missing dep (ext-pkg at ../../gone/ext-pkg) should be silently
        skipped. The present dep (my-dep at ../my-dep) should be installed.
        """
        ctr = await self._partial_workspace().build(
            package="sub-project", group=["dev"]
        )
        script = _assert_modules_script(
            present=["my_dep"],
            absent=["ext_pkg"],
        )
        await ctr.with_exec(["python", "-c", script]).stdout()
