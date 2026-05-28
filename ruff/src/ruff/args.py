from typing import Annotated

import dagger
from dagger import DefaultPath, Doc, Ignore

SourceDir = Annotated[
    dagger.Directory,
    Doc("Project source directory."),
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
