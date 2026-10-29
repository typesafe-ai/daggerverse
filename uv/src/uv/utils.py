"""Pure helpers for the uv module (no Dagger runtime required)."""

import re
import tomllib
from collections import OrderedDict, deque
from collections.abc import Collection
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

_UV_CACHE_DIR = "/mnt/cache/uv"

_VERSION_SPECIFIER_RE = re.compile(r"[><=!~]")


def pinned_image(name: str) -> str:
    """The image reference in this package's `images/<name>/Dockerfile`, which Dependabot keeps current."""
    dockerfile = Path(__file__).resolve().parent / "images" / name / "Dockerfile"
    for line in dockerfile.read_text().splitlines():
        if line.startswith("FROM "):
            return line.split()[1]
    msg = f"no FROM line in {dockerfile}"
    raise ValueError(msg)


# The uv image and version used when a workspace doesn't declare
# `required-version`, so audits and builds run on a pinned image.
_DEFAULT_IMAGE, _DEFAULT_VERSION = pinned_image("uv").rsplit(":", 1)


def image_ref(version: str) -> str:
    """The (distroless) uv image reference for a given tag/version.

    `FROM scratch` with only the uv binary. Enough to run `uv audit`, but not
    `uv sync`: with no libc/OS layer, uv can't provision a managed Python.
    """
    return f"{_DEFAULT_IMAGE}:{version}"


def debian_image_ref(version: str) -> str:
    """A Debian-based uv image reference for a given tag/version.

    Unlike the distroless `image_ref`, this variant ships a libc/OS layer and
    ca-certificates, so `uv` can download a managed Python at sync time. Used as
    the default build base.

    Maps to the `:<version>-debian` tag. `astral-sh/uv` publishes `-debian` for
    both old and current uv releases (the `-bookworm` variant was dropped for
    recent versions).
    """
    return f"{_DEFAULT_IMAGE}:{version}-debian"


def is_exact_version(value: str) -> bool:
    stripped = value.strip()
    if stripped.startswith("=="):
        return "*" not in stripped
    return not _VERSION_SPECIFIER_RE.search(stripped)


def normalize_exact_version(value: str) -> str:
    if value.startswith("=="):
        return value[2:].strip()
    return value.strip()


