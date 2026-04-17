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
