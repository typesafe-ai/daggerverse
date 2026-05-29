import re
import tomllib

import dagger
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_DEFAULT_VERSION = "latest"
_DEFAULT_IMAGE = "ghcr.io/astral-sh/ruff"

_VERSION_SPECIFIER_RE = re.compile(r"[><=!~]")


def is_exact_version(value: str) -> bool:
    stripped = value.strip()
    if stripped.startswith("=="):
        return "*" not in stripped
    return not _VERSION_SPECIFIER_RE.search(stripped)


def normalize_exact_version(value: str) -> str:
    if value.startswith("=="):
        return value[2:].strip()
    return value.strip()


def parse_version_from_uv_lock(content: str) -> str | None:
    data = tomllib.loads(content)
    for pkg in data.get("package", []):
        if pkg.get("name") == "ruff":
            return pkg["version"]
    return None


def parse_version_from_ruff_toml(content: str) -> str | None:
    data = tomllib.loads(content)
    return data.get("required-version")


def parse_version_from_pyproject(content: str) -> str | None:
    data = tomllib.loads(content)
    return data.get("tool", {}).get("ruff", {}).get("required-version")


def _pad_release(version: Version) -> Version:
    """Pad a bare ``X`` or ``X.Y`` release to a concrete ``X.Y.Z`` image tag.

    Trailing zeros don't change PEP 440 ordering (``0.5`` == ``0.5.0``), so this
    keeps specifier satisfaction intact while producing a tag that actually
    exists in the registry (ruff publishes three-component tags).
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


async def _resolve_from_uv_lock(source: dagger.Directory) -> str | None:
    if not await source.glob("uv.lock"):
        return None
    content = await source.file("uv.lock").contents()
    return parse_version_from_uv_lock(content)


async def _resolve_from_ruff_toml(source: dagger.Directory) -> str | None:
    for name in ("ruff.toml", ".ruff.toml"):
        if not await source.glob(name):
            continue
        content = await source.file(name).contents()
        result = parse_version_from_ruff_toml(content)
        if result is not None:
            return result
    return None


async def _resolve_from_pyproject(source: dagger.Directory) -> str | None:
    if not await source.glob("pyproject.toml"):
        return None
    content = await source.file("pyproject.toml").contents()
    return parse_version_from_pyproject(content)


def resolve_specifier(value: str) -> str:
    """Concrete image tag for a configured version value (exact pin or range).

    Exact pins are used verbatim; ranges resolve to their minimal compatible
    version. Falls back to the default tag when a range has no lower bound.
    """
    if is_exact_version(value):
        return normalize_exact_version(value)
    return minimal_compatible_version(value) or _DEFAULT_VERSION


async def resolve_version(source: dagger.Directory) -> str:
    for resolver in (
        _resolve_from_uv_lock,
        _resolve_from_ruff_toml,
        _resolve_from_pyproject,
    ):
        value = await resolver(source)
        if value is None:
            continue
        return resolve_specifier(value)
    return _DEFAULT_VERSION
