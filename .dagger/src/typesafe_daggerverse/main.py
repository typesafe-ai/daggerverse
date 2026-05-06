"""Checks for typesafe-ai daggerverse modules."""

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
    async def uv_workspace_build_workspace(self) -> str:
        """Build the uv-workspace multi-package fixture and verify every local package imports."""
        ctr = await self._workspace().build(package="my-app")
        return await ctr.with_exec(
            ["python", "-c", "import my_app, my_lib, my_core"]
        ).stdout()

    @check
    @function
    async def uv_workspace_build_full_workspace(self) -> str:
        """Build the full uv-workspace (no package filter) and verify every local package imports."""
        ctr = await self._workspace().build(all_packages=True)
        return await ctr.with_exec(
            ["python", "-c", "import my_app, my_lib, my_core"]
        ).stdout()

    @check
    @function
    async def uv_workspace_build_workspace_app(self) -> str:
        """Build a workspace where the target package is a flat app (no build-system)."""
        ctr = await self._workspace_app().build(package="my-app")
        return await ctr.with_exec(["python", "-c", "import my_lib, my_core"]).stdout()

    @check
    @function
    async def uv_workspace_build_standalone(self) -> str:
        """Build the uv-workspace standalone fixture and verify it imports."""
        ctr = await self._standalone().build(package="standalone-app")
        return await ctr.with_exec(["python", "-c", "import standalone_app"]).stdout()

    @check
    @function
    async def uv_workspace_standalone_selective_extra(self) -> str:
        """`extra=['viz']` installs only the viz extra's deps, not other extras or groups."""
        ctr = await self._standalone().build(package="standalone-app", extra=["viz"])
        script = _assert_modules_script(
            present=["tabulate"],
            absent=["idna", "six", "packaging"],
        )
        return await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def uv_workspace_standalone_all_extras(self) -> str:
        """`all_extras=True` installs every extra but no groups."""
        ctr = await self._standalone().build(package="standalone-app", all_extras=True)
        script = _assert_modules_script(
            present=["tabulate", "idna"],
            absent=["six", "packaging"],
        )
        return await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def uv_workspace_standalone_selective_group(self) -> str:
        """`group=['docs']` installs only the docs group's deps, not other groups or extras."""
        ctr = await self._standalone().build(package="standalone-app", group=["docs"])
        script = _assert_modules_script(
            present=["six"],
            absent=["packaging", "tabulate", "idna"],
        )
        return await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def uv_workspace_standalone_all_groups(self) -> str:
        """`all_groups=True` installs every group but no extras."""
        ctr = await self._standalone().build(package="standalone-app", all_groups=True)
        script = _assert_modules_script(
            present=["six", "packaging"],
            absent=["tabulate", "idna"],
        )
        return await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def uv_workspace_full_workspace_all_extras_all_groups(self) -> str:
        """Full workspace build with all extras (from my-app) and all groups (from root) installed."""
        ctr = await self._workspace().build(
            all_packages=True, all_extras=True, all_groups=True
        )
        script = _assert_modules_script(
            present=["tabulate", "idna", "six", "packaging"],
            absent=[],
        )
        return await ctr.with_exec(["python", "-c", script]).stdout()

    @check
    @function
    async def uv_workspace_build_self(self) -> str:
        """Build uv-workspace from its own source on a fresh tree."""
        ctr = await self._self().build(package="uv-workspace")
        return await ctr.with_exec(["python", "-c", "import uv_workspace"]).stdout()

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
    async def uv_workspace_pytest_self(self) -> str:
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
        return await ctr.with_exec(["pytest", "-q"]).stdout()
