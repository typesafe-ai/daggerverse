"""Pure helpers for the uv module (no Dagger runtime required)."""

import json
import posixpath
import re
import tomllib
from pathlib import PurePosixPath

from packaging.specifiers import SpecifierSet
from packaging.version import Version

_DEFAULT_IMAGE = "ghcr.io/astral-sh/uv"
_DEFAULT_VERSION = "latest"
_PYPI_URL = "https://pypi.org/pypi/uv/json"

_VERSION_SPECIFIER_RE = re.compile(r"[><=!~]")


def image_ref(version: str) -> str:
    """The uv image reference for a given tag/version."""
    return f"{_DEFAULT_IMAGE}:{version}"


def workspace_path(lockfile: str) -> str:
    """Source-relative directory holding the given ``uv.lock``."""
    return posixpath.dirname(lockfile) or "."


def pyproject_path(lockfile: str) -> str:
    """Path to the ``pyproject.toml`` sibling of the given ``uv.lock``."""
    return posixpath.join(posixpath.dirname(lockfile), "pyproject.toml")


def uv_toml_path(lockfile: str) -> str:
    """Path to the ``uv.toml`` sibling of the given ``uv.lock``."""
    return posixpath.join(posixpath.dirname(lockfile), "uv.toml")


def is_excluded(path: str, patterns: list[str]) -> bool:
    """Whether a workspace path matches any of the exclude glob patterns."""
    workspace = PurePosixPath(path)
    return any(workspace.full_match(pattern) for pattern in patterns)


def is_exact_version(value: str) -> bool:
    if value.startswith("=="):
        return True
    return not _VERSION_SPECIFIER_RE.search(value)


def normalize_exact_version(value: str) -> str:
    if value.startswith("=="):
        return value[2:].strip()
    return value.strip()


def parse_required_version_from_pyproject(content: str) -> str | None:
    """``[tool.uv].required-version`` from pyproject.toml contents, if present."""
    data = tomllib.loads(content)
    return data.get("tool", {}).get("uv", {}).get("required-version")


def parse_required_version_from_uv_toml(content: str) -> str | None:
    """Top-level ``required-version`` from uv.toml contents, if present."""
    data = tomllib.loads(content)
    return data.get("required-version")


def resolve_from_pypi_data(pypi_json: str, specifier: str) -> str:
    data = json.loads(pypi_json)
    spec = SpecifierSet(specifier)
    matching = [
        v
        for v, files in data["releases"].items()
        if Version(v) in spec and (not files or not files[0].get("yanked", False))
    ]
    if not matching:
        msg = f"No uv versions on PyPI match {specifier}"
        raise ValueError(msg)
    return str(max(matching, key=Version))
