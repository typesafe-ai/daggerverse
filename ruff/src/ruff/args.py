from typing import Annotated, TypeAlias

from dagger import Doc

# Never uploaded. No `.gitignore` lists `.git`; caches, virtualenvs and build
# output are left to the gitignore filter.
SOURCE_EXCLUDE = ["**/.git"]

Exclude: TypeAlias = Annotated[
    list[str],
    Doc("Glob patterns of workspace paths that are not uploaded to the Dagger engine."),
]

# The files that can pin the ruff version (see `resolve_version`).
VERSION_FILES = ["uv.lock", "ruff.toml", ".ruff.toml", "pyproject.toml"]
