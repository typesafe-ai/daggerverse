"""Reusable annotated type aliases for uv-workspace Dagger function parameters."""

from typing import Annotated, TypeAlias

from dagger import Doc

Package: TypeAlias = Annotated[
    str,
    Doc("Package name; if set, only that package's transitive local deps are installed. Maps to `uv sync --package`"),
]

Extra: TypeAlias = Annotated[
    list[str],
    Doc("Extras to install; passed to `uv sync` as repeated `--extra`"),
]

Group: TypeAlias = Annotated[
    list[str],
    Doc("Dependency groups to install; passed to `uv sync` as repeated `--group`"),
]

AllExtras: TypeAlias = Annotated[
    bool,
    Doc("Install every extra; maps to `uv sync --all-extras`"),
]

AllGroups: TypeAlias = Annotated[
    bool,
    Doc("Install every dependency group; maps to `uv sync --all-groups`"),
]

AllPackages: TypeAlias = Annotated[
    bool,
    Doc("Install every workspace member; maps to `uv sync --all-packages`"),
]

DaggerCodegen: TypeAlias = Annotated[
    bool,
    Doc(
        "If True (default), and the package being built has a "
        "`dagger.json`, run Dagger codegen and overlay the generated "
        "SDK before `uv sync`. No-op for non-Dagger projects."
    ),
]
