"""Pure helpers for `UvWorkspaceSource.audit`: reading `uv audit` reports and
writing the lock upgrades and cooldown exemptions that fix them."""

import enum
import json
import math
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypeAlias

import tomlkit
from dagger import Doc, enum_type
from packaging.version import InvalidVersion, Version

from uv.utils import normalize_package_name


@enum_type
class UpdateStrategy(enum.Enum):
    """How far `audit` upgrades a vulnerable package."""

    LATEST = "latest"
    """The newest version the workspace's constraints (and cooldown) allow."""

    MINIMAL = "minimal"
    """The lowest version that fixes every advisory against the package."""


@enum_type
class CooldownPolicy(enum.Enum):
    """What `audit` does when the workspace's cooldown (`exclude-newer`) holds back a fix."""

    RESPECT = "respect"
    """Leave the cooldown alone: a fix newer than the cutoff stays unapplied and the audit fails."""

    EXEMPT = "exempt"
    """Exempt just the fix: pin the package's `exclude-newer-package` cutoff to the fixing
    version's upload time. The exemption is removed once the cooldown catches up with it."""


AuditStrategy: TypeAlias = Annotated[
    UpdateStrategy,
    Doc("How far `audit` upgrades a vulnerable package: as far as `LATEST` allowed, or to the `MINIMAL` fix."),
]

AuditCooldown: TypeAlias = Annotated[
    CooldownPolicy,
    Doc(
        "What `audit` does when the cooldown (`exclude-newer`) holds back a fix: `RESPECT` it, or "
        "`EXEMPT` the fixing version in `exclude-newer-package` until the cooldown catches up."
    ),
]

# Marks the `exclude-newer-package` entries `audit` writes, so it only ever prunes its own.
EXEMPTION_MARKER = "uv audit: security fix exempt from cooldown"


@dataclass(frozen=True)
class Vulnerability:
    id: str
    package: str
    version: str
    fix_versions: list[str]


def parse_report(text: str) -> list[Vulnerability]:
    """The vulnerabilities in `uv audit --output-format json` output."""
    return [
        Vulnerability(
            id=v["id"],
            package=normalize_package_name(v["dependency"]["name"]),
            version=v["dependency"]["version"],
            fix_versions=v.get("fix_versions") or [],
        )
        for v in json.loads(text)["vulnerabilities"]
    ]


def by_package(vulns: list[Vulnerability]) -> dict[str, list[Vulnerability]]:
    grouped: dict[str, list[Vulnerability]] = {}
    for v in vulns:
        grouped.setdefault(v.package, []).append(v)
    return grouped


def _versions(values: list[str]) -> list[Version]:
    parsed = []
    for value in values:
        try:
            parsed.append(Version(value))
        except InvalidVersion:
            continue
    return sorted(parsed)


def minimal_fix(vulns: list[Vulnerability]) -> str | None:
    """The lowest version above the installed one that fixes every advisory in `vulns` (one package).

    Each advisory is fixed by its lowest fix version above the installed one; the package
    needs the highest of those. `None` when some advisory has no such fix.
    """
    current = Version(vulns[0].version)
    needed = []
    for v in vulns:
        above = [f for f in _versions(v.fix_versions) if f > current]
        if not above:
            return None
        needed.append(above[0])
    return str(max(needed))


def upgrade_spec(package: str, target: str, strategy: UpdateStrategy) -> str:
    """The `uv lock --upgrade-package` value upgrading `package` past `target` (its minimal fix)."""
    return package if strategy is UpdateStrategy.LATEST else f"{package}=={target}"


def upload_cutoff(lock: str, package: str, version: str) -> str:
    """The earliest `exclude-newer` cutoff that admits `package==version`: its latest file
    upload time in `uv.lock`, rounded up to the second."""
    for pkg in tomllib.loads(lock).get("package", []):
        if normalize_package_name(pkg["name"]) == package and pkg.get("version") == version:
            files = [pkg["sdist"]] if "sdist" in pkg else []
            files += pkg.get("wheels", [])
            times = [_parse_time(f["upload-time"]) for f in files if "upload-time" in f]
            if times:
                latest = max(times)
                return _format_time(datetime.fromtimestamp(math.ceil(latest.timestamp()), UTC))
    msg = f"uv.lock has no upload time for {package}=={version}"
    raise ValueError(msg)


