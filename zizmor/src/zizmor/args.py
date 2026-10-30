from typing import Annotated, TypeAlias

from dagger import Doc

# Nothing beyond gitignored files by default.
SOURCE_EXCLUDE: list[str] = []

Exclude: TypeAlias = Annotated[
    list[str],
    Doc("Glob patterns of workspace paths that are not uploaded to the Dagger engine."),
]