def parse_pyvenv_cfg(content: str) -> dict[str, str]:
    """Parse a venv's `pyvenv.cfg` (simple `key = value` lines) into a dict.

    Used by `UvVenv.create` to read the base interpreter (`home`) and the
    `relocatable` flag straight from the venv uv created.
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result


def extract_indices(data: dict, *, uv_toml: bool = False) -> list[dict]:
    """Extract index entries from parsed TOML data.

    Reads `[[index]]` (uv.toml) or `[[tool.uv.index]]` (pyproject.toml).
    Returns dicts with all recognised index fields for each entry that
    declares at least a `name` and `url`.
    """
    raw = data.get("index", []) if uv_toml else data.get("tool", {}).get("uv", {}).get("index", [])
    results: list[dict] = []
    for idx in raw:
        name = idx.get("name")
        url = idx.get("url")
        if name and url:
            results.append(
                {
                    "name": name,
                    "url": url,
                    "publish_url": idx.get("publish-url"),
                    "default": idx.get("default", False),
                    "explicit": idx.get("explicit", False),
                    "authenticate": idx.get("authenticate"),
                    "format": idx.get("format"),
                }
            )
    results.sort(key=lambda e: e["name"] or "")
    return results


def parse_indices(content: str, *, uv_toml: bool = False) -> list[dict]:
    return extract_indices(tomllib.loads(content), uv_toml=uv_toml)


def parse_project_name(content: str) -> str | None:
    """The `[project].name` declared in pyproject.toml, if any.

    Used to mimic `uv sync`'s default of operating on the current package when
    no explicit package is requested.
    """
    data = tomllib.loads(content)
    return data.get("project", {}).get("name")


def selected_groups(content: str, groups: list[str], all_groups: bool, no_default_groups: bool) -> set[str] | None:
    """The dependency groups `uv sync` installs, or None for all of them.

    `content` is the workspace root's pyproject.toml, whose `[tool.uv]
    default-groups` (`["dev"]` unless set, or `"all"`) is added unless
    `no_default_groups` is set.
    """
    if all_groups:
        return None
    default = tomllib.loads(content).get("tool", {}).get("uv", {}).get("default-groups", ["dev"])
    if no_default_groups:
        default = []
    elif default == "all":
        return None
    return set(groups) | set(default)


def python_request(python_version: str | None, pyproject: str) -> str | None:
    """The Python to provision: the `.python-version` pin, else `[project].requires-python`.

    Passed to `uv venv --python` and `uv python install` before the workspace's
    `pyproject.toml` is in the container, so they pick what `uv sync` would.
    """
    if python_version:
        return python_version
    return tomllib.loads(pyproject).get("project", {}).get("requires-python")


def parse_required_version_from_pyproject(content: str) -> str | None:
    data = tomllib.loads(content)
    return data.get("tool", {}).get("uv", {}).get("required-version")


def parse_required_version_from_uv_toml(content: str) -> str | None:
    data = tomllib.loads(content)
    return data.get("required-version")


def _pad_release(version: Version) -> Version:
    """Pad a bare `X` or `X.Y` release to a concrete `X.Y.Z` image tag.

    Trailing zeros don't change PEP 440 ordering (`0.5` == `0.5.0`), so this
    keeps specifier satisfaction intact while producing a tag that
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
    (`>=`, `>`, `~=`, `==`) and returns the smallest one the whole set
    accepts. Returns `None` when the specifier has no lower bound (e.g. only
    `<`/`<=`/`!=` constraints), leaving the caller to pick a default.
    """
    spec = SpecifierSet(specifier)
    candidates: list[Version] = []
    for clause in spec:
        if clause.operator in (">=", "~=", "=="):
            base = clause.version.removesuffix(".*")
            candidates.append(_pad_release(Version(base)))
        elif clause.operator == ">":
            release = _pad_release(Version(clause.version)).release
            candidates.append(Version(".".join(str(n) for n in (*release[:-1], release[-1] + 1))))
    feasible = sorted(c for c in candidates if c in spec)
    return str(feasible[0]) if feasible else None


def format_audit_failure(exit_code: int, stdout: str, stderr: str, workspace: str | None = None) -> str:
    """Human-readable message for a failed `uv audit` exec.

    `uv audit` writes its vulnerability report to stdout/stderr, but the
    :class:`dagger.ExecError` it raises stringifies to only a terse
    "exit code N" message. Folding the captured output into the message keeps
    the report in the trace/span error instead of leaving it solely in Dagger's
    stderr logs. Both streams are included (deduplicated) since uv's findings
    may land on either depending on version.

    `workspace` (a source-relative path) is named in the summary when given,
    so an aggregated/standalone error identifies which workspace failed.
    """
    seen: list[str] = []
    for stream in (stdout, stderr):
        text = (stream or "").strip()
        if text and text not in seen:
            seen.append(text)
    target = f" for {workspace}" if workspace else ""
    summary = f"uv audit failed{target} (exit code {exit_code})"
    detail = "\n".join(seen)
    return f"{summary}:\n\n{detail}" if detail else summary


def resolve_specifier(value: str) -> str:
    """Concrete image tag for a configured version value (exact pin or range).

    Exact pins are used verbatim; ranges resolve to their minimal compatible
    version. Falls back to the default tag when a range has no lower bound.
    """
    if is_exact_version(value):
        return normalize_exact_version(value)
    return minimal_compatible_version(value) or _DEFAULT_VERSION


def normalize_package_name(name: str) -> str:
    """PEP 503 name normalization: lowercase, collapse [-_.] runs to a single hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def require_package_selection(packages: list[str], all_packages: bool, default_package: str | None) -> None:
    """Ensure there is a package to install, else raise.

    `uv sync` defaults to the current package; we mirror that via
    `default_package` (the workspace root's `[project].name`). A pure
    workspace root has no `[project].name`, so with no explicit selection
    there is nothing to install. Surface that as an error rather than silently
    installing every member.
    """
    if packages or all_packages or default_package:
        return
    msg = (
        "No package to install: this workspace's pyproject.toml declares no "
        "[project].name (a pure workspace root), so there is no current package "
        "to default to. Pass `package` to select one or more members, or set "
        "`all_packages` to install every workspace member."
    )
    raise ValueError(msg)


def parse_local_packages(lock_data: dict) -> OrderedDict[str, str]:
    """Return {package_name: local_path} for editable, directory, and virtual packages.

    Virtual members still need their metadata staged for workspace resolution.
    Results are sorted by package name for deterministic build order.
    """
    result = {}
    for pkg in lock_data.get("package", []):
        source = pkg.get("source", {})
        if "editable" in source:
            result[pkg["name"]] = source["editable"]
        elif "directory" in source:
            result[pkg["name"]] = source["directory"]
        elif "virtual" in source:
            result[pkg["name"]] = source["virtual"]
    return OrderedDict(sorted(result.items()))


def find_transitive_local_deps(
    lock_data: dict, project: str, groups: Collection[str] | None = None
) -> OrderedDict[str, str]:
    """Find all local packages that `project` transitively depends on (including itself).

    Results are sorted by package name for deterministic build order.
    The *project* name is PEP 503-normalised so callers can pass the raw
    `[project].name` from `pyproject.toml` (which may use underscores).

    Like `uv sync`, dependency groups apply only to `project` itself, never to
    its dependencies: `groups` selects which of its groups are followed (None
    follows all of them).

    Virtual roots and members participate in traversal just like installable
    packages, including dependencies reached through a virtual member.
    """
    locals_ = parse_local_packages(lock_data)
    project = normalize_package_name(project)

    dep_graph: dict[str, list[str]] = {}
    for pkg in lock_data.get("package", []):
        name = pkg["name"]
        deps = {d["name"] for d in pkg.get("dependencies", [])}
        if name == project:
            for group, group_deps in pkg.get("dev-dependencies", {}).items():
                if groups is None or group in groups:
                    deps |= {d["name"] for d in group_deps}
        dep_graph[name] = sorted(deps)

    needed: dict[str, str] = {}
    # Seed traversal from `project` even when it isn't local so we still walk
    # its dependencies. Only local packages are recorded in `needed` and traversed
    # (a third-party dep's transitive deps are remote, not workspace-local).
    visited = {project}
    queue: deque[str] = deque([project])
    if project in locals_:
        needed[project] = locals_[project]

    while queue:
        current = queue.popleft()
        for dep in dep_graph.get(current, []):
            if dep not in visited:
                visited.add(dep)
                if dep in locals_:
                    needed[dep] = locals_[dep]
                    queue.append(dep)

    return OrderedDict(sorted(needed.items()))


def build_uv_sync_args(
    *,
    packages: list[str],
    extras: list[str],
    groups: list[str],
    all_extras: bool,
    all_groups: bool,
    all_packages: bool,
    no_editable: bool = False,
    no_default_groups: bool = False,
) -> list[str]:
    args = ["uv", "sync", "--frozen", "--link-mode", "copy"]
    if no_editable:
        args.append("--no-editable")
    if no_default_groups:
        args.append("--no-default-groups")
    if all_extras:
        args.append("--all-extras")
    for extra in extras:
        args += ["--extra", extra]
    if all_groups:
        args.append("--all-groups")
    for group in groups:
        args += ["--group", group]
    if all_packages:
        args.append("--all-packages")
    for package in packages:
        args += ["--package", package]
    return args