def locked_version(lock: str, package: str) -> str | None:
    for pkg in tomllib.loads(lock).get("package", []):
        if normalize_package_name(pkg["name"]) == package:
            return pkg.get("version")
    return None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_SPAN = re.compile(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")


def _parse_span(value: str) -> timedelta:
    """An ISO 8601 duration as uv writes `exclude-newer-span` (no months or years)."""
    match = _SPAN.fullmatch(value)
    if match is None:
        msg = f"unsupported exclude-newer span {value!r}"
        raise ValueError(msg)
    weeks, days, hours, minutes, seconds = (float(g) if g else 0 for g in match.groups())
    return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def cooldown_cutoff(lock: str, now: datetime) -> datetime | None:
    """The global `exclude-newer` cutoff `uv.lock` was resolved with, as of `now`.

    uv records a relative cooldown as `exclude-newer-span` (with a placeholder
    `exclude-newer`), and an absolute one as `exclude-newer`. `None` without a cooldown.
    """
    options = tomllib.loads(lock).get("options", {})
    if "exclude-newer-span" in options:
        return now - _parse_span(options["exclude-newer-span"])
    if "exclude-newer" in options:
        return _parse_time(options["exclude-newer"])
    return None


def _exemptions_table(doc: tomlkit.TOMLDocument, uv_toml: bool, create: bool):
    """The uv settings table of a `uv.toml` or `pyproject.toml` and its `exclude-newer-package`
    table, as a standard table (an inline one is converted: only those carry comments)."""
    parent = doc
    if not uv_toml:
        for key in ("tool", "uv"):
            if key not in parent:
                if not create:
                    return parent, None
                parent[key] = tomlkit.table(is_super_table=key == "tool")
            parent = parent[key]
    current = parent.get("exclude-newer-package")
    if current is None:
        if not create:
            return parent, None
        parent["exclude-newer-package"] = tomlkit.table()
    elif isinstance(current, tomlkit.items.InlineTable):
        table = tomlkit.table()
        for key, value in current.items():
            table[key] = value
        parent["exclude-newer-package"] = table
    return parent, parent["exclude-newer-package"]


def _is_ours(item) -> bool:
    # tomlkit hands booleans back as plain `bool`s, with no trivia to carry a comment.
    trivia = getattr(item, "trivia", None)
    return trivia is not None and EXEMPTION_MARKER in (trivia.comment or "")


def add_exemptions(config: str, exemptions: dict[str, str], *, uv_toml: bool) -> str:
    """`config` with each `package = cutoff` exemption written to `exclude-newer-package`."""
    doc = tomlkit.parse(config)
    _, table = _exemptions_table(doc, uv_toml, create=True)
    for package, cutoff in sorted(exemptions.items()):
        table[package] = cutoff
        table[package].comment(EXEMPTION_MARKER)
    return tomlkit.dumps(doc)


def has_user_exemption(config: str, package: str, *, uv_toml: bool) -> bool:
    """Whether `config` sets its own `exclude-newer-package` entry for `package`, which `audit` leaves alone."""
    _, table = _exemptions_table(tomlkit.parse(config), uv_toml, create=False)
    return table is not None and any(
        normalize_package_name(key) == package and not _is_ours(item) for key, item in table.items()
    )


def prune_exemptions(config: str, cutoff: datetime | None, *, uv_toml: bool) -> str:
    """`config` without the exemptions `audit` wrote that the global cooldown has caught up with.

    Such an entry would otherwise hold its package at that date for good. Without a
    global cooldown (`cutoff` is `None`) every one of them is moot.
    """
    doc = tomlkit.parse(config)
    parent, table = _exemptions_table(doc, uv_toml, create=False)
    if table is None:
        return config
    expired = [
        key for key, item in table.items() if _is_ours(item) and (cutoff is None or _parse_time(str(item)) <= cutoff)
    ]
    if not expired:
        return config
    for key in expired:
        del table[key]
    if not table:
        del parent["exclude-newer-package"]
    # tomlkit keeps the blank line that separated a removed trailing table.
    return tomlkit.dumps(doc).rstrip("\n") + "\n"
