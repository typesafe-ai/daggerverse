from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

from ruff.checker import RuffChecker
from ruff.formatter import RuffFormatter
from ruff.utils import _DEFAULT_IMAGE, _DEFAULT_VERSION, resolve_version


@object_type
class Ruff:
    """Ruff Python linter and formatter."""

    ctr: Annotated[
        dagger.Container,
        Doc("Container with ruff installed."),
    ] = field()

    @classmethod
    async def create(
        cls,
        source: Annotated[
            dagger.Directory | None,
            Doc(
                "Project source directory used to auto-detect the ruff version "
                "from uv.lock, ruff.toml, .ruff.toml, or pyproject.toml."
            ),
        ] = None,
        ctr: Annotated[
            dagger.Container | None,
            Doc("Container with ruff installed. Defaults to the official ghcr.io/astral-sh/ruff image."),
        ] = None,
        version: Annotated[
            str | None,
            Doc("Ruff image tag. Only used when ``ctr`` is not provided. Overrides auto-detection from source."),
        ] = None,
    ) -> "Ruff":
        if ctr is None:
            if version is None:
                if source is not None:
                    version = await resolve_version(source)
                else:
                    version = _DEFAULT_VERSION
            ctr = dag.container().from_(f"{_DEFAULT_IMAGE}:{version}")
        return cls(ctr=ctr)

    @function(cache="1h")
    async def version(self) -> str:
        """The resolved ruff version."""
        return (await self.ctr.with_exec(["/ruff", "version"]).stdout()).strip()

    @function
    def check(self) -> RuffChecker:
        """Return the ruff linter (``ruff check``)."""
        return RuffChecker(ctr=self.ctr)

    @function
    def format(self) -> RuffFormatter:
        """Return the ruff formatter (``ruff format``)."""
        return RuffFormatter(ctr=self.ctr)
