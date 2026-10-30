from typing import Annotated, Self

from dagger import Doc, field, function, generate, object_type
from dagger_clients.core import Changeset, Container, Directory, Workspace, core

from ruff.args import SOURCE_EXCLUDE, VERSION_FILES, Exclude
from ruff.utils import _DEFAULT_IMAGE, resolve_version


@object_type
class Ruff:
    """Ruff Python linter and formatter.

    `check` and `format` are generators: `dagger generate` applies their fixes,
    and `dagger check --generated` fails while either would change a file. `check` also
    fails on violations ruff can't fix.
    """

    ctr: Annotated[
        Container,
        Doc("Container with ruff installed."),
    ] = field()

    source: Annotated[
        Directory,
        Doc("Source tree: the Dagger workspace, less `exclude` and gitignored files."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        ctr: Annotated[
            Container | None,
            Doc("Container with ruff installed. Defaults to the official ghcr.io/astral-sh/ruff image."),
        ] = None,
        version: Annotated[
            str | None,
            Doc("Ruff image tag. Only used when `ctr` is not provided. Defaults to the version the workspace pins."),
        ] = None,
        exclude: Exclude = SOURCE_EXCLUDE,
    ) -> Self:
        if ctr is None:
            if version is None:
                version = await resolve_version(ws.directory("/", include=VERSION_FILES, gitignore=True))
            ctr = core().container().from_(f"{_DEFAULT_IMAGE}:{version}")
        # The workspace gets a new cache key on every run, and so does everything
        # loaded from it. Re-loading the source as a workspace of its own keys it
        # by its contents, so ruff reruns only when a file changes.
        source = ws.directory("/", exclude=exclude, gitignore=True).as_workspace().directory("/")
        return cls(ctr=ctr, source=source)

    @function(cache="1h")
    async def version(self) -> str:
        """The resolved ruff version."""
        return (await self.ctr.with_exec(["/ruff", "version"]).stdout()).strip()

    def _fix(self, args: list[str]) -> Changeset:
        """The edits `ruff <args> .` makes to the source."""
        fixed = (
            self.ctr.with_workdir("/work")
            .with_mounted_directory("/work", self.source)
            .with_exec(["/ruff", *args, "--no-cache", "."])
            .directory("/work")
        )
        before = core().directory().with_directory(".", self.source)
        after = core().directory().with_directory(".", fixed)
        return after.changes(before)

    @generate
    @function
    def check(self) -> Changeset:
        """Fix lint violations with `ruff check --fix`.

        Fails when violations ruff can't fix remain.
        """
        return self._fix(["check", "--fix"])

    @generate
    @function
    def format(self) -> Changeset:
        """Format source files with `ruff format`."""
        return self._fix(["format"])
