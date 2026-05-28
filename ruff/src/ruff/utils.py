import json
import re
import tomllib

import dagger
from dagger import dag
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_DEFAULT_VERSION = "0.15.14"
_DEFAULT_IMAGE = "ghcr.io/astral-sh/ruff"
_PYPI_URL = "https://pypi.org/pypi/ruff/json"

_VERSION_SPECIFIER_RE = re.compile(r"[><=!~]")


def is_exact_version(value: str) -> bool:
    if value.startswith("=="):
        return True
    return not _VERSION_SPECIFIER_RE.search(value)


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


def resolve_from_pypi_data(pypi_json: str, specifier: str) -> str:
    data = json.loads(pypi_json)
    spec = SpecifierSet(specifier)
    matching = [
        v
        for v, files in data["releases"].items()
        if Version(v) in spec and (not files or not files[0].get("yanked", False))
    ]
    if not matching:
        msg = f"No ruff versions on PyPI match {specifier}"
        raise ValueError(msg)
    return str(max(matching, key=Version))


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


async def resolve_version(source: dagger.Directory) -> str:
    for resolver in (
        _resolve_from_uv_lock,
        _resolve_from_ruff_toml,
        _resolve_from_pyproject,
    ):
        value = await resolver(source)
        if value is None:
            continue
        if is_exact_version(value):
            return normalize_exact_version(value)
        pypi_json = await dag.http(_PYPI_URL).contents()
        return resolve_from_pypi_data(pypi_json, value)
    return _DEFAULT_VERSION
