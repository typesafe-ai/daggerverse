from typing import Annotated

from dagger import DefaultPath, Doc, Ignore
from dagger_clients.core import Directory

SourceDir = Annotated[
    Directory,
    Doc("Project source directory."),
    DefaultPath("/"),
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
