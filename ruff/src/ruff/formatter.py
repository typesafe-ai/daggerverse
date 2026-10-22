import sys
from typing import Annotated

from dagger import Doc, check, field, function, object_type
from dagger_clients.core import Changeset, Container, Directory, ReturnType, Workspace, core

from ruff.args import ruff_container, source


@object_type
class RuffFormatter:
    """Ruff formatter (`ruff format`)."""

    ctr: Annotated[
        Container | None,
        Doc("Container with ruff installed. Defaults to the ruff image at `version`."),
    ] = field(default=None)

    version: Annotated[
        str | None,
        Doc("Ruff image tag. Defaults to the version the workspace pins."),
    ] = field(default=None)

    async def _container(self, ws: Workspace, source: Directory) -> Container:
        ctr = await ruff_container(ws, self.ctr, self.version)
        return ctr.with_workdir("/work").with_mounted_directory("/work", source)

    @check
    @function
    async def lint(
        self,
        ws: Workspace,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to `ruff format --check`."),
        ] = None,
    ) -> None:
        """Run `ruff format --check` and report unformatted files.

        Exits non-zero when files would be reformatted.
        """
        args = ["/ruff", "format", "--check"]
        if extra_args:
            args.extend(extra_args)
        args.append(".")
        await (await self._container(ws, source(ws))).with_exec(args).sync()

    @function
    async def fix(
        self,
        ws: Workspace,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to `ruff format`."),
        ] = None,
    ) -> Changeset:
        """Auto-format source files and return a Changeset."""
        args = ["/ruff", "format"]
        if extra_args:
            args.extend(extra_args)
        args.append(".")
        before_dir = source(ws)
        ctr = await self._container(ws, before_dir)
        result = ctr.with_exec(args, expect=ReturnType.ANY)
        output = await result.combined_output()
        if output.strip():
            sys.stderr.write(output)
        exit_code = await result.exit_code()
        if exit_code not in (0, 1):
            raise RuntimeError(
                f"`ruff format` failed with exit code {exit_code}: "
                "ruff reported an error (configuration, usage, or runtime). "
                "See stderr above for ruff's output."
            )
        before = core().directory().with_directory(".", before_dir)
        fixed = result.directory("/work").without_directory(".ruff_cache")
        after = core().directory().with_directory(".", fixed)
        return after.changes(before)
