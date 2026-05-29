"""Pure helpers for the uv module (no Dagger runtime required)."""

import posixpath
import re
import tomllib
from pathlib import PurePosixPath

from packaging.specifiers import SpecifierSet
from packaging.version import Version

_DEFAULT_IMAGE = "ghcr.io/astral-sh/uv"
_DEFAULT_VERSION = "latest"

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
    stripped = value.strip()
    if stripped.startswith("=="):
        return "*" not in stripped
    return not _VERSION_SPECIFIER_RE.search(stripped)


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


def _pad_release(version: Version) -> Version:
    """Pad a bare ``X`` or ``X.Y`` release to a concrete ``X.Y.Z`` image tag.

    Trailing zeros don't change PEP 440 ordering (``0.5`` == ``0.5.0``), so this
    keeps specifier satisfaction intact while producing a tag that actually
    exists in the registry (uv publishes three-component tags).
    """
    if version.epoch or version.pre or version.post or version.dev or version.local:
        return version
    release = version.release
    if len(release) >= 3:
        return version
    padded = (*release, *([0] * (3 - len(release))))
    return Version(".".join(str(n) for n in padded))


def minimal_compatible_version(specifier: str) -> str | None:
    """Lowest version satisfying a PEP 440 specifier, computed without PyPI.

    Derives candidate lower bounds straight from the specifier's operands
    (``>=``, ``>``, ``~=``, ``==``) and returns the smallest one the whole set
    accepts. Returns ``None`` when the specifier has no lower bound (e.g. only
    ``<``/``<=``/``!=`` constraints), leaving the caller to pick a default.
    """
    spec = SpecifierSet(specifier)
    candidates: list[Version] = []
    for clause in spec:
        if clause.operator in (">=", "~=", "=="):
            base = clause.version[:-2] if clause.version.endswith(".*") else clause.version
            candidates.append(_pad_release(Version(base)))
        elif clause.operator == ">":
            release = _pad_release(Version(clause.version)).release
            candidates.append(Version(".".join(str(n) for n in (*release[:-1], release[-1] + 1))))
    feasible = sorted(c for c in candidates if c in spec)
    return str(feasible[0]) if feasible else None


def resolve_specifier(value: str) -> str:
    """Concrete image tag for a configured version value (exact pin or range).

    Exact pins are used verbatim; ranges resolve to their minimal compatible
    version. Falls back to the default tag when a range has no lower bound.
    """
    if is_exact_version(value):
        return normalize_exact_version(value)
    return minimal_compatible_version(value) or _DEFAULT_VERSION
