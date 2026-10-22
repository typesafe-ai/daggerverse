from dagger_clients.core import Container, Directory, Workspace, core

from ruff.utils import _DEFAULT_IMAGE, resolve_version

# Never linted or formatted: caches, virtualenvs, build output and generated
# Dagger code.
SOURCE_EXCLUDE = [
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

# The files that can pin the ruff version (see `resolve_version`).
VERSION_FILES = ["uv.lock", "ruff.toml", ".ruff.toml", "pyproject.toml"]


def source(ws: Workspace) -> Directory:
    """The workspace root ruff runs on."""
    return ws.directory("/", exclude=SOURCE_EXCLUDE, gitignore=True)


async def ruff_container(ws: Workspace, ctr: Container | None, version: str | None) -> Container:
    """`ctr` when set, otherwise the ruff image at `version` or the version the workspace pins."""
    if ctr is not None:
        return ctr
    if version is None:
        version = await resolve_version(ws.directory("/", include=VERSION_FILES))
    return core().container().from_(f"{_DEFAULT_IMAGE}:{version}")
