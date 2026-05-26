import sys
from typing import Annotated

import dagger
from dagger import Doc, check, dag, field, function, object_type

from ruff.args import SourceDir


@object_type
class RuffChecker:
    """Ruff linter (``ruff check``)."""

    ctr: Annotated[
        dagger.Container,
        Doc("Container with ruff installed."),
    ] = field()

    def _container(self, source: dagger.Directory) -> dagger.Container:
        return self.ctr.with_workdir("/work").with_mounted_directory("/work", source)

    @check
    @function
    async def lint(
        self,
        source: SourceDir,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to ``ruff check``."),
        ] = None,
    ) -> str:
        """Run ``ruff check`` and report violations.

        Exits non-zero when violations are found.
        """
        args = ["/ruff", "check", "--no-fix"]
        if extra_args:
            args.extend(extra_args)
        args.append(".")
        return await self._container(source).with_exec(args).combined_output()

    @function
    async def fix(
        self,
        source: SourceDir,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to ``ruff check --fix``."),
        ] = None,
    ) -> dagger.Changeset:
        """Auto-fix lint violations and return a Changeset."""
        args = ["/ruff", "check", "--fix"]
        if extra_args:
            args.extend(extra_args)
        args.append(".")
        ctr = self._container(source)
        result = ctr.with_exec(args, expect=dagger.ReturnType.ANY)
        output = await result.combined_output()
        if output.strip():
            sys.stderr.write(output)
        before = dag.directory().with_directory(".", source)
        fixed = result.directory("/work").without_directory(".ruff_cache")
        after = dag.directory().with_directory(".", fixed)
        return after.changes(before)
