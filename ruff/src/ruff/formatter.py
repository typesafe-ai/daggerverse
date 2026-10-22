import sys
from typing import Annotated

from dagger import Doc, check, field, function, object_type
from dagger_clients.core import Changeset, Container, Directory, ReturnType, core

from ruff.args import SourceDir


@object_type
class RuffFormatter:
    """Ruff formatter (`ruff format`)."""

    ctr: Annotated[
        Container,
        Doc("Container with ruff installed."),
    ] = field()

    def _container(self, source: Directory) -> Container:
        return self.ctr.with_workdir("/work").with_mounted_directory("/work", source)

    @check
    @function
    async def lint(
        self,
        source: SourceDir,
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
        await self._container(source).with_exec(args).sync()

    @function
    async def fix(
        self,
        source: SourceDir,
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
        ctr = self._container(source)
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
        before = core().directory().with_directory(".", source)
        fixed = result.directory("/work").without_directory(".ruff_cache")
        after = core().directory().with_directory(".", fixed)
        return after.changes(before)
