from typing import Annotated, TypeAlias

import dagger
from dagger import DefaultPath, Doc, Ignore

SourceDir = Annotated[
    dagger.Directory,
    Doc("Source directory."),
    DefaultPath("."),
    Ignore(
        [
            "**/__pycache__",
            "**/*.pyc",
            "**/node_modules",
            "**/.venv",
            "**/.tox",
            "**/.nox",
            "**/.git",
            "**/.mypy_cache",
            "**/.pytest_cache",
            "**/.ruff_cache",
            "**/.direnv",
            "**/.devenv",
            "**/dist",
            "**/build",
            "**/*.egg-info",
            "**/sdk",
        ]
    ),
]


WorkspacePath: TypeAlias = Annotated[
    str,
    Doc("Path to the workspace root (holding uv.lock and pyproject.toml) within the source directory."),
]

Package: TypeAlias = Annotated[
    list[str],
    Doc(
        "Package names to install; passed to `uv sync` as repeated `--package`. "
        "Defaults to the current (workspace root) package, mirroring a bare `uv sync`. "
        "Use `all_packages` to install every workspace member instead."
    ),
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

NoEditable: TypeAlias = Annotated[
    bool,
    Doc(
        "Install local/workspace packages non-editable (`uv sync --no-editable`), "
        "baking their source into site-packages instead of linking it. Makes the "
        "resulting venv self-contained, so it can be exported/copied (e.g. via "
        "`copy_venv`) without also carrying the workspace source."
    ),
]
