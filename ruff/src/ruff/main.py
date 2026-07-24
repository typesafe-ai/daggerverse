from __future__ import annotations

from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

from ruff.args import SourceDir
from ruff.checker import RuffChecker
from ruff.formatter import RuffFormatter
from ruff.utils import _DEFAULT_IMAGE, resolve_version


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
        source: SourceDir,
        ctr: Annotated[
            dagger.Container | None,
            Doc("Container with ruff installed. Defaults to the official ghcr.io/astral-sh/ruff image."),
        ] = None,
        version: Annotated[
            str | None,
            Doc("Ruff image tag. Only used when `ctr` is not provided. Overrides auto-detection from source."),
        ] = None,
    ) -> Ruff:
        if ctr is None:
            if version is None:
                version = await resolve_version(source)
            ctr = dag.container().from_(f"{_DEFAULT_IMAGE}:{version}")
        return cls(ctr=ctr)

    @function(cache="1h")
    async def version(self) -> str:
        """The resolved ruff version."""
        return (await self.ctr.with_exec(["/ruff", "version"]).stdout()).strip()

    @function
    def check(self) -> RuffChecker:
        """Return the ruff linter (`ruff check`)."""
        return RuffChecker(ctr=self.ctr)

    @function
    def format(self) -> RuffFormatter:
        """Return the ruff formatter (`ruff format`)."""
        return RuffFormatter(ctr=self.ctr)
