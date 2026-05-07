from collections import OrderedDict, deque


def parse_local_packages(lock_data: dict) -> OrderedDict[str, str]:
    """Return {package_name: local_path} for all local packages (editable or directory).

    Results are sorted by package name for deterministic build order.
    """
    result = {}
    for pkg in lock_data.get("package", []):
        source = pkg.get("source", {})
        if "editable" in source:
            result[pkg["name"]] = source["editable"]
        elif "directory" in source:
            result[pkg["name"]] = source["directory"]
    return OrderedDict(sorted(result.items()))


def find_transitive_local_deps(lock_data: dict, project: str) -> OrderedDict[str, str]:
    """Find all local packages that `project` transitively depends on (including itself).

    Results are sorted by package name for deterministic build order.
    """
    locals_ = parse_local_packages(lock_data)

    dep_graph: dict[str, list[str]] = {}
    for pkg in lock_data.get("package", []):
        name = pkg["name"]
        deps = {d["name"] for d in pkg.get("dependencies", [])}
        for group_deps in pkg.get("dev-dependencies", {}).values():
            deps |= {d["name"] for d in group_deps}
        dep_graph[name] = sorted(deps)

    needed: dict[str, str] = {}
    queue: deque[str] = deque()

    if project in locals_:
        needed[project] = locals_[project]
        queue.append(project)

    visited = {project}
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
    package: str | None,
    extras: list[str],
    groups: list[str],
    all_extras: bool,
    all_groups: bool,
    all_packages: bool,
) -> list[str]:
    """Build the `uv sync` base argv mirroring the `uv sync` CLI flags."""
    args = ["uv", "sync", "--frozen", "--link-mode", "copy"]
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
    if package:
        args += ["--package", package]
    return args
