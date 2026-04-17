"""Checks for typesafe-ai daggerverse modules."""

from typing import Annotated

import dagger
from dagger import DefaultPath, Doc, check, dag, field, function, object_type


def _base() -> dagger.Container:
    return (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.14-bookworm")
        .with_workdir("/workspace")
    )


@object_type
class TypesafeDaggerverse:
    """CI checks for modules published from this repo."""

    source: Annotated[
        dagger.Directory,
        Doc("Daggerverse repo root"),
        DefaultPath("."),
    ] = field()

    @check
    @function
    async def uv_workspace_build_workspace(self) -> str:
        """Build the uv-workspace multi-package fixture and verify every local package imports."""
        ctr = await dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace"),
            base_container=_base(),
        ).build(package="my-app")
        return await ctr.with_exec(
            ["python", "-c", "import my_app, my_lib, my_core"]
        ).stdout()

    @check
    @function
    async def uv_workspace_build_full_workspace(self) -> str:
        """Build the full uv-workspace (no package filter) and verify every local package imports."""
        ctr = await dag.uv_workspace(
            source_dir=self.source.directory("uv-workspace/tests/_packages/workspace"),
            base_container=_base(),
        ).build()
        return await ctr.with_exec(
            ["python", "-c", "import my_app, my_lib, my_core"]
        ).stdout()

    @check
    @function
    async def uv_workspace_build_standalone(self) -> str:
        """Build the uv-workspace standalone fixture and verify it imports."""
        ctr = await dag.uv_workspace(
            source_dir=self.source.directory(
                "uv-workspace/tests/_packages/standalone-app"
            ),
            base_container=_base(),
        ).build(package="standalone-app")
        return await ctr.with_exec(["python", "-c", "import standalone_app"]).stdout()
