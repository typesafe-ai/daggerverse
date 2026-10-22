from typing import Annotated

from dagger import Doc, field, function, object_type
from dagger_clients.core import Container, Workspace

from ruff.args import ruff_container
from ruff.checker import RuffChecker
from ruff.formatter import RuffFormatter


@object_type
class Ruff:
    """Ruff Python linter and formatter.

    Runs on the Dagger workspace root. To run on a directory instead, pass
    `directory.as_workspace()`.
    """

    ctr: Annotated[
        Container | None,
        Doc("Container with ruff installed. Defaults to the official ghcr.io/astral-sh/ruff image."),
    ] = field(default=None)

    version: Annotated[
        str | None,
        Doc("Ruff image tag. Only used when `ctr` is not provided. Defaults to the version the workspace pins."),
    ] = field(default=None)

    @function(cache="1h")
    async def resolved_version(self, ws: Workspace) -> str:
        """The ruff version `check` and `format` run."""
        ctr = await ruff_container(ws, self.ctr, self.version)
        return (await ctr.with_exec(["/ruff", "version"]).stdout()).strip()

    @function
    def check(self) -> RuffChecker:
        """Return the ruff linter (`ruff check`)."""
        return RuffChecker(ctr=self.ctr, version=self.version)

    @function
    def format(self) -> RuffFormatter:
        """Return the ruff formatter (`ruff format`)."""
        return RuffFormatter(ctr=self.ctr, version=self.version)
