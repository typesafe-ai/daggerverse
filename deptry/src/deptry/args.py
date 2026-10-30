from typing import Annotated, TypeAlias

from dagger import Doc

Exclude: TypeAlias = Annotated[
    list[str] | None,
    Doc("Glob patterns of project paths to leave out of projects, e.g. **/tests/_packages/**."),
]
